import fs from 'fs';
import { initWasm, Resvg } from '../node_modules/@resvg/resvg-wasm/index.mjs';
await initWasm(fs.readFileSync('../node_modules/@resvg/resvg-wasm/index_bg.wasm'));
const font = fs.readFileSync('DejaVuSans-Bold.ttf');

// the mark, drawn in a 512 x 512 box: a moving head on its yoke throwing three beams, a beat pulse through it
const mark = (dark) => {
  const body = dark ? '#d9deea' : '#1d2230', lens = '#ffffff', pulse = dark ? '#ffffff' : '#1d2230';
  return `
  <defs>
    <linearGradient id="bm" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#ff2fb3" stop-opacity="0.95"/><stop offset="1" stop-color="#ff2fb3" stop-opacity="0"/></linearGradient>
    <linearGradient id="bv" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#8a5bff" stop-opacity="0.95"/><stop offset="1" stop-color="#8a5bff" stop-opacity="0"/></linearGradient>
    <linearGradient id="bc" x1="0" y1="1" x2="0" y2="0"><stop offset="0" stop-color="#22d3ff" stop-opacity="0.95"/><stop offset="1" stop-color="#22d3ff" stop-opacity="0"/></linearGradient>
    <radialGradient id="glow" cx="0.5" cy="0.5" r="0.5"><stop offset="0" stop-color="#ffffff"/><stop offset="0.45" stop-color="#bfe9ff"/><stop offset="1" stop-color="#8a5bff" stop-opacity="0"/></radialGradient>
  </defs>
  <!-- beams -->
  <polygon points="236,262 276,262 150,8 30,48" fill="url(#bm)"/>
  <polygon points="238,262 274,262 316,0 196,0" fill="url(#bv)"/>
  <polygon points="236,262 276,262 482,48 362,8" fill="url(#bc)"/>
  <!-- beat pulse -->
  <polyline points="14,372 56,372 78,372 98,306 124,436 146,372 186,372"
    fill="none" stroke="${pulse}" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>
  <polyline points="326,372 366,372 388,306 414,436 434,372 456,372 498,372"
    fill="none" stroke="${pulse}" stroke-width="16" stroke-linecap="round" stroke-linejoin="round"/>
  <!-- base and yoke -->
  <rect x="166" y="440" width="180" height="46" rx="14" fill="${body}"/>
  <rect x="236" y="410" width="40" height="36" fill="${body}"/>
  <path d="M168 418 L168 300 Q168 286 182 286 L196 286 L196 404 L316 404 L316 286 L330 286 Q344 286 344 300 L344 418 Z" fill="${body}"/>
  <!-- head -->
  <rect x="200" y="226" width="112" height="132" rx="40" fill="${body}"/>
  <circle cx="256" cy="268" r="58" fill="url(#glow)" opacity="0.9"/>
  <circle cx="256" cy="268" r="30" fill="${lens}"/>
  <circle cx="256" cy="268" r="30" fill="none" stroke="${dark ? '#8a5bff' : '#3a2f7a'}" stroke-width="6"/>`;
};
const icon = (dark) => `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">${mark(dark)}</svg>`;
const logo = (dark) => `<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="512" viewBox="0 0 1280 512">
  <g>${mark(dark)}</g>
  <defs><linearGradient id="tx" x1="0" y1="0" x2="1" y2="0"><stop offset="0" stop-color="#ff2fb3"/><stop offset="0.5" stop-color="#8a5bff"/><stop offset="1" stop-color="#22d3ff"/></linearGradient></defs>
  <text x="560" y="300" font-family="DejaVu Sans" font-weight="bold" font-size="200" fill="${dark ? '#ffffff' : '#1d2230'}">Beat</text>
  <text x="560" y="470" font-family="DejaVu Sans" font-weight="bold" font-size="150" fill="url(#tx)" letter-spacing="6">DMX</text>
</svg>`;
const render = (svg, w, file) => {
  const png = new Resvg(svg, { fitTo: { mode: 'width', value: w }, font: { fontBuffers: [font], loadSystemFonts: false, defaultFontFamily: 'DejaVu Sans' } }).render().asPng();
  fs.writeFileSync(file, png);
  console.log(file, png.length, 'bytes');
};
fs.mkdirSync('out', { recursive: true });
for (const dark of [false, true]) {
  const p = dark ? 'dark_' : '';
  render(icon(dark), 256, `out/${p}icon.png`);
  render(icon(dark), 512, `out/${p}icon@2x.png`);
  render(logo(dark), 320, `out/${p}logo.png`);
  render(logo(dark), 640, `out/${p}logo@2x.png`);
}
// previews on light and dark backgrounds
const prev = `<svg xmlns="http://www.w3.org/2000/svg" width="1400" height="560">
  <rect width="700" height="560" fill="#f5f6f8"/><rect x="700" width="700" height="560" fill="#111318"/>
  <g transform="translate(40,20) scale(0.5)">${mark(false)}</g>
  <g transform="translate(330,80) scale(0.18)">${mark(false)}</g>
  <g transform="translate(470,110) scale(0.09)">${mark(false)}</g>
  <g transform="translate(740,20) scale(0.5)">${mark(true)}</g>
  <g transform="translate(1030,80) scale(0.18)">${mark(true)}</g>
  <g transform="translate(1170,110) scale(0.09)">${mark(true)}</g>
  <g transform="translate(40,300) scale(0.42)">${logo(false).replace(/<\/?svg[^>]*>/g, '')}</g>
  <g transform="translate(740,300) scale(0.42)">${logo(true).replace(/<\/?svg[^>]*>/g, '')}</g>
</svg>`;
render(prev, 1400, 'preview.png');
