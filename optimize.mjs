#!/usr/bin/env node
// Shrinks glTF/GLB models for the web: weld, simplify toward a triangle target, resize
// textures and convert them to WebP, drop redundant animation keys, meshopt-compress.
//
// One model:
//   node optimize.mjs <in.glb> <out.glb> [--triangles 12000] [--texture 1024] [--no-simplify]
//
// A batch from a manifest (see examples/manifest.json):
//   node optimize.mjs --manifest <manifest.json> --src <source dir> --out <output dir> [label ...]
//
// Options:
//   --triangles N   triangle target (default 12000); the simplifier stops early rather than
//                   wreck the silhouette, so the result can land above the target
//   --texture N     longest texture side in pixels (default 1024)
//   --quality N     WebP quality, 1 to 100 (default 85)
//   --no-simplify   keep the mesh as is (use for skinned characters built in Blender)
//   --budget-kb N   warn when a file ends up larger than this (default 600)
//   --total-mb N    batch only: warn when all outputs together exceed this (default 6)

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { dedup, meshopt, prune, resample, simplify, textureCompress, weld } from '@gltf-transform/functions';
import { MeshoptDecoder, MeshoptEncoder, MeshoptSimplifier } from 'meshoptimizer';
import sharp from 'sharp';

export const DEFAULTS = { triangles: 12000, texture: 1024, quality: 85, simplify: true };

await Promise.all([MeshoptEncoder.ready, MeshoptDecoder.ready, MeshoptSimplifier.ready]);
const io = new NodeIO()
  .registerExtensions(ALL_EXTENSIONS)
  .registerDependencies({ 'meshopt.encoder': MeshoptEncoder, 'meshopt.decoder': MeshoptDecoder });

export function countTriangles(doc) {
  let tris = 0;
  for (const mesh of doc.getRoot().listMeshes()) {
    for (const prim of mesh.listPrimitives()) {
      if (prim.getMode() !== 4) continue; // TRIANGLES only
      const indices = prim.getIndices();
      const count = indices ? indices.getCount() : prim.getAttribute('POSITION').getCount();
      tris += count / 3;
    }
  }
  return Math.round(tris);
}

function textureBytes(doc) {
  return doc.getRoot().listTextures().reduce((n, t) => n + (t.getImage()?.byteLength || 0), 0);
}

function maxTextureSize(doc) {
  let max = 0;
  for (const t of doc.getRoot().listTextures()) {
    const size = t.getSize();
    if (size) max = Math.max(max, size[0], size[1]);
  }
  return max;
}

const kb = (bytes) => `${(bytes / 1024).toFixed(0)} KB`;

export async function optimizeFile(input, output, options = {}) {
  const { triangles, texture, quality, simplify: simplifyMesh } = { ...DEFAULTS, ...options };
  const doc = await io.read(input);
  const before = { tris: countTriangles(doc), tex: maxTextureSize(doc), bytes: fs.statSync(input).size };

  const steps = [dedup(), weld()];
  if (simplifyMesh && before.tris > triangles) {
    // The error cap keeps silhouettes; if it stops short of the target, the report shows it.
    steps.push(simplify({ simplifier: MeshoptSimplifier, ratio: triangles / before.tris, error: 0.01 }));
  }
  steps.push(
    resample(), // drops redundant animation keys (Mixamo bakes every bone on every frame)
    prune(),
    textureCompress({ encoder: sharp, targetFormat: 'webp', resize: [texture, texture], quality }),
    meshopt({ encoder: MeshoptEncoder, level: 'medium' }),
  );
  await doc.transform(...steps);

  fs.mkdirSync(path.dirname(path.resolve(output)), { recursive: true });
  await io.write(output, doc);
  const after = { tris: countTriangles(doc), tex: maxTextureSize(doc), texBytes: textureBytes(doc), bytes: fs.statSync(output).size };
  return { before, after };
}

