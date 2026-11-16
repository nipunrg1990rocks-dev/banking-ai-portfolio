import { Alert, Box, Button, Card, CardContent, Dialog, DialogActions, DialogContent, DialogTitle, Grid, TextField, Typography } from "@mui/material";
import { DataGrid } from "@mui/x-data-grid";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { autoColumns } from "../components/autoColumns";
import { ErrorAlert, PageHeader } from "../components/common";
import { num } from "../format";

export default function Recon() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const runs = useQuery({ queryKey: ["recon-runs"], queryFn: () => api("/recon/runs") });
  const breaks = useQuery({ queryKey: ["recon-breaks"], queryFn: () => api("/recon/breaks") });
  const [sel, setSel] = useState<any>(null);
  const [note, setNote] = useState("");
  const run = useMutation({ mutationFn: () => api("/recon/run", { method: "POST" }), onSuccess: () => qc.invalidateQueries() });
  const wo = useMutation({ mutationFn: () => api(`/recon/breaks/${sel.break_id}/write-off`, { method: "POST", body: { note } }), onSuccess: () => { setSel(null); qc.invalidateQueries(); } });
  return (
    <Box>
      <PageHeader title="Reconciliation" subtitle="Internal ledger positions and cash compared with the simulated custodian statement (R096). Cash breaks within tolerance count as matched."
        actions={can("recon.manage") && <Button variant="contained" onClick={() => run.mutate()} disabled={run.isPending} data-testid="run-recon">Run reconciliation</Button>} />
      {run.data && <Alert severity="success" sx={{ mb: 2 }} data-testid="recon-result">Reconciled statement {(run.data as any).statement_date}: {JSON.stringify((run.data as any).summary)}</Alert>}
      <ErrorAlert error={run.error || runs.error} />
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {(runs.data?.items || []).slice(0, 4).map((r: any) => (
          <Grid key={r.run_id} size={{ xs: 12, sm: 6, md: 3 }}>
            <Card data-testid={`recon-run-${r.recon_type}`}><CardContent>
              <Typography variant="caption" color="text.secondary">{r.recon_type} · {r.statement_date}</Typography>
              <Typography variant="h5">{r.breaks} breaks</Typography>
              <Typography variant="body2" color="text.secondary">{r.compared} compared · {r.matched} matched · {r.within_tolerance} within tolerance</Typography>
            </CardContent></Card>
          </Grid>
        ))}
      </Grid>
      <Box sx={{ height: 480 }} data-testid="breaks-grid">
        <DataGrid density="compact" rows={breaks.data?.items || []} getRowId={(r) => r.break_id} loading={breaks.isFetching} onRowClick={(p) => setSel(p.row)}
          columns={autoColumns(["break_id", "recon_type", "break_type", "status", "account_id", "key", "internal_value", "external_value", "difference", "statement_date"],
            { break_id: { width: 300 }, internal_value: { valueFormatter: (v) => num(v, 4) }, external_value: { valueFormatter: (v) => num(v, 4) }, difference: { valueFormatter: (v) => num(v, 4) } })} />
      </Box>
      <Dialog open={!!sel} onClose={() => setSel(null)} fullWidth>
        <DialogTitle>Break {sel?.break_id}</DialogTitle>
        <DialogContent>
          <Typography variant="body2" sx={{ mb: 2 }}>Internal {sel?.internal_value ?? "missing"} vs custodian {sel?.external_value ?? "missing"} (difference {sel?.difference}).</Typography>
          <TextField fullWidth size="small" label="Write-off justification" value={note} onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { "data-testid": "writeoff-note" } }} />
          <Typography variant="caption" color="text.secondary">A write-off needs a checker's approval.</Typography>
          <ErrorAlert error={wo.error} />
        </DialogContent>
        <DialogActions><Button onClick={() => setSel(null)}>Close</Button><Button variant="contained" disabled={!can("recon.manage") || sel?.status !== "OPEN"} onClick={() => wo.mutate()} data-testid="request-writeoff">Request write-off</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
