import { Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Stack, Step, StepLabel, Stepper, Switch, Tab, Table, TableBody, TableCell, TableHead, TableRow, Tabs, TextField, Tooltip, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert, KeyValues, PageHeader, Section, StatusChip } from "../components/common";
import { dt, money, num } from "../format";

function SimpleTable({ rows, cols, testId }: { rows: any[]; cols: { k: string; label?: string; f?: (v: any, r: any) => any }[]; testId?: string }) {
  if (!rows?.length) return <Typography variant="body2" color="text.secondary">None</Typography>;
  return (
    <Box sx={{ overflowX: "auto" }}>
      <Table size="small" data-testid={testId}>
        <TableHead><TableRow>{cols.map((c) => <TableCell key={c.k}>{c.label || c.k.replaceAll("_", " ")}</TableCell>)}</TableRow></TableHead>
        <TableBody>
          {rows.map((r, i) => (
            <TableRow key={i}>{cols.map((c) => <TableCell key={c.k}>{c.f ? c.f(r[c.k], r) : typeof r[c.k] === "object" && r[c.k] !== null ? JSON.stringify(r[c.k]) : String(r[c.k] ?? "")}</TableCell>)}</TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  );
}

function diff(a: any, b: any) {
  const keys = new Set([...Object.keys(a || {}), ...Object.keys(b || {})]);
  return [...keys].filter((k) => JSON.stringify(a?.[k]) !== JSON.stringify(b?.[k]) && k !== "version").map((k) => ({ field: k, before: a?.[k], after: b?.[k] }));
}

function Lifecycle({ d }: { d: any }) {
  const path: string[] = d.lifecycle_path;
  const status: string = d.trade.status;
  const idx = path.indexOf(status);
  const side = ["PARTIALLY_SETTLED", "SETTLEMENT_FAILED", "CANCELLED", "EXERCISED"].includes(status);
  const na = Object.entries(d.not_applicable || {});
  return (
    <Box sx={{ overflowX: "auto" }} data-testid="lifecycle">
      <Stepper activeStep={idx >= 0 ? idx + (["SETTLED", "EXPIRED", "MATURED", "CLOSED"].includes(status) ? 1 : 0) : path.length} alternativeLabel sx={{ minWidth: 640 }}>
        {path.map((s) => <Step key={s} completed={idx >= 0 ? path.indexOf(s) < idx || (s === status && ["SETTLED", "EXPIRED", "MATURED", "CLOSED"].includes(s)) : undefined}><StepLabel>{s.replaceAll("_", " ")}</StepLabel></Step>)}
      </Stepper>
      {side && <Alert severity={status === "CANCELLED" ? "info" : "warning"} sx={{ mt: 1 }}>Side state: {status.replaceAll("_", " ")}</Alert>}
      {na.length > 0 && (
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mt: 1 }}>
          {na.map(([st, why]) => <Tooltip key={st} title={String(why)}><Chip size="small" variant="outlined" label={`${st}: not applicable`} /></Tooltip>)}
        </Stack>
      )}
    </Box>
  );
}

const AMENDABLE = ["quantity", "price", "settlement_date", "counterparty_id", "account_id", "book", "trader_id"];

export default function TradeDetail() {
  const { id } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState(0);
  const [dlg, setDlg] = useState<null | "amend" | "cancel" | "transition" | "force">(null);
  const [form, setForm] = useState<Record<string, string>>({});
  const [rebook, setRebook] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const q = useQuery({ queryKey: ["trade", id], queryFn: () => api(`/trades/${id}`) });
  const act = useMutation({
    mutationFn: async () => {
      const t = q.data.trade;
      if (dlg === "amend") {
        const changes: Record<string, string> = {};
        AMENDABLE.forEach((k) => form[k] && (changes[k] = form[k]));
        return api(`/trades/${id}`, { method: "PATCH", body: { changes, reason: form.reason || "" }, headers: { "If-Match": String(t.version) } });
      }
      if (dlg === "cancel") return api(`/trades/${id}/cancel`, { method: "POST", body: { reason: form.reason || "", rebook: rebook ? { quantity: form.rebook_quantity || t.quantity, price: form.rebook_price || t.price } : null }, headers: { "If-Match": String(t.version) } });
      if (dlg === "transition") return api(`/trades/${id}/transition`, { method: "POST", body: { target: form.target, reason: form.reason || "manual" } });
      return api(`/trades/${id}/force-match`, { method: "POST", body: { reason: form.reason || "" } });
    },
    onSuccess: (r: any) => {
      setMsg(r.approval_id ? `Sent for approval: ${r.approval_id}${r.applied && Object.keys(r.applied).length ? ` (applied now: ${Object.keys(r.applied).join(", ")})` : ""}` : `Done${r.status ? `: ${r.status}` : ""}`);
      setDlg(null);
      setForm({});
      qc.invalidateQueries({ queryKey: ["trade", id] });
    },
  });
  if (q.error) return <ErrorAlert error={q.error} />;
  if (!q.data) return <Typography>Loading…</Typography>;
  const d = q.data;
  const t = d.trade;
  const econ = t.economics || {};
  const versions = d.versions || [];
  return (
    <Box>
      <PageHeader title={`${t.trade_id} · ${t.product_type.replaceAll("_", " ")}`}
        subtitle={<>{t.side} {num(t.quantity, 2)} {t.instrument_id} @ {num(t.price, 6)} {t.trade_ccy} · version {t.version} · booked by {t.trader_id}{t.rebook_of && <> · rebook of <Link to={`/trades/${t.rebook_of}`}>{t.rebook_of}</Link></>}</>}
        actions={<>
          <StatusChip value={t.status} size="medium" />
          {can("trade.amend") && <Button variant="outlined" onClick={() => setDlg("amend")} data-testid="action-amend">Amend</Button>}
          {can("trade.cancel") && t.status !== "CANCELLED" && <Button variant="outlined" color="error" onClick={() => setDlg("cancel")} data-testid="action-cancel">Cancel</Button>}
          {can("trade.transition") && d.allowed_transitions.length > 0 && <Button variant="outlined" onClick={() => setDlg("transition")} data-testid="action-transition">Move status</Button>}
          {can("match.manage") && t.status === "ALLOCATED" && <Button variant="outlined" onClick={() => setDlg("force")} data-testid="action-force-match">Force match</Button>}
        </>} />
      {msg && <Alert severity="success" onClose={() => setMsg(null)} sx={{ mb: 2 }} data-testid="action-result">{msg}</Alert>}
      {(t.flags || []).length > 0 && <Stack direction="row" spacing={1} sx={{ mb: 2 }}>{t.flags.map((f: string) => <Chip key={f} color="warning" label={f} data-testid={`flag-${f}`} />)}</Stack>}
      <Card sx={{ mb: 2 }}><CardContent><Lifecycle d={d} /></CardContent></Card>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ mb: 2 }}>
        {["Economics", `Allocations (${d.allocations.length})`, `Settlement (${d.instructions.length})`, "Confirmation & clearing", `Events (${d.events.length})`, `Ledger (${d.ledger.length})`, `Versions (${versions.length})`, `Audit (${d.audit.length})`, `Exceptions & approvals (${d.exceptions.length + d.approvals.length})`].map((l, i) => <Tab key={i} label={l} data-testid={`tab-${i}`} />)}
      </Tabs>
      {tab === 0 && (
        <>
          <Section title="Trade"><KeyValues data={t} keys={["trade_id", "status", "product_group", "instrument_id", "client_id", "account_id", "counterparty_id", "book", "market", "trade_date", "trade_time_utc", "settlement_date", "end_date", "settle_ccy", "match_status", "source_system", "source_trade_ref", "order_id"]} /></Section>
          <Section title="Computed economics (production engine)"><KeyValues data={econ} /></Section>
          {Object.keys(t.terms || {}).length > 0 && <Section title="Asset-specific terms"><KeyValues data={t.terms} /></Section>}
        </>
      )}
      {tab === 1 && <Section title="Allocations"><SimpleTable testId="allocations-table" rows={d.allocations} cols={[{ k: "allocation_id" }, { k: "account_id" }, { k: "alloc_qty", f: (v) => num(v, 4) }, { k: "settle_amount", f: (v) => money(v, t.settle_ccy) }, { k: "status", f: (v) => <StatusChip value={v} /> }]} /></Section>}
      {tab === 2 && <Section title="Settlement instructions"><SimpleTable testId="instructions-table" rows={d.instructions} cols={[{ k: "instruction_id" }, { k: "leg" }, { k: "direction" }, { k: "account_id" }, { k: "sec_qty", f: (v) => num(v, 4) }, { k: "cash_amount", f: (v, r) => money(v, r.currency) }, { k: "currency" }, { k: "settle_date" }, { k: "settlement_location" }, { k: "status", f: (v) => <StatusChip value={v} /> }, { k: "settled_on" }, { k: "fail_reason" }, { k: "attempts" }]} /></Section>}
      {tab === 3 && <>
        <Section title="Counterparty confirmations"><SimpleTable rows={d.confirmations} cols={[{ k: "confirmation_id" }, { k: "cp_trade_ref" }, { k: "quantity", f: (v) => num(v, 4) }, { k: "price", f: (v) => num(v, 6) }, { k: "settle_amount", f: (v, r) => money(v, r.settle_ccy) }, { k: "settle_date" }, { k: "match_result", f: (v) => <StatusChip value={v} /> }, { k: "match_detail" }]} /></Section>
        <Section title="Clearing (simulated CCP)"><SimpleTable rows={d.clearing} cols={[{ k: "clearing_id" }, { k: "ccp_id" }, { k: "novation_ref" }, { k: "status" }, { k: "business_date" }]} /></Section>
      </>}
      {tab === 4 && <Section title="Lifecycle events"><SimpleTable rows={d.events} cols={[{ k: "event_date" }, { k: "event_type" }, { k: "allocation_id" }, { k: "amount", f: (v, r) => money(v, r.currency) }, { k: "currency" }, { k: "status", f: (v) => <StatusChip value={v} /> }, { k: "details" }]} /></Section>}
      {tab === 5 && <Section title="Ledger postings (double entry)"><SimpleTable testId="ledger-table" rows={d.ledger} cols={[{ k: "value_date" }, { k: "source_event_id" }, { k: "leg_seq" }, { k: "ledger" }, { k: "ledger_account" }, { k: "key" }, { k: "amount", f: (v) => num(v, 6) }, { k: "entry_type" }, { k: "reversal_of" }]} /></Section>}
      {tab === 6 && <Section title="Versions">
        {versions.map((v: any, i: number) => (
          <Box key={v.version} sx={{ mb: 2 }} data-testid={`version-${v.version}`}>
            <Typography variant="subtitle2">v{v.version} · {v.change_type} · {v.changed_by} · {dt(v.changed_at)}</Typography>
            {i > 0 && <SimpleTable rows={diff(versions[i - 1].snapshot, v.snapshot)} cols={[{ k: "field" }, { k: "before" }, { k: "after" }]} />}
          </Box>
        ))}
      </Section>}
      {tab === 7 && <Section title="Audit trail (append-only)"><SimpleTable testId="audit-table" rows={d.audit} cols={[{ k: "ts", f: dt }, { k: "business_date" }, { k: "actor" }, { k: "role" }, { k: "action" }, { k: "correlation_id" }, { k: "details" }]} /></Section>}
      {tab === 8 && <>
        <Section title="Exceptions"><SimpleTable rows={d.exceptions} cols={[{ k: "exception_id" }, { k: "exception_type" }, { k: "severity", f: (v) => <StatusChip value={v} /> }, { k: "status", f: (v) => <StatusChip value={v} /> }, { k: "owner_role" }, { k: "rule_id" }, { k: "message" }]} /></Section>
        <Section title="Approvals"><SimpleTable rows={d.approvals} cols={[{ k: "approval_id" }, { k: "request_type" }, { k: "maker" }, { k: "checker" }, { k: "status", f: (v) => <StatusChip value={v} /> }, { k: "reason" }, { k: "payload" }]} /></Section>
      </>}

      <Dialog open={!!dlg} onClose={() => setDlg(null)} fullWidth maxWidth="sm">
        <DialogTitle>{{ amend: "Amend trade", cancel: "Cancel trade", transition: "Move status", force: "Request force match" }[dlg || "amend"]}</DialogTitle>
        <DialogContent>
          <Stack spacing={1.5} sx={{ mt: 1 }}>
            {dlg === "amend" && <>
              <Alert severity="info">Book and trader changes apply at once. Economic changes need a checker and create a new version (R091). Settled trades must be cancelled and rebooked (R090).</Alert>
              {AMENDABLE.map((k) => <TextField key={k} size="small" label={k.replaceAll("_", " ")} placeholder={String(t[k] ?? "")} value={form[k] || ""} onChange={(e) => setForm({ ...form, [k]: e.target.value })} slotProps={{ htmlInput: { "data-testid": `amend-${k}` } }} />)}
            </>}
            {dlg === "cancel" && <>
              <Alert severity="warning">Cancellation needs a checker. Settled postings are reversed exactly (R097).</Alert>
              <FormControlLabel control={<Switch checked={rebook} onChange={(e) => setRebook(e.target.checked)} />} label="Rebook with corrected terms" />
              {rebook && <><TextField size="small" label="Rebook quantity" placeholder={t.quantity} onChange={(e) => setForm({ ...form, rebook_quantity: e.target.value })} />
                <TextField size="small" label="Rebook price" placeholder={t.price} onChange={(e) => setForm({ ...form, rebook_price: e.target.value })} /></>}
            </>}
            {dlg === "transition" && <TextField select size="small" label="Target status" value={form.target || ""} onChange={(e) => setForm({ ...form, target: e.target.value })} slotProps={{ htmlInput: { "data-testid": "transition-target" } }}>
              {d.allowed_transitions.map((s: string) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
            </TextField>}
            <TextField size="small" label="Reason" required multiline value={form.reason || ""} onChange={(e) => setForm({ ...form, reason: e.target.value })} slotProps={{ htmlInput: { "data-testid": "action-reason" } }} />
            <ErrorAlert error={act.error} />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDlg(null)}>Close</Button>
          <Button variant="contained" onClick={() => act.mutate()} disabled={act.isPending} data-testid="action-submit">Submit</Button>
        </DialogActions>
      </Dialog>
    </Box>
  );
}
