/** Rebuild the grayscale QR-chip E mask from the actual wordmark's first E.
 * Run: NODE_PATH=/tmp/booth-tools/node_modules node scripts/build_booth_qr_e.mjs
 * (requires sharp; the public SVG is the source of truth).
 */
import { createRequire } from 'node:module';
import { readFile } from 'node:fs/promises';
const require = createRequire(import.meta.url);
const sharp = require('sharp');
const source = await readFile('frontend/public/assets/logo-wordmark-pins.svg', 'utf8');
const paths = [...source.matchAll(/<path\b[^>]*\bd="([^"]+)"[^>]*>/g)];
const firstE = paths.at(-1)?.[1];
if (!firstE?.startsWith('M1996.76 ')) throw new Error('EESoc first E path was not found');
// Exact same crop as the SVG editor: x 400, y 680, width 2120, height 1750.
const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="423" viewBox="400 680 2120 1750"><path d="${firstE}" fill="#FFFFFF" fill-rule="evenodd"/></svg>`;
await sharp(Buffer.from(svg)).ensureAlpha().extractChannel('alpha').png()
  .toFile('backend/api/assets/booth/qr-e-mask.png');
console.log('Generated 512px EESoc E mask');
