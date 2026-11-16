"""
Builds one web-ready GLB from a rigged character and separate animation files, for example a
Mixamo character exported "With Skin" plus animations exported "Without Skin".

Run with Blender 4.2 or newer (no UI needed):

    blender -b -P build_rigged.py -- --character <character.fbx|.glb> --out <output.glb>
        --clip idle=<anim_idle.fbx> --clip walk=<anim_walk.fbx> [--clip <name>=<file> ...]
        [--material-from <textured.glb>] [--triangles 18000] [--arm-space 12]

Then compress it for the web without simplifying again:

    node optimize.mjs <output.glb> <web.glb> --no-simplify --texture 512

What it does:
  1. Imports the character (FBX or glTF).
  2. Imports each animation file and retargets it onto the character as a clip with the given
     name. Retargeting matches every bone's world orientation frame by frame, so it works even
     when the animation files carry a different rest pose than the character (animation-only
     FBX files often do). Hip movement is rescaled to the character's size, which also covers
     a character exported in different units.
  3. Optionally swings the upper arms outward by --arm-space degrees on every frame, like
     Mixamo's "Character Arm-Space" slider, so arms stop clipping into a bulky body.
  4. If the character has no image textures and --material-from is given, copies the first
     textured material from that file (useful when the rigging service drops textures).
  5. Decimates the mesh to about --triangles (default 18000) before skinning is evaluated,
     so the weights carry over, then exports a GLB with one animation per clip.
"""

import math
import os
import sys

import bpy
from mathutils import Matrix, Quaternion, Vector

def parse_args():
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else []
    usage = ("Usage: blender -b -P build_rigged.py -- --character <file> --out <output.glb> "
             "--clip <name>=<file> [--clip ...] [--material-from <file>] [--triangles 18000] [--arm-space 12]")

    def option(name, default=None, cast=str):
        return cast(argv[argv.index(name) + 1]) if name in argv else default

    clips = []
    for i, arg in enumerate(argv):
        if arg == "--clip" and i + 1 < len(argv) and "=" in argv[i + 1]:
            name, path = argv[i + 1].split("=", 1)
            clips.append((name, os.path.abspath(path)))
    character, out = option("--character"), option("--out")
    if not character or not out or not clips:
        sys.exit(usage)
    material = option("--material-from")
    return (
        os.path.abspath(character),
        os.path.abspath(out),
        clips,
        os.path.abspath(material) if material else None,
        option("--triangles", 18000, int),
        option("--arm-space", 0.0, float),
    )


def log(msg):
    print(f"[build_rigged] {msg}", flush=True)


def import_fbx(path):
    if path.lower().endswith((".glb", ".gltf")):
        return import_glb(path)
    before = set(bpy.data.objects)
    bpy.ops.import_scene.fbx(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def import_glb(path):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=path)
    return [o for o in bpy.data.objects if o not in before]


def find_armature(objects):
    arms = [o for o in objects if o.type == "ARMATURE"]
    if not arms:
        raise RuntimeError("no armature found")
    return arms[0]


def hips_bone(arm):
    for bone in arm.data.bones:
        if bone.name.lower().endswith("hips"):
            return bone
    return arm.data.bones[0]


def bone_prefix(arm):
    name = hips_bone(arm).name
    return name[: len(name) - len("hips")]


def find_bone(arm, suffix):
    for bone in arm.data.bones:
        if bone.name.endswith(suffix):
            return bone
    return None


def rest_differences(char_arm, anim_arm, char_prefix, anim_prefix):
    """Angle in degrees between each bone's rest orientation in the two rigs (world space)."""
    diffs = []
    for bone in char_arm.data.bones:
        other = anim_arm.data.bones.get(anim_prefix + bone.name[len(char_prefix):])
        if other is None:
            continue
        a = (char_arm.matrix_world @ bone.matrix_local).to_quaternion()
        b = (anim_arm.matrix_world @ other.matrix_local).to_quaternion()
        angle = math.degrees(a.rotation_difference(b).angle)
        diffs.append((min(angle, 360.0 - angle), bone.name))  # 356° is the same turn as 4°
    return sorted(diffs, reverse=True)


def bones_by_depth(arm):
    """Bone names grouped by hierarchy depth, parents before children."""
    levels = []

    def walk(bone, depth):
        while len(levels) <= depth:
            levels.append([])
        levels[depth].append(bone.name)
        for child in bone.children:
            walk(child, depth + 1)

    for bone in arm.data.bones:
        if bone.parent is None:
            walk(bone, 0)
    return levels


def assign_action(arm, action):
    arm.animation_data.action = action
    # Blender 4.4+ layered actions also need a slot before they evaluate
    if action is not None and hasattr(arm.animation_data, "action_slot"):
        if arm.animation_data.action_slot is None and len(action.slots):
            arm.animation_data.action_slot = action.slots[0]


