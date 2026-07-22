// Renderiza a página do boleto do MK-AUTH em PDF (com o QR PIX do banco).
// Uso: NODE_PATH=/opt/hermes/node_modules node render_boleto.js <saida.pdf> <url>
const outPath = process.argv[2];
const url = process.argv[3];

if (!outPath || !url) {
  console.error('Uso: node render_boleto.js <saida.pdf> <url>');
  process.exit(2);
}

(async () => {
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ args: ['--no-sandbox', '--disable-dev-shm-usage'] });
  try {
    const context = await browser.newContext({ ignoreHTTPSErrors: true });
    const page = await context.newPage();
    await page.goto(url, { waitUntil: 'networkidle', timeout: 45000 });
    await page.waitForTimeout(1500); // imagens do código de barras/QR
    await page.pdf({ path: outPath, format: 'A4', printBackground: true,
                     margin: { top: '8mm', bottom: '8mm', left: '8mm', right: '8mm' } });
    console.log('PDF_OK', outPath);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error('ERRO:', e.message);
  process.exit(1);
});
