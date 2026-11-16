import { Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Grid, MenuItem, Stack, TextField, Typography } from "@mui/material";
import type { GridColDef } from "@mui/x-data-grid";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert, KeyValues, PageHeader, Section, StatusChip } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { dt, num } from "../format";

const COLS: GridColDef[] = [
  { field: "order_id", headerName: "Order", width: 150, renderCell: (p) => <Link to={`/orders/${p.value}`}>{p.value}</Link> },
  { field: "status", headerName: "Status", width: 190, renderCell: (p) => <StatusChip value={p.value} /> },
  { field: "instrument_id", headerName: "Instrument", width: 110 },
  { field: "side", headerName: "Side", width: 80 },
  { field: "order_type", headerName: "Type", width: 80 },
  { field: "order_qty", headerName: "Qty", width: 120, valueFormatter: (v) => num(v, 2) },
  { field: "filled_qty", headerName: "Filled", width: 120, valueFormatter: (v) => num(v, 2) },
  { field: "limit_price", headerName: "Limit", width: 100, valueFormatter: (v) => num(v, 4) },
  { field: "client_id", headerName: "Client", width: 90 },
  { field: "account_id", headerName: "Account", width: 100 },
  { field: "trader_id", headerName: "Trader", width: 100 },
  { field: "channel", headerName: "Channel", width: 90 },
  { field: "order_ts_utc", headerName: "Time (UTC)", width: 170, valueFormatter: (v) => dt(v) },
];

function OrderDetail({ id }: { id: string }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["order", id], queryFn: () => api(`/orders/${id}`) });
  const [cpty, setCpty] = useState("");
  const [fill, setFill] = useState({ qty: "", price: "" });
  const book = useMutation({ mutationFn: () => api(`/orders/${id}/book`, { method: "POST", body: { counterparty_id: cpty || undefined } }), onSuccess: () => qc.invalidateQueries({ queryKey: ["order", id] }) });
  const addFill = useMutation({ mutationFn: () => api(`/orders/${id}/fills`, { method: "POST", body: [fill] }), onSuccess: () => qc.invalidateQueries({ queryKey: ["order", id] }) });
  const cancel = useMutation({ mutationFn: () => api(`/orders/${id}/cancel`, { method: "POST" }), onSuccess: () => qc.invalidateQueries({ queryKey: ["order", id] }) });
  if (!q.data) return <ErrorAlert error={q.error} />;
  const o = q.data.order;
  return (
    <Box>
      <PageHeader title={`Order ${o.order_id}`} subtitle={`${o.side} ${num(o.order_qty, 2)} ${o.instrument_id} · filled ${num(q.data.filled_qty, 2)} · VWAP ${q.data.vwap ?? "n/a"}`} actions={<StatusChip value={o.status} size="medium" />} />
      {o.pretrade_result?.issues?.length > 0 && <Alert severity="warning" sx={{ mb: 2 }} data-testid="pretrade-issues">Pre-trade checks: {o.pretrade_result.issues.map((i: any) => `${i.rule_id} ${i.code}`).join("; ")}</Alert>}
      <Section title="Executions (simulated venue)">
        <KeyValues data={Object.fromEntries(q.data.executions.map((e: any) => [e.exec_id, `${num(e.exec_qty, 2)} @ ${num(e.exec_price, 6)} on ${e.execution_venue} · ${dt(e.exec_ts_utc)}`]))} />
      </Section>
      {q.data.trades.length > 0 && <Section title="Booked trade">{q.data.trades.map((t: any) => <Link key={t.trade_id} to={`/trades/${t.trade_id}`} data-testid="order-trade-link">{t.trade_id} ({t.status})</Link>)}</Section>}
      {can("order.manage") && !["REJECTED", "CANCELLED", "FILLED", "PARTIALLY_FILLED_DONE"].includes(o.status) && (
        <Section title="Receive a fill from the venue">
          <Stack direction="row" spacing={1}>
            <TextField size="small" label="Qty" value={fill.qty} onChange={(e) => setFill({ ...fill, qty: e.target.value })} slotProps={{ htmlInput: { "data-testid": "fill-qty" } }} />
            <TextField size="small" label="Price" value={fill.price} onChange={(e) => setFill({ ...fill, price: e.target.value })} slotProps={{ htmlInput: { "data-testid": "fill-price" } }} />
            <Button variant="outlined" onClick={() => addFill.mutate()} data-testid="add-fill">Add fill</Button>
            <Button color="error" onClick={() => cancel.mutate()} data-testid="cancel-order">Cancel remainder</Button>
          </Stack>
          <ErrorAlert error={addFill.error || cancel.error} />
        </Section>
      )}
      {can("trade.book") && q.data.executions.length > 0 && q.data.trades.length === 0 && (
        <Section title="Book trade from fills (quantity = sum of fills, price = VWAP, R026)">
          <Stack direction="row" spacing={1}>
            <TextField size="small" label="Counterparty (optional)" value={cpty} onChange={(e) => setCpty(e.target.value)} slotProps={{ htmlInput: { "data-testid": "order-book-cpty" } }} />
            <Button variant="contained" onClick={() => book.mutate()} disabled={book.isPending} data-testid="order-book">Book trade</Button>
          </Stack>
          <ErrorAlert error={book.error} />
          {book.data && <Alert severity="success" sx={{ mt: 1 }}>Booked {(book.data as any).trade.trade_id}</Alert>}
        </Section>
      )}
    </Box>
  );
}

