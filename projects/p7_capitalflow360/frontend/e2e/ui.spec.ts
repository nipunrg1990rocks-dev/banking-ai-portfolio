// CapitalFlow 360 browser tests. Each title starts with its catalogue id (testkit/oracle/specs.py UI_CASES), so
// testkit/merge_results.py can map every browser run back to its case, scenario and rules.
// Titles tagged @mobile also run on the phone-sized projects.
import { expect, type APIRequestContext, type Page, test } from "@playwright/test";

const PASSWORD = process.env.CF360_DEMO_PASSWORD || "Demo!2026";
const ROLES: Record<string, string> = {
  TRADER: "trader1", MIDDLE_OFFICE: "mo1", SETTLEMENTS: "settle1", RECONCILIATION: "recon1", RISK: "risk1", CHECKER: "checker1", ADMIN: "admin1",
};

async function login(page: Page, user: string) {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto("/");
  await page.getByTestId("login-username").fill(user);
  await page.getByTestId("login-password").fill(PASSWORD);
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("page-title")).toBeVisible();
  return errors;
}

async function token(request: APIRequestContext, user: string): Promise<string> {
  const r = await request.post("/api/auth/login", { data: { username: user, password: PASSWORD } });
  return (await r.json()).access_token;
}

async function api(request: APIRequestContext, user: string, method: string, path: string, data?: unknown) {
  const headers = { Authorization: `Bearer ${await token(request, user)}` };
  const r = await request.fetch(path, { method, headers, data });
  return r.json();
}

async function noHorizontalScroll(page: Page) {
  const [sw, cw] = await page.evaluate(() => [document.documentElement.scrollWidth, document.documentElement.clientWidth]);
  expect(sw).toBeLessThanOrEqual(cw + 1);
}

for (const [role, user] of Object.entries(ROLES)) {
  test(`TC-UI-LOGIN-${role} ${role} signs in and lands on their workspace`, async ({ page }) => {
    const errors = await login(page, user);
    await expect(page.getByTestId("current-user")).toContainText(role);
    await expect(page.getByTestId("business-date")).toBeVisible();
    expect(errors).toEqual([]);
  });
}

const PAGES: [string, string, string][] = [
  ["BLOTTER", "/blotter", "mo1"], ["TRADE-DETAIL", "/trades/TRD00001", "mo1"], ["BOOK", "/book", "trader1"], ["ORDERS", "/orders", "trader1"],
  ["QUEUES", "/queues/matching", "mo1"], ["APPROVALS", "/approvals", "checker1"], ["EXCEPTIONS", "/exceptions", "settle1"],
  ["POSITIONS", "/positions", "settle1"], ["RECON", "/recon", "recon1"], ["EVENTS", "/events", "mo1"], ["REFDATA", "/refdata", "mo1"],
  ["DASHBOARDS", "/dashboards/executive", "risk1"], ["TEST-RUNS", "/test-runs", "risk1"], ["REPORTS", "/reports", "risk1"],
  ["AUDIT", "/audit", "risk1"], ["ADMIN", "/admin", "admin1"],
];
for (const [id, path, user] of PAGES) {
  test(`TC-UI-PAGE-${id} ${path} renders without errors @mobile`, async ({ page }) => {
    const errors = await login(page, user);
    await page.goto(path);
    await expect(page.getByTestId("page-title")).toBeVisible();
    await expect(page.getByTestId("error-alert")).toHaveCount(0);
    expect(errors).toEqual([]);
  });
}

for (const name of ["executive", "operations", "product", "risk", "data-quality", "testing", "engineering"]) {
  test(`TC-UI-DASH-${name.toUpperCase()} ${name} dashboard shows KPIs and a chart @mobile`, async ({ page }) => {
    await login(page, "risk1");
    await page.goto(`/dashboards/${name}`);
    await expect(page.locator("[data-testid^='kpi-value-']").first()).toBeVisible();
    await expect(page.locator("[data-testid^='chart-']").first()).toBeVisible();
  });
}

for (const p of ["EQUITY", "ETF", "GOVT_BOND", "CORP_BOND", "FX_SPOT", "FX_FORWARD", "FUTURE", "OPTION", "IRS", "REPO", "REVERSE_REPO", "SEC_LOAN", "SEC_BORROW"]) {
  test(`TC-UI-DETAIL-${p} ${p} trade detail shows its lifecycle path @mobile`, async ({ page, request }) => {
    const list = await api(request, "mo1", "GET", `/api/trades?product_type=${p}&page_size=1&sort=trade_id`);
    const tid = list.items[0].trade_id;
    await login(page, "mo1");
    await page.goto(`/trades/${tid}`);
    await expect(page.getByTestId("page-title")).toContainText(tid);
    await expect(page.getByTestId("lifecycle")).toBeVisible();
  });
}

test("TC-UI-BLOTTER-FILTER-CCY blotter currency filter narrows the grid", async ({ page }) => {
  await login(page, "trader1");
  await page.goto("/blotter?currency=JPY");
  const cells = page.locator("[data-testid='blotter-grid'] [role='gridcell'][data-field='settle_ccy']");
  await expect(cells.first()).toBeVisible();
  const values = await cells.allInnerTexts();
  expect(values.length).toBeGreaterThan(0);
  expect(new Set(values)).toEqual(new Set(["JPY"]));
});

