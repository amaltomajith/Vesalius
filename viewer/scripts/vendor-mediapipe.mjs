// Vendors MediaPipe into public/mediapipe/ so the viewer loads nothing from a CDN at runtime.
//   - the tasks-vision WASM runtime is copied from node_modules (Apache-2.0, pinned by package-lock)
//   - the hand landmarker model is downloaded once and verified against a pinned SHA-256
// Runs automatically before `npm run build` / `npm run dev` (see package.json). The output is
// gitignored: it is rebuilt from pinned sources, never committed.
import { createHash } from 'node:crypto';
import { cpSync, existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const out = join(root, 'public', 'mediapipe');
mkdirSync(out, { recursive: true });

cpSync(join(root, 'node_modules', '@mediapipe', 'tasks-vision', 'wasm'), join(out, 'wasm'), {
  recursive: true,
});

const MODEL_URL =
  'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';
const MODEL_SHA256 = 'fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1';
const modelPath = join(out, 'hand_landmarker.task');

const sha = (buf) => createHash('sha256').update(buf).digest('hex');
if (!existsSync(modelPath) || sha(readFileSync(modelPath)) !== MODEL_SHA256) {
  const res = await fetch(MODEL_URL);
  if (!res.ok) throw new Error(`hand landmarker download failed: ${res.status}`);
  const buf = Buffer.from(await res.arrayBuffer());
  if (sha(buf) !== MODEL_SHA256) throw new Error('hand landmarker model hash mismatch; refusing it');
  writeFileSync(modelPath, buf);
}
console.log('mediapipe vendored into public/mediapipe');

// three.js's Draco decoder (Apache-2.0, the version package-lock pins): the static site's atlas is
// Draco-compressed (scripts/compress_atlas.py), and the decoder must load from the site, not a CDN
cpSync(join(root, 'node_modules', 'three', 'examples', 'jsm', 'libs', 'draco', 'gltf'), join(root, 'public', 'draco'), {
  recursive: true,
  filter: (src) => !src.endsWith('draco_encoder.js'), // the app only decodes
});
console.log('draco decoder vendored into public/draco');
