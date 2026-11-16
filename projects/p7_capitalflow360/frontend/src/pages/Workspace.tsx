import { Box, Card, CardActionArea, CardContent, Grid, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ChartView } from "../components/ChartView";
import { KpiCard, PageHeader } from "../components/common";

type Tile = { label: string; to: string; count: () => Promise<number>; hint: string };

const total = (path: string, params: Record<string, any> = {}) => () => api(path, { params: { ...params, page_size: 1 } }).then((r: any) => r.total ?? r.items?.length ?? 0);
const count = (path: string, params: Record<string, any> = {}) => () => api(path, { params }).then((r: any) => r.items.length);

const TILES: Record<string, Tile[]> = {
  TRADER: [
    { label: "Open orders", to: "/orders", count: total("/orders", { status: "PARTIALLY_FILLED" }), hint: "Partially filled orders to complete or book" },
    { label: "My potential duplicates", to: "/exceptions?exception_type=POTENTIAL_DUPLICATE", count: total("/exceptions", { exception_type: "POTENTIAL_DUPLICATE", status: "OPEN" }), hint: "R029 review" },
    { label: "Trades booked today", to: "/blotter", count: total("/trades"), hint: "All trades in the blotter" },
  ],
  MIDDLE_OFFICE: [
    { label: "Matching queue", to: "/queues/matching", count: total("/queues/matching"), hint: "Allocated, awaiting a matched confirmation" },
    { label: "Enrichment queue", to: "/queues/enrichment", count: total("/queues/enrichment"), hint: "Missing SSI or reference data" },
    { label: "My open exceptions", to: "/exceptions?owner_role=MIDDLE_OFFICE&status=OPEN", count: total("/exceptions", { owner_role: "MIDDLE_OFFICE", status: "OPEN" }), hint: "Match breaks, missing confirmations, duplicates" },
  ],
  SETTLEMENTS: [
    { label: "Settlement queue", to: "/queues/settlement", count: total("/queues/settlement"), hint: "Pending, partial and failed instructions" },
    { label: "Fails and partials", to: "/queues/failed", count: total("/queues/failed"), hint: "Aged in business days (R086)" },
    { label: "My open exceptions", to: "/exceptions?owner_role=SETTLEMENTS&status=OPEN", count: total("/exceptions", { owner_role: "SETTLEMENTS", status: "OPEN" }), hint: "Retry or resolve" },
  ],
  RECONCILIATION: [
    { label: "Open breaks", to: "/recon", count: count("/recon/breaks", { status: "OPEN" }), hint: "Position and cash breaks vs custodian" },
    { label: "My open exceptions", to: "/exceptions?owner_role=RECONCILIATION&status=OPEN", count: total("/exceptions", { owner_role: "RECONCILIATION", status: "OPEN" }), hint: "Investigate and write off with approval" },
  ],
  RISK: [
    { label: "Open exceptions (all)", to: "/exceptions?status=OPEN", count: total("/exceptions", { status: "OPEN" }), hint: "Firm-wide control view" },
    { label: "Margin calls", to: "/exceptions?exception_type=MARGIN_CALL", count: total("/exceptions", { exception_type: "MARGIN_CALL", status: "OPEN" }), hint: "Repo collateral deficits above MTA (R072)" },
    { label: "Pending approvals", to: "/approvals", count: count("/approvals"), hint: "Maker-checker backlog" },
  ],
  CHECKER: [{ label: "Pending approvals", to: "/approvals", count: count("/approvals"), hint: "Amendments, cancels, SSIs, write-offs" }],
  ADMIN: [
    { label: "Processing runs", to: "/admin", count: () => api("/admin/processing-runs").then((r: any) => r.items.length), hint: "End-of-day runs" },
    { label: "Test runs", to: "/test-runs", count: () => api("/test-runs").then((r: any) => r.items.length), hint: "Imported Playwright results" },
  ],
};

function TileCard({ t }: { t: Tile }) {
  const q = useQuery({ queryKey: ["tile", t.label, t.to], queryFn: t.count });
  return (
    <Card sx={{ height: "100%" }}>
      <CardActionArea component={Link} to={t.to} sx={{ height: "100%" }} data-testid={`tile-${t.label.replaceAll(" ", "-").toLowerCase()}`}>
        <CardContent>
          <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase" }}>{t.label}</Typography>
          <Typography variant="h4" fontWeight={800}>{q.isLoading ? "…" : q.data}</Typography>
          <Typography variant="body2" color="text.secondary">{t.hint}</Typography>
        </CardContent>
      </CardActionArea>
    </Card>
  );
}

export default function Workspace() {
  const { me } = useAuth();
  const ops = useQuery({ queryKey: ["dash", "operations"], queryFn: () => api("/dashboards/operations") });
  const tiles = TILES[me?.role || ""] || [];
  return (
    <Box>
      <PageHeader title={`Good day, ${me?.display_name?.split(" ")[0]}`} subtitle={`${me?.role?.replaceAll("_", " ")} workspace. Tiles open your queues; KPI cards drill into the underlying records.`} />
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {tiles.map((t) => <Grid key={t.label} size={{ xs: 12, sm: 6, md: 4 }}><TileCard t={t} /></Grid>)}
      </Grid>
      <Typography variant="subtitle1" fontWeight={700} sx={{ mb: 1 }}>Operations at a glance</Typography>
      <Grid container spacing={2} sx={{ mb: 2 }}>
        {(ops.data?.kpis || []).slice(0, 8).map((k: any) => <Grid key={k.id} size={{ xs: 6, md: 3 }}><KpiCard k={k} /></Grid>)}
      </Grid>
      <Grid container spacing={2}>
        {(ops.data?.charts || []).slice(0, 2).map((c: any) => <Grid key={c.id} size={{ xs: 12, md: 6 }}><ChartView c={c} /></Grid>)}
      </Grid>
    </Box>
  );
}
