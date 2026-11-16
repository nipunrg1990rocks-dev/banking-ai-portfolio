import { Alert, Box, Button, Card, CardContent, Chip, Grid, Stack, TextField, ToggleButton, ToggleButtonGroup, Typography } from "@mui/material";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert, PageHeader, StatusChip } from "../components/common";
import { dt } from "../format";

function ApprovalCard({ a }: { a: any }) {
  const { me, can } = useAuth();
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const decide = useMutation({
    mutationFn: (approve: boolean) => api(`/approvals/${a.approval_id}/decision`, { method: "POST", body: { approve, note: note || undefined } }),
    onSuccess: () => qc.invalidateQueries(),
  });
  const own = a.maker === me?.username;
  return (
    <Card data-testid={`approval-${a.approval_id}`}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center">
          <Typography fontWeight={700}>{a.request_type.replaceAll("_", " ")}</Typography>
          <StatusChip value={a.status} />
        </Stack>
        <Typography variant="body2" color="text.secondary">{a.approval_id} · maker {a.maker} · {dt(a.created_at)}</Typography>
        <Typography variant="body2" sx={{ mt: 1 }}>
          {a.entity_type === "trade" ? <Link to={`/trades/${a.entity_id}`}>{a.entity_id}</Link> : a.entity_id}: {a.reason}
        </Typography>
        <Box component="pre" sx={{ fontSize: 12, bgcolor: "action.hover", p: 1, borderRadius: 1, overflowX: "auto", maxHeight: 160 }}>{JSON.stringify(a.payload, null, 2)}</Box>
        {a.status === "PENDING" && (
          <>
            {own && <Alert severity="warning" sx={{ mb: 1 }}>You raised this request. Maker-checker (R092) requires a different checker.</Alert>}
            <TextField size="small" fullWidth label="Decision note" value={note} onChange={(e) => setNote(e.target.value)} sx={{ mb: 1 }} slotProps={{ htmlInput: { "data-testid": "decision-note" } }} />
            <Stack direction="row" spacing={1}>
              <Button variant="contained" color="success" disabled={!can("approval.decide") && !own} onClick={() => decide.mutate(true)} data-testid="approve">Approve</Button>
              <Button variant="outlined" color="error" disabled={!can("approval.decide") && !own} onClick={() => decide.mutate(false)} data-testid="reject">Reject</Button>
            </Stack>
          </>
        )}
        {a.checker && <Chip size="small" sx={{ mt: 1 }} label={`checker ${a.checker}: ${a.decision_note || "no note"}`} />}
        <ErrorAlert error={decide.error} />
      </CardContent>
    </Card>
  );
}

export default function Approvals() {
  const [status, setStatus] = useState("PENDING");
  const q = useQuery({ queryKey: ["approvals", status], queryFn: () => api("/approvals", { params: { status: status === "ALL" ? "" : status } }) });
  return (
    <Box>
      <PageHeader title="Approvals" subtitle="Economic amendments, cancellations, force matches, SSIs, client changes and break write-offs wait here for a checker." />
      <ToggleButtonGroup size="small" exclusive value={status} onChange={(_, v) => v && setStatus(v)} sx={{ mb: 2 }}>
        {["PENDING", "APPROVED", "REJECTED", "ALL"].map((s) => <ToggleButton key={s} value={s} data-testid={`approvals-${s}`}>{s}</ToggleButton>)}
      </ToggleButtonGroup>
      <ErrorAlert error={q.error} />
      {q.data?.items?.length === 0 && <Typography color="text.secondary" data-testid="approvals-empty">Nothing here.</Typography>}
      <Grid container spacing={2}>{(q.data?.items || []).map((a: any) => <Grid key={a.approval_id} size={{ xs: 12, md: 6 }}><ApprovalCard a={a} /></Grid>)}</Grid>
    </Box>
  );
}
