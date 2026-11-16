# glb-web-optimizer

Make AI-generated and game 3D models small enough for a web page.

Image-to-3D tools such as Tripo and Hyper3D Rodin export models with around two million
triangles and three 4096 px textures: 15 to 65 MB per prop. A web page wants a few hundred
kilobytes. These tools get you there in one command per batch, and include a Blender script that
turns a Mixamo character plus separate animation files into one GLB with named clips.

They were built for the 3D carousel on the [Live Cut](https://arturojreal.com/live-cut/) page
(a game made for Epic Games' Epic AI MegaJam 2026), where 19 models went from about 1 GB to 7.3 MB.

| Tool | What it does |
|---|---|
| `optimize.mjs` | Per model: dedupe, weld, simplify toward a triangle target, resize textures and convert them to WebP, drop redundant animation keys, meshopt-compress. One file, or a batch from a manifest. |
| `preview.mjs` | Writes uncompressed copies that Blender can open (Blender can't read meshopt compression). |
| `build_rigged.py` | Blender script: a rigged character plus animation files in, one GLB with named clips out. Retargets by world orientation, rescales hip movement, can widen the arms, borrows a textured material, decimates. |

## Live Cut numbers

| Model | Before | After |
|---|---|---|
| barrel (Tripo) | 61.1 MB, 1,905,224 triangles, 4096 px | 376 KB, 12,000 triangles, 1024 px WebP |
| van (Tripo) | 61.9 MB, 1,893,139 triangles | 512 KB, 12,000 triangles |
| walkie (small prop) | 14.9 MB, 184,514 triangles | 220 KB, 12,000 triangles, 512 px WebP |
| scavenger (Mixamo rig, 4 clips) | 71.3 MB FBX, 1,943,725 triangles; 24.6 MB after `build_rigged.py` | 637 KB, 17,999 triangles, 512 px WebP |
| all 19 carousel models | 0.95 GB | 7.3 MB |

## Install

Needs [Node.js](https://nodejs.org/) 18 or newer. For `build_rigged.py`, [Blender](https://www.blender.org/) 4.2 or newer.

```sh
git clone https://github.com/arturojreal/glb-web-optimizer.git
cd glb-web-optimizer
npm install
```

## Optimize one model

macOS, Linux, PowerShell and Command Prompt all take the same command:

```sh
node optimize.mjs models/drone.glb web/drone.glb
```

It prints before and after numbers:

```
drone            1913276 ->   12000 tris   4096 -> 1024 px    63278 KB ->  431 KB (textures 260 KB)
```

Options:

| Option | Default | Meaning |
|---|---|---|
| `--triangles N` | 12000 | Triangle target. The simplifier stops early rather than wreck the silhouette, so the result can land above it. |
| `--texture N` | 1024 | Longest texture side in pixels. 512 is plenty for small props and characters that show at a few hundred pixels. |
| `--quality N` | 85 | WebP quality. |
| `--no-simplify` | off | Keep the mesh as it is. Use it for skinned characters (simplify those in Blender with `build_rigged.py`). |
| `--budget-kb N` | 600 | Flag any output larger than this. |

## Optimize a batch

List your models in a manifest (see [`examples/manifest.json`](examples/manifest.json)):

```json
{
  "assets": [
    { "label": "drone", "source": "meshes/drone.glb", "file": "drone.glb" },
    { "label": "van", "source": "meshes/van.glb", "file": "van.glb", "triangles": 15000 },
    { "label": "walkie", "source": "meshes/walkie.glb", "file": "walkie.glb", "texture": 512 },
    { "label": "character", "source": "character_raw.glb", "file": "character.glb", "simplify": false, "texture": 512 }
  ]
}
```

`source` is relative to `--src`, and `file` is written under `--out`. Per-model `triangles`,
`texture` and `simplify` override the command line; `"skip": "reason"` leaves a model out.

```sh
node optimize.mjs --manifest manifest.json --src path/to/models --out web
node optimize.mjs --manifest manifest.json --src path/to/models --out web van drone
```

The second form redoes only the labels you name. The batch ends with the total size and flags it
if it's over `--total-mb` (default 6).

## Check the results

Look at every model before you ship it: thin parts such as antennas, wires and roof gear can
tear when simplified. If one does, rerun it with a higher `--triangles`.

- In a browser: drag the file onto https://gltf-viewer.donmccurdy.com/ (it reads meshopt).
- In Blender: make uncompressed copies first, then use File > Import > glTF 2.0.

```sh
node preview.mjs web               # every .glb in the folder, into ./preview
node preview.mjs web/van.glb --out check
```

## A rigged character with animations

Mixamo, for example, gives you the character ("With Skin") and each animation as its own FBX
("Without Skin"). `build_rigged.py` bakes them into one GLB, then `optimize.mjs` compresses it.

macOS or Linux:

```sh
blender -b -P build_rigged.py -- \
  --character rig/character.fbx --out character_raw.glb \
  --clip idle=rig/idle.fbx --clip walk=rig/walking.fbx --clip run=rig/running.fbx --clip jump=rig/jump.fbx \
  --material-from meshes/character.glb
node optimize.mjs character_raw.glb web/character.glb --no-simplify --texture 512
```

PowerShell (put `&` before a quoted program path; use a backtick to continue a line):

```powershell
& "C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b -P build_rigged.py -- `
  --character rig\character.fbx --out character_raw.glb `
  --clip idle=rig\idle.fbx --clip walk=rig\walking.fbx --clip run=rig\running.fbx --clip jump=rig\jump.fbx `
  --material-from meshes\character.glb
node optimize.mjs character_raw.glb web\character.glb --no-simplify --texture 512
```

Command Prompt (use `^` to continue a line):

```bat
"C:\Program Files\Blender Foundation\Blender 5.0\blender.exe" -b -P build_rigged.py -- ^
  --character rig\character.fbx --out character_raw.glb ^
  --clip idle=rig\idle.fbx --clip walk=rig\walking.fbx --clip run=rig\running.fbx --clip jump=rig\jump.fbx ^
  --material-from meshes\character.glb
node optimize.mjs character_raw.glb web\character.glb --no-simplify --texture 512
```

| Option | Default | Meaning |
|---|---|---|
| `--character FILE` | required | The rigged character, FBX or glTF. |
| `--out FILE` | required | The GLB to write. |
| `--clip NAME=FILE` | at least one | An animation file and the clip name to give it. Repeat for each clip. |
| `--material-from FILE` | none | If the character has no image textures, copy the first textured material from this file (for example the original image-to-3D export, when the rigging service dropped the textures). |
| `--triangles N` | 18000 | Decimate the mesh to about this many triangles, in Blender, before export. |
| `--arm-space DEG` | 0 | Swing the upper arms outward on every frame, like Mixamo's "Character Arm-Space" slider, so arms stop clipping into a bulky body. Start around 12. |

Read the log. For each clip it reports whether the animation's rest pose matched the character,
the hip movement scale, and how far the arms sit from the body on average, plus a warning for
anything missing (no UV map, no textures, a missing clip):

```
[build_rigged] clip 'run': 21 frames, rest pose differs from the character (up to 68Â° on mixamorig:LeftHandMiddle4), retargeted, hip movement x0.9746; arms sit on average left 41Â°, right 50Â° out from the body
[build_rigged] mesh 'tripo_node_...': 1943725 â†’ 17999 triangles
```

## Gotchas

- **Blender can't open meshopt files.** Importing one fails with "Extension
  EXT_meshopt_compression is not available". Use `preview.mjs`, or check in a web viewer.
- **Don't simplify skinned meshes outside Blender.** Simplifying after skinning can tear the
  mesh at the joints. `build_rigged.py` decimates before export, so pass `--no-simplify` to
  `optimize.mjs` for characters.
- **Animation-only FBX files often import with a posed rest pose.** Copying their actions onto
  the character then twists every bone. That's why `build_rigged.py` retargets by matching
  each bone's world orientation frame by frame instead of copying keys.
- **glTF allows 4 bone influences per vertex.** Blender keeps the strongest 4 on export; a
  rig that leans on more can look slightly different on the web.
- **Bone names with `:` in three.js.** three.js drops animation tracks whose bone names contain
  characters it reserves for property paths, unless they're sanitized. Mixamo names such as
  `mixamorig:Hips` work when loaded through `GLTFLoader`, which sanitizes them; build tracks by
  hand and you need `THREE.PropertyBinding.sanitizeNodeName`.
- **Bounding boxes of skinned meshes in three.js.** Compute them only after
  `skeleton.update()`, or the box is taken from the bind pose and the model frames wrong.
- **The simplifier can stop short of the target.** It caps the geometric error to keep the
  silhouette; the report shows where it landed so you can decide.

## Tested with

Blender 5.0.1, Node 24 (18 or newer works), gltf-transform 4.5.1, meshoptimizer 1.3.0 and sharp
0.35.5, on Windows 11. `npm test` builds a dense textured sample, optimizes it and checks the
output; GitHub Actions runs it on Ubuntu and Windows.

## License

MIT. See [LICENSE](LICENSE).
