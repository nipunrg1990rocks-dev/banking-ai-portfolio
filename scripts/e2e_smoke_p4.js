// End-to-end smoke test of the running risk control room with a real browser.
// Usage: node scripts/e2e_smoke_p4.js [baseUrl] [screenshotDir]
const { chromium } = require('playwright');

const base = process.argv[2] || 'http://localhost:8501';
const shots = process.argv[3] || 'projects/p4_risk_dashboard/docs/screenshots';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 1800 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const step = async (name, fn) => { await fn(); console.log('PASS', name); };
  const settle = (ms = 2500) => page.waitForTimeout(ms);
  const noException = async () => {
    if (await page.locator('[data-testid="stException"]').count()) {
      throw new Error(await page.locator('[data-testid="stException"]').first().innerText());
    }
  };
  const tab = async (name) => { await page.getByRole('tab', { name }).click(); await settle(); await noException(); };
  const panel = () => page.locator('[role="tabpanel"]:visible');

  await step('control room loads with KRIs, agent briefing and findings', async () => {
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByText('Risk & Data-Quality Control Room').first().waitFor({ timeout: 90000 });
    await page.getByText(/Agent briefing/).first().waitFor({ timeout: 90000 });
    await page.getByText('Liquidity coverage ratio').first().waitFor();
    await page.getByText(/CHG-2026-1043/).first().waitFor();
    await settle(5000); await noException();
    await page.screenshot({ path: `${shots}/01_control_room.png`, fullPage: false });
  });

  await step('watch the agent investigate (tool calls stream in)', async () => {
    await page.getByRole('button', { name: /Watch the agent investigate/ }).click();
    await page.getByText(/Done: \d+ tool calls/).waitFor({ timeout: 60000 });
    await page.getByText(/correlate_changes\(/).first().waitFor();
    await noException();
    await page.getByText(/Done: \d+ tool calls/).scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/02_agent_investigation.png`, fullPage: false });
  });

  await step('ask why: root cause with evidence and tool trace', async () => {
    await tab('Ask the risk agent');
    await page.getByRole('button', { name: 'Why did reconciliation breaks go up?' }).click();
    await panel().getByText(/NOSTRO_EUR/).first().waitFor({ timeout: 60000 });
    await panel().getByText(/CHG-2026-1043/).first().waitFor();
    await panel().getByText(/How the agent worked it out/).first().waitFor();
    await settle(); await noException();
    await page.screenshot({ path: `${shots}/03_ask_why.png`, fullPage: false });
  });

  await step('ask a typed what-if question', async () => {
    await page.getByPlaceholder(/Ask the risk agent/).fill('What if another USD 300m of deposits leaves?');
    await page.keyboard.press('Enter');
    await panel().getByText(/USD 300m/).first().waitFor({ timeout: 60000 });
    await settle(); await noException();
    await page.getByText(/USD 300m/).first().scrollIntoViewIfNeeded();
    await page.screenshot({ path: `${shots}/04_ask_what_if.png`, fullPage: false });
  });

  await step('operations and data-quality tab', async () => {
    await tab('Operations & data quality');
    await panel().getByText(/Rule health by week/).waitFor();
    await settle(3000);
    await page.screenshot({ path: `${shots}/05_operations_dq.png`, fullPage: false });
  });

  await step('metric explorer with drill-down and SQL', async () => {
    await tab('Metric explorer');
    await panel().getByText('Break down by').waitFor();
    await panel().locator('[data-testid="stSelectbox"]').first().click();
    await page.keyboard.type('Open reconciliation breaks');
    await page.keyboard.press('Enter');
    await settle(4000); await noException();
    await panel().getByText(/NOSTRO_EUR/).first().waitFor({ timeout: 30000 });
    await page.screenshot({ path: `${shots}/06_metric_explorer.png`, fullPage: false });
  });

  await step('what-if lab', async () => {
    await tab('What-if lab');
    await panel().getByText(/Payments Ops capacity/).waitFor();
    await panel().getByText(/LCR after outflow/).waitFor();
    await settle(3000);
    await page.screenshot({ path: `${shots}/07_what_if_lab.png`, fullPage: false });
  });

  await step('board pack: approval needs a named reviewer, then it is logged', async () => {
    await tab('Board pack');
    const approve = page.getByRole('button', { name: /Approve board pack/ });
    if (!(await approve.isDisabled())) throw new Error('Approve should be disabled without a reviewer');
    await page.getByPlaceholder(/A\. Analyst/).fill('A. Analyst, Head of ERM');
    await page.keyboard.press('Enter');
    await settle();
    await approve.click();
    await panel().getByText(/Approved by A\. Analyst/).first().waitFor({ timeout: 30000 });
    await panel().getByText(/Hash chain intact/).waitFor();
    await settle(); await noException();
    await page.screenshot({ path: `${shots}/08_board_pack.png`, fullPage: false });
  });

  await step('how it works: architecture, real vs simulated, back-test', async () => {
    await tab('How it works');
    await panel().getByText('What is real, what is simulated').waitFor();
    await panel().getByText(/Agent back-test/).waitFor();
    await panel().locator('svg .node').first().waitFor({ timeout: 30000 });
    await settle();
    await page.screenshot({ path: `${shots}/09_how_it_works.png`, fullPage: false });
  });

  await browser.close();
  if (errors.length) { console.error('Page errors:', errors); process.exit(1); }
  console.log('All steps passed');
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
