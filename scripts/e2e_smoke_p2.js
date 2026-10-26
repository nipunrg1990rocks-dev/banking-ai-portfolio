// End-to-end smoke test of the running lakehouse explorer with a real browser.
// Usage: node scripts/e2e_smoke_p2.js [baseUrl] [screenshotDir]
const { chromium } = require('playwright');

const base = process.argv[2] || 'http://localhost:8501';
const shots = process.argv[3] || 'projects/p2_lakehouse/docs/screenshots';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 2200 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const step = async (name, fn) => { await fn(); console.log('PASS', name); };
  const settle = () => page.waitForTimeout(2000);
  const noException = async () => {
    if (await page.locator('[data-testid="stException"]').count()) {
      throw new Error(await page.locator('[data-testid="stException"]').first().innerText());
    }
  };
  const tab = async (name) => { await page.getByRole('tab', { name }).click(); await settle(); await noException(); };

  await step('Mission Control loads with the incident and agent inbox', async () => {
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByText('Agent inbox').first().waitFor({ timeout: 60000 });
    await page.getByText('BLOCKED').first().waitFor();
    await page.getByText(/blocking controls passed/).first().waitFor();
    await settle(); await noException();
    await page.screenshot({ path: `${shots}/00_mission_control.png`, fullPage: true });
  });

  await step('AML Investigator investigates an alert and asks for a human decision', async () => {
    await tab(/AML Investigator/);
    await page.getByRole('button', { name: /Dispatch the AML Investigator/ }).click();
    await page.getByText(/Risk score \d+ \(escalate at 50\)/).waitFor({ timeout: 60000 });
    await page.getByText('Evidence cited').first().waitFor();
    await page.getByText('Your decision').first().waitFor();
    await noException();
    await page.screenshot({ path: `${shots}/09_agent_aml_case.png`, fullPage: true });
    await page.getByRole('button', { name: /Run on all alerts and score/ }).click();
    await page.getByText(/cases fully correct/).first().waitFor({ timeout: 120000 });
    await noException();
  });

  await step('Data Steward finds the root cause of the blocked delivery', async () => {
    await tab(/Data Steward/);
    await page.getByRole('button', { name: /Dispatch the Data Steward/ }).click();
    await page.getByText(/Root cause: TRUNCATED_FILE/).waitFor({ timeout: 60000 });
    await page.getByText('Draft ticket', { exact: false }).first().waitFor();
    await noException();
    await page.screenshot({ path: `${shots}/10_agent_data_steward.png`, fullPage: true });
  });

  await step('Ask Ledgerline answers with SQL and a chart', async () => {
    await tab(/Ask Ledgerline/);
    await page.getByRole('button', { name: /daily transaction volume trend/ }).click();
    await page.getByText('SQL and result').waitFor({ timeout: 60000 });
    await page.waitForTimeout(1500); await noException();
    await page.screenshot({ path: `${shots}/11_agent_ask.png`, fullPage: true });
  });

  await step('Lakehouse explorer overview with lineage graph', async () => {
    await tab(/Lakehouse explorer/);
    await page.locator('svg .node').first().waitFor({ timeout: 30000 });
    await page.getByText('Every transaction accounted for').waitFor();
    await settle(); await noException();
    await page.screenshot({ path: `${shots}/01_overview_lineage.png`, fullPage: true });
  });

  await step('data quality scorecard and quarantine', async () => {
    await tab('Data quality');
    await page.getByText('Quarantine explorer').waitFor();
    await page.getByText('silver_transactions_reconcile_to_bronze').first().waitFor();
    await page.screenshot({ path: `${shots}/02_data_quality.png`, fullPage: true });
  });

  await step('AML alert drill-down', async () => {
    await tab('AML alerts');
    await page.getByText('Investigate an alert').waitFor();
    await page.getByText(/candidates for an investigator/).waitFor();
    await page.getByText(/Why:/).first().waitFor();
    await page.locator('canvas, svg').nth(1).waitFor();
    await page.screenshot({ path: `${shots}/03_aml_alert_drilldown.png`, fullPage: true });
  });

  await step('SCD2 history and change data feed', async () => {
    await tab('Customer history (SCD2)');
    await page.getByText(/Change Data Feed/).first().waitFor();
    await page.getByText('update_postimage').first().waitFor();
    await page.screenshot({ path: `${shots}/04_scd2_history.png`, fullPage: true });
  });

  await step('Delta history and time travel', async () => {
    await tab('Delta Lake');
    await page.getByText(/VERSION AS OF/).first().waitFor();
    await page.getByText(/Time travel: read the table/).first().waitFor();
    await page.screenshot({ path: `${shots}/05_delta_time_travel.png`, fullPage: true });
  });

  await step('SQL console runs a query and blocks file access', async () => {
    await tab('SQL console');
    await page.getByRole('button', { name: 'Run query' }).click();
    await page.locator('[data-testid="stAlert"]').filter({ hasText: /^\s*[\d,]+ rows\s*$/ }).first().waitFor({ timeout: 30000 });
    await page.screenshot({ path: `${shots}/06_sql_console.png`, fullPage: true });
    const box = page.locator('textarea:not([data-testid="stChatInputTextArea"])').first();
    await box.fill("SELECT * FROM read_csv('/etc/passwd')");
    await box.press('Control+Enter');
    await page.getByRole('button', { name: 'Run query' }).click();
    await page.locator('[data-testid="stAlert"]').filter({ hasText: /Permission|disabled|external/i }).first().waitFor({ timeout: 30000 });
    await box.fill('DROP TABLE transactions');
    await box.press('Control+Enter');
    await page.getByRole('button', { name: 'Run query' }).click();
    await page.locator('[data-testid="stAlert"]').filter({ hasText: /read-only/ }).first().waitFor({ timeout: 30000 });
  });

  await step('Gold table browser with SQL definition', async () => {
    await tab('Gold tables');
    await page.getByText('Spark SQL definition').waitFor();
    await page.getByText('Download CSV').waitFor();
    await page.screenshot({ path: `${shots}/07_gold_tables.png`, fullPage: true });
  });

  await step('How it works, verified vs not, agent scorecard', async () => {
    await tab(/How it works/);
    await page.getByText('What is verified vs not').waitFor();
    await page.getByText('Evaluation against the planted answer key').waitFor({ timeout: 120000 });
    await page.waitForTimeout(1500); await noException();
    await page.screenshot({ path: `${shots}/08_how_it_works.png`, fullPage: true });
  });

  if (errors.length) { console.log('PAGE ERRORS', errors); process.exit(1); }
  await browser.close();
  console.log('ALL STEPS PASSED');
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
