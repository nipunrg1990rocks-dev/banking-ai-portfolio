import { Alert, Box, Button, Card, CardContent, Chip, Grid, MenuItem, Stack, Tab, Tabs, TextField, Typography } from "@mui/material";
import { DataGrid } from "@mui/x-data-grid";
import { useQuery } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams } from "react-router";
import { api } from "../api";
import { autoColumns } from "../components/autoColumns";
import { ChartView } from "../components/ChartView";
import { ErrorAlert, KpiCard, PageHeader } from "../components/common";

const NAMES: Record<string, { title: string; who: string }> = {
  executive: { title: "Executive", who: "Volumes, settlement performance and open risk in one view" },
  operations: { title: "Operations", who: "Queues, matching, fails and exception ageing" },
  product: { title: "Product", who: "Economics per asset class" },
  risk: { title: "Risk & control", who: "Limits, maker-checker and margin" },
  "data-quality": { title: "Data quality", who: "Completeness, validity and rejection codes" },
  testing: { title: "Testing", who: "Executed cases, pass rates, defects and coverage" },
  engineering: { title: "Engineering", who: "Processing runs, lakehouse jobs, API latency and lineage" },
};
const FILTERS = [
  { k: "product_group", label: "Product group", opts: ["", "EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"] },
  { k: "currency", label: "Currency", opts: ["", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"] },
];

export default function Dashboard() {
  const { name = "executive" } = useParams();
  const nav = useNavigate();
  const [sp, setSp] = useSearchParams();
  const params = Object.fromEntries(["product_group", "currency", "book", "counterparty_id", "as_of"].map((k) => [k, sp.get(k) || undefined]));
  const q = useQuery({ queryKey: ["dashboard", name, params], queryFn: () => api(`/dashboards/${name}`, { params }) });
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }); };
  const d = q.data;
  return (
    <Box>
      <PageHeader title={`${NAMES[name]?.title || name} dashboard`} subtitle={NAMES[name]?.who} />
      <Tabs value={name} onChange={(_, v) => nav(`/dashboards/${v}?${sp.toString()}`)} variant="scrollable" sx={{ mb: 2 }}>
        {Object.entries(NAMES).map(([k, v]) => <Tab key={k} value={k} label={v.title} data-testid={`dash-tab-${k}`} />)}
      </Tabs>
      <Card sx={{ mb: 2 }}><CardContent>
        <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" alignItems="center">
          {FILTERS.map((f) => (
            <TextField key={f.k} select size="small" label={f.label} value={sp.get(f.k) || ""} onChange={(e) => set(f.k, e.target.value)} sx={{ minWidth: 150 }} slotProps={{ htmlInput: { "data-testid": `dash-filter-${f.k}` } }}>
              {f.opts.map((o) => <MenuItem key={o} value={o}>{o || "All"}</MenuItem>)}
            </TextField>
          ))}
          <TextField size="small" type="date" label="As of" InputLabelProps={{ shrink: true }} value={sp.get("as_of") || ""} onChange={(e) => set("as_of", e.target.value)} slotProps={{ htmlInput: { "data-testid": "dash-filter-as_of" } }} />
          {sp.toString() && <Button onClick={() => setSp(new URLSearchParams(), { replace: true })}>Clear</Button>}
          <Box sx={{ flex: 1 }} />
          {d && <Typography variant="caption" color="text.secondary" data-testid="dash-meta">As of {d.as_of} · refreshed {d.refreshed_at} · data processed {d.data_last_processed_at || "n/a"}</Typography>}
        </Stack>
        {d?.currency_basis && <Typography variant="caption" color="text.secondary">{d.currency_basis}</Typography>}
      </CardContent></Card>
      <ErrorAlert error={q.error} />
      {name === "testing" && d && !d.test_run && <Alert severity="info" sx={{ mb: 2 }}>No test run has been imported yet. Results appear here after a Playwright run is imported.</Alert>}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {(d?.kpis || []).map((k: any) => <Grid key={k.id} size={{ xs: 6, sm: 4, md: 3 }}><KpiCard k={k} /></Grid>)}
      </Grid>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {(d?.charts || []).map((c: any) => <Grid key={c.id} size={{ xs: 12, lg: 6 }}><ChartView c={c} /></Grid>)}
      </Grid>
      {d?.lineage && (
        <Card sx={{ mb: 2 }} data-testid="lineage"><CardContent>
          <Typography variant="subtitle2" fontWeight={700} gutterBottom>Data lineage</Typography>
          <Stack spacing={0.5}>{d.lineage.map((l: any, i: number) => <Stack key={i} direction="row" spacing={1} alignItems="center"><Chip size="small" label={l.from} /><span>→</span><Chip size="small" color="primary" variant="outlined" label={l.to} /></Stack>)}</Stack>
        </CardContent></Card>
      )}
      {d?.table?.length > 0 && (
        <Box sx={{ height: 420 }} data-testid="dash-table">
          <DataGrid density="compact" rows={d.table.map((r: any, i: number) => ({ _id: i, ...r }))} getRowId={(r) => r._id} columns={autoColumns(Object.keys(d.table[0]))} />
        </Box>
      )}
    </Box>
  );
}
