import { Box, Card, CardActionArea, CardContent, Chip, Grid, MenuItem, Stack, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useSearchParams } from "react-router";
import { api } from "../api";
import { autoColumns } from "../components/autoColumns";
import { ErrorAlert, PageHeader } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { dt } from "../format";

export default function TestRuns() {
  const [sp] = useSearchParams();
  const runs = useQuery({ queryKey: ["test-runs"], queryFn: () => api("/test-runs") });
  const [run, setRun] = useState<string>(sp.get("run_id") || "");
  const [status, setStatus] = useState("");
  const [project, setProject] = useState("");
  const [q, setQ] = useState("");
  const active = run || runs.data?.items?.[0]?.run_id;
  return (
    <Box>
      <PageHeader title="Test runs" subtitle="Executed Playwright results imported from the JSON reporter. Only real executions appear here; nothing is generated for display." />
      <ErrorAlert error={runs.error} />
      {runs.data?.items?.length === 0 && <Typography color="text.secondary" data-testid="no-runs">No runs imported yet.</Typography>}
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {(runs.data?.items || []).map((r: any) => (
          <Grid key={r.run_id} size={{ xs: 12, sm: 6, md: 4 }}>
            <Card sx={{ borderColor: r.run_id === active ? "primary.main" : undefined, borderWidth: r.run_id === active ? 2 : 1 }}>
              <CardActionArea onClick={() => setRun(r.run_id)} data-testid={`run-${r.run_id}`}>
                <CardContent>
                  <Typography fontWeight={700}>{r.label}</Typography>
                  <Typography variant="caption" color="text.secondary">{r.run_id} · {r.environment} · {dt(r.started_at)}</Typography>
                  <Stack direction="row" spacing={0.5} useFlexGap flexWrap="wrap" sx={{ mt: 1 }}>
                    {Object.entries(r.summary || {}).filter(([, v]) => typeof v === "number").map(([k, v]) => <Chip key={k} size="small" label={`${k}: ${v}`} />)}
                    {r.defects_enabled && <Chip size="small" color="error" label={`defects ${r.defects_enabled}`} />}
                  </Stack>
                </CardContent>
              </CardActionArea>
            </Card>
          </Grid>
        ))}
      </Grid>
      {active && (
        <>
          <Stack direction="row" spacing={1.5} sx={{ mb: 1 }} useFlexGap flexWrap="wrap">
            <TextField select size="small" label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ minWidth: 130 }}>{["", "passed", "failed", "flaky", "skipped"].map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}</TextField>
            <TextField select size="small" label="Project" value={project} onChange={(e) => setProject(e.target.value)} sx={{ minWidth: 130 }}>{["", "api", "chromium", "firefox", "webkit"].map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}</TextField>
            <TextField size="small" label="Search title / case / scenario" value={q} onChange={(e) => setQ(e.target.value)} />
          </Stack>
          <ServerGrid path={`/test-runs/${active}/results`} params={{ status: status || undefined, project: project || undefined, q: q || undefined }} rowId="id" testId="results-grid"
            columns={autoColumns(["project", "status", "scenario_id", "case_id", "title", "rule_ids", "duration_ms", "retries", "defect_id", "error"], { title: { width: 420 }, error: { width: 400 } })} />
        </>
      )}
    </Box>
  );
}
