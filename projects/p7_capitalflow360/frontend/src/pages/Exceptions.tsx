import { Alert, Box, Button, Card, CardContent, Chip, Dialog, DialogActions, DialogContent, DialogTitle, MenuItem, Stack, TextField } from "@mui/material";
import type { GridColDef } from "@mui/x-data-grid";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type Page } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert, PageHeader, StatusChip } from "../components/common";
import ServerGrid from "../components/ServerGrid";

const TYPES = ["", "MATCH_BREAK", "MISSING_CONFIRMATION", "SSI_MISSING", "SETTLEMENT_FAIL", "PARTIAL_SETTLEMENT", "RECON_BREAK", "POTENTIAL_DUPLICATE", "LIMIT_WARNING", "MARGIN_CALL", "CUTOFF_MISSED"];
const ROLES = ["", "MIDDLE_OFFICE", "SETTLEMENTS", "RECONCILIATION", "RISK"];

export default function Exceptions() {
  const [sp, setSp] = useSearchParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [sel, setSel] = useState<any>(null);
  const [form, setForm] = useState<{ action: string; assignee?: string; note?: string }>({ action: "START" });
  const [counts, setCounts] = useState<Record<string, number>>({});
  const filters = useMemo(() => ({ status: sp.get("status") || undefined, exception_type: sp.get("exception_type") || undefined, owner_role: sp.get("owner_role") || undefined, severity: sp.get("severity") || undefined }), [sp]);
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n, { replace: true }); };
  const act = useMutation({
    mutationFn: () => api(`/exceptions/${sel.exception_id}/actions`, { method: "POST", body: form }),
    onSuccess: () => { setSel(null); qc.invalidateQueries(); },
  });
  const cols: GridColDef[] = [
    { field: "exception_id", headerName: "Exception", width: 280 },
    { field: "exception_type", headerName: "Type", width: 170 },
    { field: "severity", headerName: "Sev", width: 90, renderCell: (p) => <StatusChip value={p.value} /> },
    { field: "status", headerName: "Status", width: 120, renderCell: (p) => <StatusChip value={p.value} /> },
    { field: "owner_role", headerName: "Owner", width: 130 },
    { field: "assigned_to", headerName: "Assignee", width: 100 },
    { field: "trade_id", headerName: "Trade", width: 100, renderCell: (p) => p.value && <Link to={`/trades/${p.value}`} onClick={(e) => e.stopPropagation()}>{p.value}</Link> },
    { field: "rule_id", headerName: "Rule", width: 70 },
    { field: "age_bdays", headerName: "Age (bd)", width: 80 },
    { field: "age_bucket", headerName: "Bucket", width: 80 },
    { field: "retries", headerName: "Retries", width: 70 },
    { field: "opened_business_date", headerName: "Opened", width: 105 },
    { field: "message", headerName: "Message", width: 420 },
  ];
  return (
    <Box>
      <PageHeader title="Exceptions" subtitle="Every break has an owner role, an age in business days and a retry or resolution path. Click a row to act on it." />
      <Card sx={{ mb: 2 }}><CardContent>
        <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap" alignItems="center">
          <TextField select size="small" label="Status" value={sp.get("status") || ""} onChange={(e) => set("status", e.target.value)} sx={{ minWidth: 140 }} slotProps={{ htmlInput: { "data-testid": "exc-filter-status" } }}>{["", "OPEN", "IN_PROGRESS", "RESOLVED", "WRITTEN_OFF"].map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}</TextField>
          <TextField select size="small" label="Type" value={sp.get("exception_type") || ""} onChange={(e) => set("exception_type", e.target.value)} sx={{ minWidth: 200 }} slotProps={{ htmlInput: { "data-testid": "exc-filter-type" } }}>{TYPES.map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}</TextField>
          <TextField select size="small" label="Owner" value={sp.get("owner_role") || ""} onChange={(e) => set("owner_role", e.target.value)} sx={{ minWidth: 170 }}>{ROLES.map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}</TextField>
          {Object.entries(counts).map(([k, v]) => <Chip key={k} label={`${k}: ${v}`} size="small" variant="outlined" />)}
        </Stack>
      </CardContent></Card>
      <ServerGrid path="/exceptions" params={filters} columns={cols} rowId="exception_id" testId="exceptions-grid"
        onData={(p: Page<any>) => setCounts(p.status_counts || {})} onRowClick={(r) => { setSel(r); setForm({ action: "START" }); }} />
      <Dialog open={!!sel} onClose={() => setSel(null)} fullWidth>
        <DialogTitle>{sel?.exception_type?.replaceAll("_", " ")} · {sel?.exception_id}</DialogTitle>
        <DialogContent>
          <Alert severity="info" sx={{ mb: 2 }}>{sel?.message}</Alert>
          {!can("exception.manage") && <Alert severity="warning">Your role can view exceptions but not act on them.</Alert>}
          <Stack spacing={1.5}>
            <TextField select size="small" label="Action" value={form.action} onChange={(e) => setForm({ ...form, action: e.target.value })} slotProps={{ htmlInput: { "data-testid": "exc-action" } }}>
              {["ASSIGN", "START", "RETRY", "RESOLVE", "ESCALATE", "COMMENT"].map((a) => <MenuItem key={a} value={a}>{a}</MenuItem>)}
            </TextField>
            {form.action === "ASSIGN" && <TextField size="small" label="Assignee username" value={form.assignee || ""} onChange={(e) => setForm({ ...form, assignee: e.target.value })} slotProps={{ htmlInput: { "data-testid": "exc-assignee" } }} />}
            <TextField size="small" label="Note" multiline value={form.note || ""} onChange={(e) => setForm({ ...form, note: e.target.value })} slotProps={{ htmlInput: { "data-testid": "exc-note" } }} />
            <ErrorAlert error={act.error} />
          </Stack>
        </DialogContent>
        <DialogActions><Button onClick={() => setSel(null)}>Close</Button><Button variant="contained" disabled={!can("exception.manage") || act.isPending} onClick={() => act.mutate()} data-testid="exc-submit">Apply</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