export default function Orders() {
  const { id } = useParams();
  const { can } = useAuth();
  const nav = useNavigate();
  const [open, setOpen] = useState(false);
  const [f, setF] = useState<Record<string, string>>({ side: "BUY", order_type: "MARKET", product_type: "EQUITY" });
  const [status, setStatus] = useState("");
  const create = useMutation({
    mutationFn: () => api("/orders", { method: "POST", body: { ...f, limit_price: f.order_type === "LIMIT" ? f.limit_price : undefined } }),
    onSuccess: (r: any) => { setOpen(false); nav(`/orders/${r.order.order_id}`); },
  });
  if (id) return <OrderDetail id={id} />;
  return (
    <Box>
      <PageHeader title="Orders & fills" subtitle="Orders pass pre-trade checks (client, KYC, product permission, limits, lot size, price collar), then a simulated venue fills them, possibly in parts."
        actions={can("order.manage") && <Button variant="contained" onClick={() => setOpen(true)} data-testid="new-order">New order</Button>} />
      <TextField select size="small" label="Status" value={status} onChange={(e) => setStatus(e.target.value)} sx={{ mb: 2, minWidth: 220 }}>
        {["", "NEW", "PARTIALLY_FILLED", "FILLED", "PARTIALLY_FILLED_DONE", "REJECTED", "CANCELLED"].map((s) => <MenuItem key={s} value={s}>{s || "All"}</MenuItem>)}
      </TextField>
      <ServerGrid path="/orders" params={{ status: status || undefined }} columns={COLS} rowId="order_id" onRowClick={(r) => nav(`/orders/${r.order_id}`)} testId="orders-grid" />
      <Dialog open={open} onClose={() => setOpen(false)} fullWidth>
        <DialogTitle>New order</DialogTitle>
        <DialogContent>
          <Grid container spacing={1.5} sx={{ mt: 0.5 }}>
            {["client_id", "account_id", "instrument_id", "order_qty", "limit_price", "counterparty_id"].map((k) => (
              <Grid key={k} size={{ xs: 12, sm: 6 }}><TextField fullWidth size="small" label={k.replaceAll("_", " ")} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} slotProps={{ htmlInput: { "data-testid": `order-${k}` } }} /></Grid>
            ))}
            <Grid size={{ xs: 4 }}><TextField select fullWidth size="small" label="Product" value={f.product_type} onChange={(e) => setF({ ...f, product_type: e.target.value })}>{["EQUITY", "ETF", "FUTURE", "OPTION"].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}</TextField></Grid>
            <Grid size={{ xs: 4 }}><TextField select fullWidth size="small" label="Side" value={f.side} onChange={(e) => setF({ ...f, side: e.target.value })}>{["BUY", "SELL"].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}</TextField></Grid>
            <Grid size={{ xs: 4 }}><TextField select fullWidth size="small" label="Type" value={f.order_type} onChange={(e) => setF({ ...f, order_type: e.target.value })}>{["MARKET", "LIMIT"].map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}</TextField></Grid>
          </Grid>
          <Typography variant="caption" color="text.secondary">Market orders fill at the reference close in two clips (60/40).</Typography>
          <ErrorAlert error={create.error} />
        </DialogContent>
        <DialogActions><Button onClick={() => setOpen(false)}>Close</Button><Button variant="contained" onClick={() => create.mutate()} data-testid="submit-order">Submit</Button></DialogActions>
      </Dialog>
    </Box>
  );
}
