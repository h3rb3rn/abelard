const fs = require('fs');
const path = require('path');

async function main() {
  // Load @tailwindcss/postcss dynamically
  const pluginPath = path.resolve(__dirname, 'node_modules/@tailwindcss/postcss/dist/index.mjs');
  const postcssPath = path.resolve(__dirname, 'node_modules/postcss/lib/postcss.mjs');

  // Use require with .js alias to the ESM bundle
  // PostCSS v8 API via CJS-compatible wrapper
  const { readFileSync } = require('fs');
  const postcssPkg = JSON.parse(readFileSync(path.resolve(__dirname, 'node_modules/postcss/package.json'), 'utf8'));

  // Use node --input-type=module for ESM
  console.log('PostCSS installed:', postcssPkg.version);

  // Since we can't dynamically import in CommonJS easily, let's write the CSS output directly
  // using @tailwindcss/postcss which generates pure Tailwind v4 output
  const tailwindSrc = readFileSync(path.resolve(__dirname, 'services/static/css/tailwind.css'), 'utf8');
  console.log('Source file has', tailwindSrc.split('\n').length, 'lines');

  // Check the existing tailwind.css for syntax issues
  if (/\d{5,}em/.test(tailwindSrc)) {
    console.log('WARN: potential mega-num found');
  }
  if (/#[a-f0-9]{2,5}(?![a-f0-9])/.test(tailwindSrc)) {
    console.log('WARN: potentially short hex color found');
  }

  // Check for broken calc values like .873em  
  const brokenValues = [
    /\.873em/g,        // broken decimal em value
    /#[a-f0-9]{2}(?![a-f0-9])/g,  // truncated hex (#abc instead of #abcdef)  
    /[^\/]\/[a-z]/gi,  // bare slash not in calc/property
  ];

  for (const re of brokenValues) {
    const matches = tailwindSrc.match(re);
    if (matches) {
      console.log('Found suspicious pattern', re, ':', matches.slice(0, 5));
    }
  }
}

main().catch(console.error);
