import { Alert, Box, Button, Tab, Tabs } from "@mui/material";
import type { GridColDef } from "@mui/x-data-grid";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert, PageHeader, StatusChip } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { money, num } from "../format";
import { TRADE_COLUMNS } from "./Blotter";

const QUEUES = [
  { id: "matching", label: "Matching", help: "Allocated trades waiting for a matching counterparty confirmation (R082). Breaks can be force-matched with approval." },
  { id: "enrichment", label: "Enrichment", help: "Captured trades missing reference data such as an SSI (R080). Request the SSI in Reference data; approval repairs the trades." },
  { id: "clearing", label: "Clearing", help: "Confirmed exchange-traded and cleared trades waiting for the simulated CCP." },
  { id: "settlement", label: "Settlement", help: "Pending, partial and failed instructions. Receipts settle before deliveries each day." },
  { id: "failed", label: "Fails & partials", help: "Failed or partly settled instructions, aged in business days (R086). Retry runs settlement again for the business date." },
];

export default function Queues() {
  const { queue = "matching" } = useParams();
  const nav = useNavigate();
  const { can } = useAuth();
  const qc = useQueryClient();
  const [done, setDone] = useState<string | null>(null);
  const retry = useMutation({
    mutationFn: (id: string) => api(`/settlements/${id}/retry`, { method: "POST" }),
    onSuccess: (r: any) => { setDone(`${r.instruction_id}: ${r.status}`); qc.invalidateQueries(); },
  });
  const meta = QUEUES.find((q) => q.id === queue) || QUEUES[0];
  const isInstr = queue === "settlement" || queue === "failed";
  const instrCols: GridColDef[] = [
    { field: "instruction_id", headerName: "Instruction", width: 170 },
    { field: "trade_id", headerName: "Trade", width: 100, renderCell: (p) => <Link to={`/trades/${p.value}`}>{p.value}</Link> },
    { field: "status", headerName: "Status", width: 110, renderCell: (p) => <StatusChip value={p.value} /> },
    { field: "leg", headerName: "Leg", width: 110 },
    { field: "direction", headerName: "Dir", width: 90 },
    { field: "account_id", headerName: "Account", width: 90 },
    { field: "sec_qty", headerName: "Securities", width: 120, valueFormatter: (v) => num(v, 2) },
    { field: "settled_qty", headerName: "Settled qty", width: 110, valueFormatter: (v) => num(v, 2) },
    { field: "cash_amount", headerName: "Cash", width: 140, renderCell: (p) => money(p.value, p.row.currency) },
    { field: "currency", headerName: "Ccy", width: 60 },
    { field: "settle_date", headerName: "Settle date", width: 105 },
    { field: "age_bdays", headerName: "Age (bd)", width: 80 },
    { field: "fail_reason", headerName: "Reason", width: 190 },
    { field: "attempts", headerName: "Tries", width: 60 },
    ...(can("settlement.manage") ? [{ field: "retry", headerName: "", width: 90, sortable: false, renderCell: (p: any) => p.row.status !== "PENDING" || p.row.due ? <Button size="small" onClick={(e) => { e.stopPropagation(); retry.mutate(p.row.instruction_id); }} data-testid={`retry-${p.row.instruction_id}`}>Retry</Button> : null } as GridColDef] : []),
  ];
  return (
    <Box>
      <PageHeader title="Work queues" subtitle={meta.help} />
      <Tabs value={queue} onChange={(_, v) => nav(`/queues/${v}`)} variant="scrollable" sx={{ mb: 2 }}>
        {QUEUES.map((q) => <Tab key={q.id} value={q.id} label={q.label} data-testid={`queue-tab-${q.id}`} />)}
      </Tabs>
      {done && <Alert severity="info" onClose={() => setDone(null)} sx={{ mb: 1 }} data-testid="retry-result">Retried {done}</Alert>}
      <ErrorAlert error={retry.error} />
      {isInstr ? (
        <ServerGrid key={queue} path={`/queues/${queue}`} columns={instrCols} rowId="instruction_id" testId={`queue-${queue}`} />
      ) : (
        <ServerGrid key={queue} path={`/queues/${queue}`} columns={TRADE_COLUMNS.filter((c) => ["trade_id", "status", "product_type", "instrument_id", "side", "quantity", "price", "settle_ccy", "trade_date", "settlement_date", "counterparty_id", "match_status", "flags"].includes(c.field))}
          rowId="trade_id" onRowClick={(r) => nav(`/trades/${r.trade_id}`)} testId={`queue-${queue}`} />
      )}
    </Box>
  );
}
