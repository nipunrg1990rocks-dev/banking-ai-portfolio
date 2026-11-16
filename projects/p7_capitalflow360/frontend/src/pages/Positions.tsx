import { Box, Grid, MenuItem, Stack, Tab, Tabs, TextField } from "@mui/material";
import { DataGrid } from "@mui/x-data-grid";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { ChartView } from "../components/ChartView";
import { ErrorAlert, PageHeader, Section } from "../components/common";
import { money, num } from "../format";

export default function Positions() {
  const [tab, setTab] = useState(0);
  const [account, setAccount] = useState("");
  const [projected, setProjected] = useState(false);
  const [ccy, setCcy] = useState("USD");
  const pos = useQuery({ queryKey: ["positions", account, projected], queryFn: () => api("/positions", { params: { account_id: account || undefined, projected } }) });
  const cash = useQuery({ queryKey: ["cash", account, projected], queryFn: () => api("/cash", { params: { account_id: account || undefined, projected } }) });
  const ladder = useQuery({ queryKey: ["ladder", ccy, account], queryFn: () => api("/cash/ladder", { params: { currency: ccy, account_id: account || undefined } }) });
  return (
    <Box>
      <PageHeader title="Positions & cash" subtitle="Balances come straight from the double-entry ledger. Settled = value date on or before the business date; projected includes future-dated postings." />
      <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} useFlexGap flexWrap="wrap">
        <TextField size="small" label="Account (e.g. AC0001)" value={account} onChange={(e) => setAccount(e.target.value.toUpperCase())} slotProps={{ htmlInput: { "data-testid": "pos-account" } }} />
        <TextField select size="small" label="Basis" value={projected ? "P" : "S"} onChange={(e) => setProjected(e.target.value === "P")} sx={{ minWidth: 140 }}>
          <MenuItem value="S">Settled</MenuItem><MenuItem value="P">Projected</MenuItem>
        </TextField>
      </Stack>
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}><Tab label="Securities" data-testid="tab-securities" /><Tab label="Cash" data-testid="tab-cash" /><Tab label="Cash ladder" data-testid="tab-ladder" /></Tabs>
      <ErrorAlert error={pos.error || cash.error || ladder.error} />
      {tab === 0 && <Box sx={{ height: 560 }} data-testid="positions-grid"><DataGrid density="compact" rows={pos.data?.items || []} getRowId={(r) => `${r.account_id}|${r.instrument_id}`} loading={pos.isFetching}
        columns={[{ field: "account_id", headerName: "Account", width: 110 }, { field: "instrument_id", headerName: "Instrument", width: 120 }, { field: "name", headerName: "Name", width: 280 }, { field: "quantity", headerName: "Quantity", width: 160, type: "number", valueFormatter: (v) => num(v, 4) }]} /></Box>}
      {tab === 1 && <Box sx={{ height: 560 }} data-testid="cash-grid"><DataGrid density="compact" rows={cash.data?.items || []} getRowId={(r) => `${r.account_id}|${r.currency}`} loading={cash.isFetching}
        columns={[{ field: "account_id", headerName: "Account", width: 110 }, { field: "currency", headerName: "Ccy", width: 80 }, { field: "amount", headerName: "Balance", width: 200, type: "number", renderCell: (p) => money(p.value, p.row.currency) }]} /></Box>}
      {tab === 2 && (
        <Grid container spacing={2}>
          <Grid size={{ xs: 12 }}>
            <TextField select size="small" label="Currency" value={ccy} onChange={(e) => setCcy(e.target.value)} sx={{ mb: 1, minWidth: 120 }}>{["USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF"].map((c) => <MenuItem key={c} value={c}>{c}</MenuItem>)}</TextField>
            <Section title={`Opening settled balance ${money(ladder.data?.opening, ccy)} ${ccy}`}>
              <ChartView c={{ id: "ladder", type: "line", title: `Projected ${ccy} balance by value date`, data: (ladder.data?.ladder || []).map((r: any) => ({ name: r.date, value: Number(r.balance) })) }} />
            </Section>
          </Grid>
        </Grid>
      )}
    </Box>
  );
}
