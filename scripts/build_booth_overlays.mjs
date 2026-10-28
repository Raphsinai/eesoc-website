/** Regenerate the transparent static 300 DPI overlays from the supplied SVGs.
 * The original SVGs remain the design source of truth. Requires sharp locally:
 *   npm install --no-save --prefix /tmp/booth-tools sharp
 *   NODE_PATH=/tmp/booth-tools/node_modules node scripts/build_booth_overlays.mjs
 * (or install sharp locally in this repository).
 */
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
import { resolve } from 'node:path';
const require = createRequire(import.meta.url);
const sharp = require('sharp');
const root = resolve('backend/api/assets/booth');
for (let count = 1; count <= 4; count++) {
  let svg = await readFile(`${root}/booth-${count}-photo.svg`, 'utf8');
  svg = svg.replace(/<rect id="background"[^>]*\/>/g, '');
  svg = svg.replace(/<rect\b(?=[^>]*\bid="photo-\d+-window")[^>]*\/>/g, '');
  svg = svg.replace(/<text\b(?=[^>]*\bid="(?:event-name|event-details|scan-text)")[^>]*>[\s\S]*?<\/text>/g, '');
  if (svg.includes('{{EVENT_NAME}}') || svg.includes('{{EVENT_DETAILS}}')) {
    throw Error(`Could not remove dynamic title from layout ${count}`);
  }
  await sharp(Buffer.from(svg), { density: 300 }).resize(1181, 1747).png()
    .toFile(`${root}/overlay-${count}.png`);
  console.log(`Rendered layout ${count}`);
}
