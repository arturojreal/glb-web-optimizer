#!/usr/bin/env node
// Writes uncompressed copies of meshopt-compressed GLBs so Blender can open them.
// Blender's glTF importer can't read EXT_meshopt_compression; browsers (three.js,
// model-viewer, Babylon.js) can, so the web files stay compressed.
//
// Usage:
//   node preview.mjs <file.glb | folder> [more files or folders] [--out preview]
//
// Copies go to ./preview/<name>.glb by default. Same triangles and textures, just without
// the mesh compression, so they look exactly like the web versions.

import fs from 'node:fs';
import path from 'node:path';
import { NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS, EXTMeshoptCompression } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';

const args = process.argv.slice(2);
const outIndex = args.indexOf('--out');
const OUT = path.resolve(outIndex === -1 ? 'preview' : args[outIndex + 1]);
const inputs = args.filter((_, i) => outIndex === -1 || (i !== outIndex && i !== outIndex + 1));

const files = [];
for (const input of inputs) {
  if (!fs.existsSync(input)) {
    console.error(`Not found: ${input}`);
    process.exit(1);
  }
  if (fs.statSync(input).isDirectory()) {
    for (const f of fs.readdirSync(input)) if (f.toLowerCase().endsWith('.glb')) files.push(path.join(input, f));
  } else {
    files.push(input);
  }
}
if (!files.length) {
  console.log('Usage: node preview.mjs <file.glb | folder> [...] [--out preview]');
  process.exit(1);
}

await MeshoptDecoder.ready;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder });

fs.mkdirSync(OUT, { recursive: true });
for (const file of files) {
  const doc = await io.read(file);
  for (const ext of doc.getRoot().listExtensionsUsed()) {
    if (ext.extensionName === EXTMeshoptCompression.EXTENSION_NAME) ext.dispose();
  }
  const out = path.join(OUT, path.basename(file));
  await io.write(out, doc);
  console.log(`${path.basename(file).padEnd(20)} -> ${path.relative(process.cwd(), out)}`);
}
console.log('\nOpen these in Blender with File > Import > glTF 2.0.');
