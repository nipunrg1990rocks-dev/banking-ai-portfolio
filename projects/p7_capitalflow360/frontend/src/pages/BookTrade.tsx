import { Alert, Box, Button, Card, CardContent, Grid, IconButton, MenuItem, Stack, TextField, Typography } from "@mui/material";
import Delete from "@mui/icons-material/Delete";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api, newIdempotencyKey } from "../api";
import { ErrorAlert, KeyValues, PageHeader, Section } from "../components/common";

const PRODUCTS: Record<string, { inst: string; sides: string[]; terms: string[]; end?: boolean }> = {
  EQUITY: { inst: "EQUITY", sides: ["BUY", "SELL"], terms: [] },
  ETF: { inst: "ETF", sides: ["BUY", "SELL"], terms: [] },
  GOVT_BOND: { inst: "GOVT_BOND", sides: ["BUY", "SELL"], terms: [] },
  CORP_BOND: { inst: "CORP_BOND", sides: ["BUY", "SELL"], terms: [] },
  FX_SPOT: { inst: "FX_PAIR", sides: ["BUY", "SELL"], terms: ["fx_counter_ccy", "fx_counter_amount"] },
  FX_FORWARD: { inst: "FX_PAIR", sides: ["BUY", "SELL"], terms: ["fx_counter_ccy", "fx_counter_amount", "fx_spot_rate", "fx_forward_points", "fx_tenor_months"] },
  FUTURE: { inst: "FUTURE", sides: ["BUY", "SELL"], terms: [] },
  OPTION: { inst: "OPTION", sides: ["BUY", "SELL"], terms: [] },
  IRS: { inst: "IRS_TEMPLATE", sides: ["PAY_FIXED", "RECEIVE_FIXED"], terms: ["irs_fixed_rate_pct", "irs_float_index", "irs_spread_bps", "irs_tenor_years"], end: true },
  REPO: { inst: "GOVT_BOND", sides: ["REPO"], terms: ["repo_rate_pct", "haircut_pct", "collateral_ccy"], end: true },
  REVERSE_REPO: { inst: "GOVT_BOND", sides: ["REVERSE_REPO"], terms: ["repo_rate_pct", "haircut_pct", "collateral_ccy"], end: true },
  SEC_LOAN: { inst: "EQUITY", sides: ["SEC_LOAN"], terms: ["sl_fee_bps", "collateral_ccy"], end: true },
  SEC_BORROW: { inst: "EQUITY", sides: ["SEC_BORROW"], terms: ["sl_fee_bps", "collateral_ccy"], end: true },
};
const NUMERIC_TERMS = new Set(["fx_counter_amount", "fx_tenor_months", "irs_spread_bps", "irs_tenor_years", "haircut_pct", "sl_fee_bps"]);