def retarget(char_arm, anim_arm, clip_name, arm_space):
    """Bake anim_arm's motion onto char_arm as a new action, frame by frame."""
    src_action = anim_arm.animation_data.action
    start, end = (int(round(f)) for f in src_action.frame_range)
    char_prefix, anim_prefix = bone_prefix(char_arm), bone_prefix(anim_arm)

    pairs = {}
    for pb in char_arm.pose.bones:
        other = anim_arm.pose.bones.get(anim_prefix + pb.name[len(char_prefix):])
        if other is not None:
            pairs[pb.name] = other
    levels = [[n for n in level if n in pairs] for level in bones_by_depth(char_arm)]

    cw, aw = char_arm.matrix_world, anim_arm.matrix_world
    cw_rot_inv = cw.to_quaternion().inverted()
    cw_inv3 = cw.to_3x3().inverted()

    char_hips = hips_bone(char_arm)
    anim_hips = anim_arm.data.bones[anim_prefix + char_hips.name[len(char_prefix):]]
    # Hip movement is scaled by the ratio of hip heights in world space
    size_ratio = (cw.to_3x3() @ char_hips.head_local).length / max((aw.to_3x3() @ anim_hips.head_local).length, 1e-9)
    anim_hips_rest_world = aw @ anim_hips.head_local

    # Arm space: rotate each upper arm outward about the body's forward axis, in rest space
    widen = {}
    if arm_space:
        head = find_bone(char_arm, "Head") or find_bone(char_arm, "Neck")
        if head is None:
            log("WARNING: no Head/Neck bone, can't work out 'up'; skipping arm space")
        else:
            up = (head.head_local - char_hips.head_local).normalized()
            for side in ("LeftArm", "RightArm"):
                bone = find_bone(char_arm, side)
                if bone is None or bone.name not in pairs or bone.parent is None:
                    log(f"WARNING: no {side} bone; skipping it for arm space")
                    continue
                outward = (bone.tail_local - bone.head_local).normalized()  # T-pose: arm points outward
                axis = (-up).cross(outward).normalized()                    # rotates 'down' toward 'outward'
                widen[bone.name] = (axis, math.radians(arm_space), bone.parent.name)

    action = bpy.data.actions.new(clip_name)
    action.use_fake_user = True
    assign_action(char_arm, action)
    for pb in char_arm.pose.bones:
        pb.rotation_mode = "QUATERNION"

    # For the log: how far each upper arm sits from the body, averaged over the clip
    head_bone = find_bone(char_arm, "Head") or find_bone(char_arm, "Neck")
    arm_bones = {side: find_bone(char_arm, side) for side in ("LeftArm", "RightArm")}
    arm_angles = {side: [] for side, bone in arm_bones.items() if bone is not None}

    scene = bpy.context.scene
    previous = {}
    for frame in range(start, end + 1):
        scene.frame_set(frame)
        for level in levels:
            for name in level:
                pb, src = char_arm.pose.bones[name], pairs[name]
                rot = cw_rot_inv @ (aw @ src.matrix).to_quaternion()
                if name in widen:
                    axis, angle, parent = widen[name]
                    parent_pb = char_arm.pose.bones[parent]
                    parent_delta = parent_pb.matrix.to_quaternion() @ char_arm.data.bones[parent].matrix_local.to_quaternion().inverted()
                    rot = Quaternion(parent_delta @ axis, angle) @ rot
                if name == char_hips.name:
                    delta_world = ((aw @ src.head) - anim_hips_rest_world) * size_ratio
                    loc = char_hips.head_local + cw_inv3 @ delta_world
                else:
                    loc = pb.matrix.translation.copy()
                pb.matrix = Matrix.LocRotScale(loc, rot, Vector((1, 1, 1)))
            bpy.context.view_layer.update()

        if head_bone is not None:
            pose = char_arm.pose.bones
            down = (pose[char_hips.name].head - pose[head_bone.name].head).normalized()
            for side in arm_angles:
                b = pose[arm_bones[side].name]
                arm_angles[side].append(math.degrees((b.tail - b.head).normalized().angle(down)))

        for level in levels:
            for name in level:
                pb = char_arm.pose.bones[name]
                q = pb.rotation_quaternion
                if name in previous and previous[name].dot(q) < 0:
                    q.negate()  # keep neighbouring keys on the same hemisphere
                previous[name] = q.copy()
                pb.keyframe_insert("rotation_quaternion", frame=frame)
                if name == char_hips.name:
                    pb.keyframe_insert("location", frame=frame)

    assign_action(char_arm, None)
    averages = {side: sum(v) / len(v) for side, v in arm_angles.items() if v}
    return action, end - start, size_ratio, averages