function report(label, { before, after }, budgetKb) {
  const over = after.bytes > budgetKb * 1024;
  const flag = over ? `  ! over ${budgetKb} KB${after.tex > 512 ? ', try --texture 512' : ''}` : '';
  console.log(
    `${label.padEnd(14)} ${String(before.tris).padStart(9)} -> ${String(after.tris).padStart(7)} tris   ` +
    `${String(before.tex).padStart(4)} -> ${String(after.tex).padStart(4)} px   ` +
    `${kb(before.bytes).padStart(9)} -> ${kb(after.bytes).padStart(7)} (textures ${kb(after.texBytes)})${flag}`
  );
}

// ---- CLI -----------------------------------------------------------------------------
const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const args = process.argv.slice(2);
  const valueOf = (name) => {
    const i = args.indexOf(name);
    return i === -1 ? undefined : args[i + 1];
  };
  const num = (name, fallback) => (valueOf(name) === undefined ? fallback : Number(valueOf(name)));
  const takesValue = new Set(['--triangles', '--texture', '--quality', '--budget-kb', '--total-mb', '--manifest', '--src', '--out']);
  const positional = args.filter((a, i) => !a.startsWith('--') && !takesValue.has(args[i - 1]));
  const cli = {
    triangles: num('--triangles', DEFAULTS.triangles),
    texture: num('--texture', DEFAULTS.texture),
    quality: num('--quality', DEFAULTS.quality),
    simplify: !args.includes('--no-simplify'),
  };
  const budgetKb = num('--budget-kb', 600);
  const usage = 'Usage:\n  node optimize.mjs <in.glb> <out.glb> [options]\n  node optimize.mjs --manifest <manifest.json> --src <dir> --out <dir> [label ...]';

  if (args.includes('--help') || args.includes('-h')) {
    console.log(usage);
  } else if (valueOf('--manifest')) {
    const manifestPath = valueOf('--manifest');
    const src = valueOf('--src') || path.dirname(manifestPath);
    const out = valueOf('--out');
    if (!out) {
      console.error(`--out is required with --manifest\n${usage}`);
      process.exit(1);
    }
    const only = new Set(positional);
    // Strip a byte-order mark: Windows editors and PowerShell often add one.
    const { assets } = JSON.parse(fs.readFileSync(manifestPath, 'utf8').replace(/^﻿/, ''));
    let failed = 0;
    for (const asset of assets) {
      if (only.size && !only.has(asset.label)) continue;
      if (asset.skip) {
        console.log(`${asset.label.padEnd(14)} skipped${asset.skip === true ? '' : `: ${asset.skip}`}`);
        continue;
      }
      const input = path.resolve(src, asset.source);
      if (!fs.existsSync(input)) {
        console.log(`${asset.label.padEnd(14)} missing: ${input}`);
        failed++;
        continue;
      }
      // Per-asset fields override the command line, which overrides the defaults.
      const result = await optimizeFile(input, path.join(out, asset.file), {
        ...cli,
        ...(asset.triangles !== undefined && { triangles: asset.triangles }),
        ...(asset.texture !== undefined && { texture: asset.texture }),
        ...(asset.simplify !== undefined && { simplify: asset.simplify }),
      });
      report(asset.label, result, budgetKb);
    }
    let total = 0;
    for (const asset of assets) {
      const f = path.join(out, asset.file);
      if (fs.existsSync(f)) total += fs.statSync(f).size;
    }
    const totalMb = num('--total-mb', 6);
    console.log(`\nTotal in ${out}: ${kb(total)}${total > totalMb * 1024 * 1024 ? `  ! over ${totalMb} MB` : ''}`);
    console.log('Look at every model in a viewer before shipping: thin parts can tear when simplified.');
    process.exit(failed ? 1 : 0);
  } else if (positional.length === 2) {
    const [input, output] = positional;
    report(path.basename(output).replace(/\.gl(b|tf)$/i, ''), await optimizeFile(input, output, cli), budgetKb);
  } else {
    console.error(usage);
    process.exit(1);
  }
}