export default function BookTrade() {
  const health = useQuery({ queryKey: ["health"], queryFn: () => api("/health") });
  const useRefList = (e: string) => useQuery({ queryKey: ["ref", e], queryFn: () => api(`/refdata/${e}`, { params: { page_size: 500 } }) });
  const insts = useRefList("instruments");
  const clients = useRefList("clients");
  const accounts = useRefList("accounts");
  const cptys = useRefList("counterparties");
  const [f, setF] = useState<Record<string, string>>({ product_type: "EQUITY", side: "BUY" });
  const [allocs, setAllocs] = useState<{ account_id: string; alloc_qty: string }[]>([]);
  const [idem, setIdem] = useState(newIdempotencyKey());
  const spec = PRODUCTS[f.product_type];
  const instOptions = useMemo(() => (insts.data?.items || []).filter((i: any) => i.product_type === spec.inst), [insts.data, spec.inst]);
  const acctOptions = useMemo(() => (accounts.data?.items || []).filter((a: any) => !f.client_id || a.client_id === f.client_id), [accounts.data, f.client_id]);
  const set = (k: string, v: string) => setF((p) => ({ ...p, [k]: v }));

  const body = () => {
    const terms: Record<string, any> = {};
    spec.terms.forEach((k) => {
      if (f[k]) terms[k] = NUMERIC_TERMS.has(k) ? Number(f[k]) : f[k];
    });
    const inst = instOptions.find((i: any) => i.instrument_id === f.instrument_id);
    return {
      product_type: f.product_type, instrument_id: f.instrument_id, client_id: f.client_id, account_id: f.account_id,
      counterparty_id: f.counterparty_id, side: f.side, quantity: f.quantity, price: f.price,
      settle_ccy: f.settle_ccy || inst?.currency || inst?.attrs?.quote_ccy || "USD", trade_date: f.trade_date || health.data?.business_date,
      settlement_date: f.settlement_date || undefined, end_date: f.end_date || undefined, book: f.book || undefined,
      source_trade_ref: f.source_trade_ref || undefined, terms, allocations: allocs.length ? allocs : undefined,
    };
  };
  const validate = useMutation({ mutationFn: () => api("/trades/validate", { method: "POST", body: body() }) });
  const book = useMutation({ mutationFn: () => api("/trades", { method: "POST", body: body(), headers: { "Idempotency-Key": idem } }) });

  const sel = (k: string, label: string, items: any[], idKey: string, text: (x: any) => string) => (
    <TextField select fullWidth size="small" label={label} value={f[k] || ""} onChange={(e) => set(k, e.target.value)} slotProps={{ htmlInput: { "data-testid": `book-${k}` } }}>
      {items.map((x) => <MenuItem key={x[idKey]} value={x[idKey]}>{text(x)}</MenuItem>)}
    </TextField>
  );
  const txt = (k: string, label: string, type = "text") => (
    <TextField fullWidth size="small" label={label} type={type} value={f[k] || ""} onChange={(e) => set(k, e.target.value)} InputLabelProps={type === "date" ? { shrink: true } : undefined} slotProps={{ htmlInput: { "data-testid": `book-${k}` } }} />
  );
  const v: any = validate.data;
  return (
    <Box>
      <PageHeader title="Book a trade" subtitle="Validate first to see the settlement date, economics and any rule warnings computed by the server. Booking uses an idempotency key, so a double click never books twice." />
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, md: 7 }}>
          <Card><CardContent>
            <Grid container spacing={1.5}>
              <Grid size={{ xs: 12, sm: 6 }}>
                <TextField select fullWidth size="small" label="Product" value={f.product_type} onChange={(e) => setF({ product_type: e.target.value, side: PRODUCTS[e.target.value].sides[0], client_id: f.client_id || "", account_id: f.account_id || "" })} slotProps={{ htmlInput: { "data-testid": "book-product_type" } }}>
                  {Object.keys(PRODUCTS).map((p) => <MenuItem key={p} value={p}>{p.replaceAll("_", " ")}</MenuItem>)}
                </TextField>
              </Grid>
              <Grid size={{ xs: 12, sm: 6 }}>{sel("side", "Side", spec.sides.map((s) => ({ s })), "s", (x) => x.s)}</Grid>
              <Grid size={{ xs: 12 }}>{sel("instrument_id", "Instrument", instOptions, "instrument_id", (i) => `${i.instrument_id} · ${i.name} · ${i.currency}`)}</Grid>
              <Grid size={{ xs: 12, sm: 6 }}>{sel("client_id", "Client", clients.data?.items || [], "client_id", (c) => `${c.client_id} · ${c.short_name} (${c.status})`)}</Grid>
              <Grid size={{ xs: 12, sm: 6 }}>{sel("account_id", "Account", acctOptions, "account_id", (a) => `${a.account_id} · ${a.account_name}`)}</Grid>
              <Grid size={{ xs: 12, sm: 6 }}>{sel("counterparty_id", "Counterparty", cptys.data?.items || [], "counterparty_id", (c) => `${c.counterparty_id} · ${c.legal_name}`)}</Grid>
              <Grid size={{ xs: 12, sm: 6 }}>{txt("book", "Book")}</Grid>
              <Grid size={{ xs: 6, sm: 4 }}>{txt("quantity", f.product_type.includes("BOND") || f.product_type.includes("REPO") ? "Nominal" : f.product_type.startsWith("FX") || f.product_type === "IRS" ? "Notional" : "Quantity")}</Grid>
              <Grid size={{ xs: 6, sm: 4 }}>{txt("price", f.product_type.startsWith("FX") ? "Rate" : f.product_type === "IRS" ? "Price (0)" : "Price")}</Grid>
              <Grid size={{ xs: 6, sm: 4 }}>{txt("settle_ccy", "Settle ccy")}</Grid>
              <Grid size={{ xs: 6, sm: 4 }}>{txt("trade_date", `Trade date (${health.data?.business_date || ""})`, "date")}</Grid>
              {(spec.end || f.product_type.includes("REPO")) && <Grid size={{ xs: 6, sm: 4 }}>{txt("settlement_date", "Start / settle date", "date")}</Grid>}
              {spec.end && <Grid size={{ xs: 6, sm: 4 }}>{txt("end_date", "End / maturity date", "date")}</Grid>}
              {spec.terms.map((k) => <Grid key={k} size={{ xs: 6, sm: 4 }}>{txt(k, k.replaceAll("_", " "))}</Grid>)}
              <Grid size={{ xs: 12, sm: 6 }}>{txt("source_trade_ref", "Source reference (optional)")}</Grid>
            </Grid>
            <Typography variant="subtitle2" sx={{ mt: 2 }}>Allocations (optional; default is one allocation to the account)</Typography>
            {allocs.map((a, i) => (
              <Stack key={i} direction="row" spacing={1} sx={{ mt: 1 }}>
                <TextField select size="small" label="Account" value={a.account_id} sx={{ minWidth: 200 }} onChange={(e) => setAllocs(allocs.map((x, j) => (j === i ? { ...x, account_id: e.target.value } : x)))} slotProps={{ htmlInput: { "data-testid": `alloc-account-${i}` } }}>
                  {acctOptions.map((x: any) => <MenuItem key={x.account_id} value={x.account_id}>{x.account_id}</MenuItem>)}
                </TextField>
                <TextField size="small" label="Quantity" value={a.alloc_qty} onChange={(e) => setAllocs(allocs.map((x, j) => (j === i ? { ...x, alloc_qty: e.target.value } : x)))} slotProps={{ htmlInput: { "data-testid": `alloc-qty-${i}` } }} />
                <IconButton onClick={() => setAllocs(allocs.filter((_, j) => j !== i))} aria-label="Remove allocation"><Delete /></IconButton>
              </Stack>
            ))}
            <Button size="small" sx={{ mt: 1 }} onClick={() => setAllocs([...allocs, { account_id: f.account_id || "", alloc_qty: "" }])} data-testid="add-allocation">Add allocation</Button>
            <Stack direction="row" spacing={1} sx={{ mt: 2 }}>
              <Button variant="outlined" onClick={() => validate.mutate()} disabled={validate.isPending} data-testid="validate-trade">Validate</Button>
              <Button variant="contained" onClick={() => book.mutate()} disabled={book.isPending} data-testid="book-trade">Book trade</Button>
              <Button onClick={() => { setIdem(newIdempotencyKey()); book.reset(); validate.reset(); }}>New ticket</Button>
            </Stack>
            <Typography variant="caption" color="text.secondary">Idempotency key: {idem}</Typography>
          </CardContent></Card>
        </Grid>
        <Grid size={{ xs: 12, md: 5 }}>
          <ErrorAlert error={validate.error || book.error} />
          {book.data && <Alert severity="success" sx={{ mb: 2 }} data-testid="book-result">Booked <Link to={`/trades/${(book.data as any).trade.trade_id}`}>{(book.data as any).trade.trade_id}</Link> · status {(book.data as any).trade.status}{(book.data as any).warnings?.length ? ` · warnings: ${(book.data as any).warnings.join(", ")}` : ""}</Alert>}
          {v && !v.valid && <Alert severity="error" data-testid="validate-result">{v.issues.map((i: any) => <div key={i.code}>{i.rule_id} {i.code}: {i.message}</div>)}</Alert>}
          {v?.valid && <Section title="Server validation passed">
            <Alert severity={v.warnings?.length ? "warning" : "success"} sx={{ mb: 1 }} data-testid="validate-result">{v.warnings?.length ? `Warnings: ${v.warnings.join(", ")}` : "No rule warnings"}</Alert>
            <KeyValues data={{ settlement_date: v.trade.settlement_date, status_after_booking: v.trade.status, usd_exposure: v.trade.usd_equivalent, ...v.trade.economics }} />
          </Section>}
        </Grid>
      </Grid>
    </Box>
  );
}
