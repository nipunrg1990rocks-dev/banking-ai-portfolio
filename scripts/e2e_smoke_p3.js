// End-to-end smoke test of the running exception manager with a real browser.
// Usage: node scripts/e2e_smoke_p3.js [baseUrl] [screenshotDir]
const { chromium } = require('playwright');

const base = process.argv[2] || 'http://localhost:8501';
const shots = process.argv[3] || 'projects/p3_exception_manager/docs/screenshots';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1440, height: 2600 } });
  const errors = [];
  page.on('pageerror', e => errors.push(String(e)));
  const step = async (name, fn) => { await fn(); console.log('PASS', name); };
  const settle = () => page.waitForTimeout(2000);
  const top = () => page.evaluate(() => { for (const el of document.querySelectorAll('*')) { if (el.scrollTop) el.scrollTop = 0; } window.scrollTo(0, 0); });
  const shot = async (name, fullPage = true) => { await top(); await page.waitForTimeout(300); await page.screenshot({ path: `${shots}/${name}`, fullPage }); };
  const noException = async () => {
    if (await page.locator('[data-testid="stException"]').count()) {
      throw new Error(await page.locator('[data-testid="stException"]').first().innerText());
    }
  };
  const tab = async (name) => { await page.getByRole('tab', { name }).click(); await settle(); await noException(); };
  const pickCase = async (text) => {
    const box = page.locator('[data-testid="stSelectbox"]').filter({ hasText: 'Exception' }).first();
    await box.click();
    await page.keyboard.type(text);
    await page.getByRole('option').filter({ hasText: text }).first().click();
    await settle(); await noException();
  };
  const persona = async (role) => {
    await page.locator('[data-testid="stSidebar"] [data-testid="stSelectbox"]').first().click();
    await page.getByRole('option').filter({ hasText: role }).first().click();
    await settle(); await noException();
  };

  await step('app loads with the open queue', async () => {
    await page.goto(base, { waitUntil: 'networkidle' });
    await page.getByText('Agentic Operations Exception Manager').first().waitFor({ timeout: 60000 });
    await page.getByText('Value at stake').waitFor();
    await settle(); await noException();
    await shot('01_open_queue.png', false);
  });

  await step('agents work the whole queue', async () => {
    await page.getByRole('button', { name: /Run the agents on all/ }).click();
    await page.getByText('Straight-through').first().waitFor({ timeout: 60000 });
    await page.getByText('Queue by category').first().waitFor({ timeout: 30000 });
    await settle(); await noException();
    await shot('02_control_room.png', true);
  });

  await step('straight-through nostro case shows full trace and verified outcome', async () => {
    await tab('Case workbench');
    await pickCase('Nostro break FX-SWAP');
    await page.getByText('Path through the agent graph').waitFor();
    await page.locator('svg .node >> visible=true').first().waitFor({ timeout: 30000 });
    await page.getByText(/Read the record back/).waitFor();
    await page.getByText(/Auto Resolved/).first().waitFor();
    await shot('03_auto_resolved_trace.png', true);
  });

  await step('sanctions hit: analyst is refused, compliance escalates to MLRO', async () => {
    await pickCase('Sanctions screening hit: Orion');
    await page.getByText(/This step needs: Compliance Officer/).waitFor();
    await page.getByRole('button', { name: /Approve/ }).click();
    await settle();
    await page.getByText(/Refused by the control/).first().waitFor();
    await shot('04_sanctions_refused.png', true);
    await persona('Compliance Officer');
    await tab('Case workbench');
    await page.getByRole('button', { name: /Escalate/ }).click();
    await settle(); await noException();
    await page.getByText(/MLRO queue/).first().waitFor();
  });

  await step('maker-checker on a large payment repair', async () => {
    await persona('Ama Mensah');
    await tab('Case workbench');
    await pickCase('Meridian Freight');
    await page.getByText(/Decision 1 of 3|Decision 1 of 2/).waitFor();
    await page.getByRole('button', { name: /Approve/ }).click();
    await settle(); await noException();
    await page.getByText(/Four-eyes check/).first().waitFor();
    await shot('05_maker_checker.png', true);
    await persona('Rachel Walsh');
    await tab('Case workbench');
    await page.getByRole('button', { name: /Approve/ }).click();
    await settle(); await noException();
    await page.locator('[data-testid="stAlert"]').filter({ hasText: /ran after approval/ }).first().waitFor();
    await page.getByText(/Four-eyes check: approve by r.walsh/).first().waitFor();
  });

  await step('approvals inbox for compliance', async () => {
    await persona('Compliance Officer');
    await page.getByRole('tab', { name: /My approvals/ }).click();
    await settle(); await noException();
    await page.getByText(/Approvals for Samuel Okafor/).waitFor();
    await shot('06_approvals_inbox.png', false);
  });

  await step('prompt-injection ticket is flagged and held', async () => {
    await persona('Ama Mensah');
    await tab('Raise an exception');
    await page.getByRole('button', { name: /prompt-injection example/ }).click();
    await settle();
    await page.getByRole('button', { name: /Submit and run the agent/ }).click();
    await settle(); await noException();
    await tab('Case workbench');
    await page.getByText(/Instruction-like text found/).waitFor({ timeout: 20000 });
    await shot('07_prompt_injection.png', true);
  });

  await step('agent graph and controls', async () => {
    await tab('Agent graph & controls');
    await page.getByText('Delegated authority (USD equivalent)').waitFor();
    await page.locator('svg .node >> visible=true').first().waitFor({ timeout: 30000 });
    await shot('08_graph_and_controls.png', true);
  });

  await step('audit chain verifies, then detects tampering', async () => {
    await tab('Audit trail');
    await page.getByText(/Hash chain verified/).waitFor();
    await shot('09_audit_trail.png', false);
    await page.getByRole('button', { name: /Simulate tampering/ }).click();
    await settle();
    await page.getByText(/Hash chain broken at event #2/).waitFor();
  });

  await step('evaluation tab shows committed results', async () => {
    await tab('Evaluation');
    await page.getByText('Fuzz control violations').waitFor();
    await shot('10_evaluation.png', true);
  });

  if (errors.length) throw new Error('Page errors: ' + errors.join('\n'));
  await browser.close();
  console.log('ALL PASS');
})().catch(e => { console.error('FAIL', e.message); process.exit(1); });