def delete_objects(objects):
    for o in objects:
        if o.name in bpy.data.objects:
            bpy.data.objects.remove(o, do_unlink=True)


def triangle_count(obj):
    return sum(len(p.vertices) - 2 for p in obj.data.polygons)


def has_image_texture(mat):
    tree = getattr(mat, "node_tree", None) if mat else None
    return bool(tree and any(n.type == "TEX_IMAGE" and n.image for n in tree.nodes))


def main():
    character, out_path, clips, material_from, target_tris, arm_space = parse_args()
    bpy.ops.wm.read_factory_settings(use_empty=True)

    # 1. Character
    char_objects = import_fbx(character)
    arm = find_armature(char_objects)
    meshes = [o for o in char_objects if o.type == "MESH"]
    char_prefix = bone_prefix(arm)
    log(f"character: armature '{arm.name}', {len(meshes)} mesh(es), bone prefix '{char_prefix}'")
    for m in meshes:
        if not m.data.uv_layers:
            log(f"WARNING: mesh '{m.name}' has no UV map, textures will not show")

    # 2. Animations
    if arm.animation_data is None:
        arm.animation_data_create()
    arm.animation_data.action = None
    for track in list(arm.animation_data.nla_tracks):
        arm.animation_data.nla_tracks.remove(track)

    for clip_name, path in clips:
        file_name = os.path.basename(path)
        if not os.path.exists(path):
            log(f"WARNING: {file_name} missing, skipping '{clip_name}'")
            continue
        imported = import_fbx(path)
        anim_arm = find_armature(imported)
        action = anim_arm.animation_data.action if anim_arm.animation_data else None
        if action is None:
            log(f"WARNING: {file_name} has no action, skipping")
            delete_objects(imported)
            continue

        anim_prefix = bone_prefix(anim_arm)
        diffs = rest_differences(arm, anim_arm, char_prefix, anim_prefix)
        worst = diffs[0] if diffs else (0.0, "")
        note = (f"rest pose differs from the character (up to {worst[0]:.0f}° on {worst[1]}), retargeted"
                if worst[0] > 10 else "rest pose matches the character")
        assign_action(anim_arm, action)
        clip, frames, size_ratio, arm_avg = retarget(arm, anim_arm, clip_name, arm_space)
        delete_objects(imported)
        bpy.data.actions.remove(action)

        # One NLA track per clip: the glTF exporter turns each into an animation
        track = arm.animation_data.nla_tracks.new()
        track.name = clip_name
        track.strips.new(clip_name, int(clip.frame_range[0]), clip)
        track.mute = True
        arms = ", ".join(f"{side[:-3].lower()} {deg:.0f}°" for side, deg in arm_avg.items())
        log(f"clip '{clip_name}': {frames} frames, {note}, hip movement x{size_ratio:.4g}"
            + (f", arms widened {arm_space:g}°" if arm_space else "")
            + (f"; arms sit on average {arms} out from the body" if arms else ""))

    # 4. Material
    if not any(has_image_texture(s.material) for m in meshes for s in m.material_slots):
        glb = material_from
        if glb and os.path.exists(glb):
            donor_objects = import_glb(glb)
            donor = next((o for o in donor_objects if o.type == "MESH" and o.material_slots), None)
            if donor:
                mat = donor.material_slots[0].material
                mat.use_fake_user = True
                for m in meshes:
                    m.data.materials.clear()
                    m.data.materials.append(mat)
                log(f"no textures on the rig, using material '{mat.name}' from {os.path.basename(glb)}")
            delete_objects(donor_objects)
        else:
            log("WARNING: the character has no image textures" + (f" and {glb} was not found" if glb else "; pass --material-from <textured file> to borrow a material"))

    # 5. Decimate (before the armature modifier, so the skin weights carry over)
    for m in meshes:
        tris = triangle_count(m)
        if tris <= target_tris:
            log(f"mesh '{m.name}': {tris} triangles, no decimation needed")
            continue
        mod = m.modifiers.new("WebDecimate", "DECIMATE")
        mod.ratio = target_tris / tris
        m.modifiers.move(len(m.modifiers) - 1, 0)
        with bpy.context.temp_override(object=m, active_object=m, selected_objects=[m]):
            bpy.ops.object.modifier_apply(modifier=mod.name)
        log(f"mesh '{m.name}': {tris} → {triangle_count(m)} triangles")

    # Export
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    bpy.ops.export_scene.gltf(
        filepath=out_path,
        export_format="GLB",
        export_animations=True,
        export_animation_mode="NLA_TRACKS",
        export_skins=True,
        export_yup=True,
        export_cameras=False,
        export_lights=False,
    )
    log(f"wrote {out_path} ({os.path.getsize(out_path) / 1024:.0f} KB)")


main()
