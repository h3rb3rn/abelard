const fs = require('fs');
const path = require('path');
const postcss = require('postcss');
const tailwindcss = require('@tailwindcss/postcss');

async function compile() {
  const srcFile = path.resolve(__dirname, 'services/static/css/tailwind_src.css');
  const outFile = path.resolve(__dirname, 'services/static/css/tailwind.css');

  const input = fs.readFileSync(srcFile, 'utf8');
  console.log('Compiling Tailwind CSS from:', srcFile);
  
  const result = await postcss([
    tailwindcss(),
  ]).process(input, {
    from: srcFile,
    to: outFile,
  });

  fs.writeFileSync(outFile, result.css);
  console.log('Successfully compiled Tailwind CSS to:', outFile, '(', result.css.length, 'bytes)');
}

compile().catch(err => { console.error(err); process.exit(1); });
