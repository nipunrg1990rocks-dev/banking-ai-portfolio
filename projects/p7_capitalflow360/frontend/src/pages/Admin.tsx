import { Alert, Box, Button, Chip, Grid, Stack, Tab, Tabs, TextField, Typography } from "@mui/material";
import { DataGrid } from "@mui/x-data-grid";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { autoColumns } from "../components/autoColumns";
import { ErrorAlert, PageHeader, Section } from "../components/common";
import ServerGrid from "../components/ServerGrid";

const TABS = ["processing", "defects", "users", "rejections", "interfaces"];

function Processing() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const clock = useQuery({ queryKey: ["clock"], queryFn: () => api("/admin/clock") });
  const runs = useQuery({ queryKey: ["runs"], queryFn: () => api("/admin/processing-runs") });
  const [d, setD] = useState("");
  const proc = useMutation({ mutationFn: () => api("/admin/process", { method: "POST", body: { business_date: d || undefined } }), onSuccess: () => qc.invalidateQueries() });
  const replay = useMutation({ mutationFn: () => api("/admin/replay", { method: "POST", body: { business_date: d || undefined } }) });
  const setClock = useMutation({ mutationFn: () => api("/admin/clock", { method: "PUT", body: { business_date: d } }), onSuccess: () => qc.invalidateQueries() });
  const r: any = replay.data;
  return (
    <>
      <Section title="Business clock and end-of-day processing">
        <Typography variant="body2" sx={{ mb: 1 }} data-testid="clock">Business date {clock.data?.business_date} · last processed {clock.data?.last_processed}</Typography>
        <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap">
          <TextField size="small" type="date" label="Date" InputLabelProps={{ shrink: true }} value={d} onChange={(e) => setD(e.target.value)} slotProps={{ htmlInput: { "data-testid": "admin-date" } }} />
          <Button variant="contained" disabled={!can("process.run")} onClick={() => proc.mutate()} data-testid="run-eod">Process through date</Button>
          <Button variant="outlined" disabled={!can("process.run")} onClick={() => replay.mutate()} data-testid="replay">Replay day (idempotency check)</Button>
          <Button disabled={!can("admin") || !d} onClick={() => setClock.mutate()} data-testid="set-clock">Set clock only</Button>
        </Stack>
        <ErrorAlert error={proc.error || replay.error || setClock.error} />
        {proc.data && <Alert severity="success" sx={{ mt: 1 }}>Processed {(proc.data as any).days.length} day(s) through {(proc.data as any).through}</Alert>}
        {r && <Alert severity={r.idempotent ? "success" : "error"} sx={{ mt: 1 }} data-testid="replay-result">Replayed {r.business_date}: ledger rows {r.ledger_rows_before} → {r.ledger_rows_after} ({r.new_postings} new). {r.idempotent ? "Idempotent." : "NOT idempotent."}</Alert>}
      </Section>
      <Box sx={{ height: 420 }}>
        <DataGrid density="compact" rows={runs.data?.items || []} getRowId={(x) => x.run_id} columns={autoColumns(["run_id", "business_date", "status", "started_at", "finished_at", "stats"], { stats: { width: 420 }, run_id: { width: 200 } })} />
      </Box>
    </>
  );
}

export default function Admin() {
  const [sp, setSp] = useSearchParams();
  const tab = sp.get("tab") || "processing";
  const defects = useQuery({ queryKey: ["defects"], queryFn: () => api("/admin/defects") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api("/admin/users"), enabled: tab === "users" });
  return (
    <Box>
      <PageHeader title="Admin" subtitle="Clock, end-of-day processing, defect switches, users, rejected bookings and simulated interface messages." />
      <Tabs value={tab} onChange={(_, v) => setSp({ tab: v })} variant="scrollable" sx={{ mb: 2 }}>{TABS.map((t) => <Tab key={t} value={t} label={t} data-testid={`admin-tab-${t}`} />)}</Tabs>
      {tab === "processing" && <Processing />}
      {tab === "defects" && (
        <Section title="Injected defects (for test-suite demonstrations)">
          <Alert severity="info" sx={{ mb: 1 }}>{defects.data?.note}</Alert>
          <Grid container spacing={1}>
            {Object.entries(defects.data?.known || {}).map(([k, v]) => (
              <Grid key={k} size={{ xs: 12, md: 6 }}>
                <Stack direction="row" spacing={1} alignItems="center"><Chip label={k} color={defects.data?.enabled?.includes(k) ? "error" : "default"} data-testid={`defect-${k}`} /><Typography variant="body2">{String(v)}</Typography></Stack>
              </Grid>
            ))}
          </Grid>
        </Section>
      )}
      {tab === "users" && (users.error ? <ErrorAlert error={users.error} /> : <Box sx={{ height: 420 }}><DataGrid density="compact" rows={users.data?.items || []} getRowId={(x) => x.username} columns={autoColumns(["username", "display_name", "role", "active"])} /></Box>)}
      {tab === "rejections" && <ServerGrid path="/admin/rejections" rowId="id" testId="rejections-grid" columns={autoColumns(["id", "source_trade_ref", "ingest_batch", "errors", "created_at"], { errors: { width: 600 } })} />}
      {tab === "interfaces" && <ServerGrid path="/interfaces/messages" rowId="message_id" testId="interfaces-grid" columns={autoColumns(["message_id", "system", "direction", "message_type", "sequence_no", "status", "ref_id", "business_date", "payload"], { message_id: { width: 240 }, payload: { width: 360 } })} />}
    </Box>
  );
}