test("TC-UI-BLOTTER-SEARCH blotter search finds a trade by id", async ({ page }) => {
  await login(page, "trader1");
  await page.goto("/blotter?q=TRD00042");
  await expect(page.getByTestId("trade-link-TRD00042")).toBeVisible();
});

test("TC-UI-BLOTTER-OPEN clicking a blotter row opens the trade", async ({ page }) => {
  await login(page, "mo1");
  await page.goto("/blotter?q=TRD00007");
  await page.getByTestId("trade-link-TRD00007").click();
  await expect(page).toHaveURL(/\/trades\/TRD00007$/);
  await expect(page.getByTestId("page-title")).toContainText("TRD00007");
});

async function fillBooking(page: Page, request: APIRequestContext, tradeDate: string) {
  const src = (await api(request, "mo1", "GET", "/api/trades/TRD00010")).trade;
  await login(page, "trader1");
  await page.goto("/book");
  const pick = async (label: string, id: string) => {
    await page.getByRole("combobox", { name: label }).click();
    await page.getByRole("option", { name: new RegExp(`^${id} `) }).click();
  };
  await pick("Instrument", src.instrument_id);
  await pick("Client", src.client_id);
  await pick("Account", src.account_id);
  await pick("Counterparty", src.counterparty_id);
  await page.getByTestId("book-quantity").fill(String(Number(src.quantity)));
  await page.getByTestId("book-price").fill(String(Number(src.price)));
  await page.getByTestId("book-trade_date").fill(tradeDate);
  await page.getByTestId("validate-trade").click();
}

test("TC-UI-BOOK-REJECT booking on a holiday shows the R010 error from the server", async ({ page, request }) => {
  await fillBooking(page, request, "2026-12-25");
  await expect(page.getByTestId("validate-result")).toContainText("R010");
});

test("TC-UI-BOOK-VALIDATE validate (dry run) shows the derived settlement date", async ({ page, request }) => {
  await fillBooking(page, request, "2026-11-06");
  await expect(page.getByTestId("validate-result")).toBeVisible();
  await expect(page.getByTestId("field-settlement_date")).toBeVisible();
});

test("TC-UI-APPROVE-SELF approvals page lists a pending request for a checker", async ({ page, request }) => {
  const src = (await api(request, "mo1", "GET", "/api/trades/TRD00010")).trade;
  const ref = `UI-${Date.now()}`;
  const booked = await api(request, "trader1", "POST", "/api/trades", {
    product_type: src.product_type, instrument_id: src.instrument_id, client_id: src.client_id, account_id: src.account_id,
    counterparty_id: src.counterparty_id, side: src.side, quantity: src.quantity, price: src.price, settle_ccy: src.settle_ccy,
    trade_date: "2026-11-06", source_trade_ref: ref, book: "EQ-FLOW",
  });
  const tid = booked.trade.trade_id;
  const headers = { Authorization: `Bearer ${await token(request, "mo1")}`, "If-Match": "1" };
  const amend = await (await request.patch(`/api/trades/${tid}`, { headers, data: { changes: { quantity: "300" }, reason: "ui test resize" } })).json();
  await login(page, "checker1");
  await page.goto("/approvals");
  await expect(page.getByTestId(`approval-${amend.approval_id}`)).toBeVisible();
  await expect(page.getByTestId(`approval-${amend.approval_id}`).getByTestId("approve")).toBeEnabled();
});

test("TC-UI-NAV-ROLE-TRADER trader navigation offers Book a trade", async ({ page }) => {
  await login(page, "trader1");
  await expect(page.getByTestId("nav-book")).toBeVisible();
});

test("TC-UI-NAV-ROLE-CHECKER checker navigation does not offer Book a trade", async ({ page }) => {
  await login(page, "checker1");
  await expect(page.getByTestId("nav-blotter")).toBeVisible();
  await expect(page.getByTestId("nav-book")).toHaveCount(0);
});

test("TC-UI-MOBILE-BLOTTER blotter is usable at phone width without horizontal page scroll @mobile", async ({ page }) => {
  await login(page, "trader1");
  await page.goto("/blotter");
  await expect(page.getByTestId("blotter-grid")).toBeVisible();
  await noHorizontalScroll(page);
});

test("TC-UI-MOBILE-DASH executive dashboard fits a phone screen @mobile", async ({ page }) => {
  await login(page, "risk1");
  await page.goto("/dashboards/executive");
  await expect(page.locator("[data-testid^='kpi-value-']").first()).toBeVisible();
  await noHorizontalScroll(page);
});

test("TC-UI-LOGOUT sign out returns to the login page @mobile", async ({ page }) => {
  await login(page, "mo1");
  await page.getByTestId("logout").click();
  await expect(page.getByTestId("login-submit")).toBeVisible();
});

test("TC-UI-BAD-LOGIN wrong password shows an error and stays on login @mobile", async ({ page }) => {
  await page.goto("/");
  await page.getByTestId("login-username").fill("trader1");
  await page.getByTestId("login-password").fill("not-the-password");
  await page.getByTestId("login-submit").click();
  await expect(page.getByTestId("error-alert")).toBeVisible();
  await expect(page.getByTestId("login-submit")).toBeVisible();
});
