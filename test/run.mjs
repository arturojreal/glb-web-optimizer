// Smoke test: builds a dense, textured sample model, optimizes it, and checks the result.
// No binary fixtures: the sample is generated here.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Document, NodeIO } from '@gltf-transform/core';
import { ALL_EXTENSIONS } from '@gltf-transform/extensions';
import { MeshoptDecoder } from 'meshoptimizer';
import sharp from 'sharp';
import { countTriangles, optimizeFile } from '../optimize.mjs';

const TMP = path.join(path.dirname(fileURLToPath(import.meta.url)), 'tmp');
fs.mkdirSync(TMP, { recursive: true });

// A UV sphere with about 80k triangles and a noisy 2048 px PNG texture.
async function makeSample(file) {
  const doc = new Document();
  const buffer = doc.createBuffer();
  const rings = 200, segments = 200;
  const pos = [], uv = [], idx = [];
  for (let r = 0; r <= rings; r++) {
    const v = r / rings, phi = v * Math.PI;
    for (let s = 0; s <= segments; s++) {
      const u = s / segments, theta = u * Math.PI * 2;
      pos.push(Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta));
      uv.push(u, v);
    }
  }
  for (let r = 0; r < rings; r++) {
    for (let s = 0; s < segments; s++) {
      const a = r * (segments + 1) + s, b = a + segments + 1;
      idx.push(a, b, a + 1, b, b + 1, a + 1);
    }
  }
  const raw = Buffer.alloc(2048 * 2048 * 3);
  for (let i = 0; i < raw.length; i++) raw[i] = (i * 2654435761) >>> 24;
  const png = await sharp(raw, { raw: { width: 2048, height: 2048, channels: 3 } }).png().toBuffer();
  const texture = doc.createTexture('base').setImage(new Uint8Array(png)).setMimeType('image/png');
  const material = doc.createMaterial('mat').setBaseColorTexture(texture);
  const prim = doc.createPrimitive()
    .setAttribute('POSITION', doc.createAccessor().setType('VEC3').setArray(new Float32Array(pos)).setBuffer(buffer))
    .setAttribute('TEXCOORD_0', doc.createAccessor().setType('VEC2').setArray(new Float32Array(uv)).setBuffer(buffer))
    .setIndices(doc.createAccessor().setType('SCALAR').setArray(new Uint32Array(idx)).setBuffer(buffer))
    .setMaterial(material);
  const mesh = doc.createMesh('sphere').addPrimitive(prim);
  doc.createScene().addChild(doc.createNode('sphere').setMesh(mesh));
  await new NodeIO().write(file, doc);
}

const input = path.join(TMP, 'sample.glb');
const output = path.join(TMP, 'sample.web.glb');
await makeSample(input);
const { before, after } = await optimizeFile(input, output, { triangles: 5000, texture: 512 });
console.log('before', before, '\nafter ', after);

assert.ok(before.tris > 70000, 'sample should be dense');
assert.ok(after.tris <= 5000 * 1.1, `triangles should be near the 5000 target, got ${after.tris}`);
assert.equal(after.tex, 512, 'texture should be resized to 512 px');
assert.ok(after.bytes < before.bytes / 5, 'output should be much smaller');

await MeshoptDecoder.ready;
const doc = await new NodeIO().registerExtensions(ALL_EXTENSIONS).registerDependencies({ 'meshopt.decoder': MeshoptDecoder }).read(output);
assert.equal(countTriangles(doc), after.tris, 'output reads back with the same triangle count');
assert.equal(doc.getRoot().listTextures()[0].getMimeType(), 'image/webp', 'texture should be WebP');
assert.ok(doc.getRoot().listExtensionsUsed().some((e) => e.extensionName === 'EXT_meshopt_compression'), 'output should be meshopt-compressed');
console.log('ok');
