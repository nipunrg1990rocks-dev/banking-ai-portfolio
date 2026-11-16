import { Box, Button, Card, CardContent, Chip, MenuItem, Stack, TextField } from "@mui/material";
import Download from "@mui/icons-material/Download";
import type { GridColDef, GridColumnVisibilityModel } from "@mui/x-data-grid";
import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { download } from "../api";
import { ErrorAlert, PageHeader, StatusChip } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { money, num } from "../format";

const GROUPS = ["", "EQUITY", "BOND", "FX", "LISTED_DERIV", "IRS", "FINANCING"];
const STATUSES = ["", "CAPTURED", "ENRICHED", "ALLOCATED", "CONFIRMED", "CLEARED", "INSTRUCTED", "PARTIALLY_SETTLED", "SETTLEMENT_FAILED", "SETTLED", "OPEN", "CLOSED", "EXPIRED", "EXERCISED", "MATURED", "CANCELLED"];
const CCYS = ["", "USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"];
const FILTER_KEYS = ["q", "status", "product_group", "product_type", "currency", "book", "counterparty_id", "client_id", "trade_date_from", "trade_date_to", "flag"];

export const TRADE_COLUMNS: GridColDef[] = [
  { field: "trade_id", headerName: "Trade", width: 110, renderCell: (p) => <Link to={`/trades/${p.value}`} data-testid={`trade-link-${p.value}`}>{p.value}</Link> },
  { field: "status", headerName: "Status", width: 150, renderCell: (p) => <StatusChip value={p.value} /> },
  { field: "product_type", headerName: "Product", width: 120 },
  { field: "product_group", headerName: "Group", width: 110 },
  { field: "instrument_id", headerName: "Instrument", width: 110 },
  { field: "side", headerName: "Side", width: 120 },
  { field: "quantity", headerName: "Quantity", width: 130, type: "number", valueFormatter: (v) => num(v, 2) },
  { field: "price", headerName: "Price", width: 110, type: "number", valueFormatter: (v) => num(v, 6) },
  { field: "settle_ccy", headerName: "Ccy", width: 70 },
  { field: "net_amount", headerName: "Net amount", width: 150, type: "number", renderCell: (p) => money(p.value, p.row.settle_ccy) },
  { field: "usd_equivalent", headerName: "USD exposure", width: 140, type: "number", valueFormatter: (v) => money(v, "USD") },
  { field: "trade_date", headerName: "Trade date", width: 110 },
  { field: "settlement_date", headerName: "Settle date", width: 110 },
  { field: "client_id", headerName: "Client", width: 90 },
  { field: "account_id", headerName: "Account", width: 100 },
  { field: "counterparty_id", headerName: "Cpty", width: 100 },
  { field: "book", headerName: "Book", width: 110 },
  { field: "trader_id", headerName: "Trader", width: 100 },
  { field: "match_status", headerName: "Match", width: 150 },
  { field: "version", headerName: "Ver", width: 60 },
  { field: "flags", headerName: "Flags", width: 200, sortable: false, renderCell: (p) => (p.value || []).map((f: string) => <Chip key={f} size="small" label={f} sx={{ mr: 0.5 }} />) },
  { field: "source_trade_ref", headerName: "Source ref", width: 170 },
  { field: "market", headerName: "Market", width: 80 },
];

export default function Blotter() {
  const [sp, setSp] = useSearchParams();
  const nav = useNavigate();
  const [err, setErr] = useState<unknown>(null);
  const [vis, setVis] = useState<GridColumnVisibilityModel>({ product_group: false, trader_id: false, source_trade_ref: false, market: false, version: false, account_id: false });
  const filters = useMemo(() => Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) || undefined])), [sp]);
  const set = (k: string, v: string) => {
    const n = new URLSearchParams(sp);
    if (v) n.set(k, v);
    else n.delete(k);
    setSp(n, { replace: true });
  };
  const visibleCols = TRADE_COLUMNS.map((c) => c.field).filter((f) => vis[f] !== false).join(",");
  const exp = async (format: "csv" | "xlsx") => {
    setErr(null);
    try {
      await download("/trades/export", { ...filters, format, columns: visibleCols }, `cf360_blotter.${format}`);
    } catch (e) {
      setErr(e);
    }
  };
  const field = (k: string, label: string, opts?: string[], w = 150) => (
    <TextField key={k} select={!!opts} size="small" label={label} value={sp.get(k) || ""} onChange={(e) => set(k, e.target.value)} sx={{ minWidth: w }}
      slotProps={{ htmlInput: { "data-testid": `filter-${k}` } }} type={k.includes("date") ? "date" : "text"} InputLabelProps={k.includes("date") ? { shrink: true } : undefined}>
      {opts?.map((o) => <MenuItem key={o} value={o}>{o || "All"}</MenuItem>)}
    </TextField>
  );
  return (
    <Box>
      <PageHeader title="Trade blotter" subtitle="Search, filter, sort and page on the server. Hide or show columns from any column menu; exports use the visible columns and current filters."
        actions={<>
          <Button startIcon={<Download />} variant="outlined" onClick={() => exp("csv")} data-testid="export-csv">CSV</Button>
          <Button startIcon={<Download />} variant="outlined" onClick={() => exp("xlsx")} data-testid="export-xlsx">Excel</Button>
        </>} />
      <ErrorAlert error={err} />
      <Card sx={{ mb: 2 }}>
        <CardContent>
          <Stack direction="row" spacing={1.5} useFlexGap flexWrap="wrap">
            {field("q", "Search id / ref / instrument", undefined, 240)}
            {field("status", "Status", STATUSES)}
            {field("product_group", "Product group", GROUPS)}
            {field("currency", "Settle ccy", CCYS, 110)}
            {field("book", "Book", undefined, 120)}
            {field("counterparty_id", "Counterparty", undefined, 120)}
            {field("client_id", "Client", undefined, 100)}
            {field("trade_date_from", "Trade date from")}
            {field("trade_date_to", "Trade date to")}
            {sp.toString() && <Button onClick={() => setSp(new URLSearchParams(), { replace: true })} data-testid="clear-filters">Clear</Button>}
          </Stack>
        </CardContent>
      </Card>
      <ServerGrid path="/trades" params={filters} columns={TRADE_COLUMNS} rowId="trade_id" serverSort defaultSort="-trade_date" testId="blotter-grid"
        columnVisibility={vis} onColumnVisibilityChange={setVis} onRowClick={(r) => nav(`/trades/${r.trade_id}`)} height="calc(100vh - 330px)" />
    </Box>
  );
}
