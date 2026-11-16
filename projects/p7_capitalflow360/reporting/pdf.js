// Print reporting/out/handbook.html to PDF with Playwright's Chromium.  node reporting/pdf.js
const path = require("path");
const { chromium } = require("playwright");

(async () => {
  const out = path.join(__dirname, "out");
  const browser = await chromium.launch();
  const page = await browser.newPage();
  await page.goto("file://" + path.join(out, "handbook.html"), { waitUntil: "load" });
  const file = path.join(out, "CapitalFlow360_Interview_and_Demonstration_Handbook.pdf");
  await page.pdf({ path: file, format: "A4", printBackground: true, preferCSSPageSize: true });
  await browser.close();
  console.log("wrote", file);
})();
