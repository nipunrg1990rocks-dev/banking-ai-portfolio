import { Alert, Box, Button, Card, CardContent, Grid, MenuItem, Tab, Tabs, TextField, Typography } from "@mui/material";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { autoColumns } from "../components/autoColumns";
import { ErrorAlert, PageHeader, Section } from "../components/common";
import ServerGrid from "../components/ServerGrid";

const PK: Record<string, string> = { clients: "client_id", accounts: "account_id", counterparties: "counterparty_id", instruments: "instrument_id", markets: "market", calendars: "calendar_code",
  conventions: "convention_id", ssis: "ssi_id", "commission-schedules": "schedule_id", "corporate-actions": "ca_id" };
const ENTITIES = ["clients", "accounts", "counterparties", "instruments", "ssis", "conventions", "markets", "calendars", "holidays", "commission-schedules", "corporate-actions", "prices", "fx-rates", "rate-fixings", "config"];

function EntityGrid({ entity }: { entity: string }) {
  const [q, setQ] = useState("");
  const [fields, setFields] = useState<string[]>([]);
  const rowId = PK[entity] || "_rid";
  return (
    <>
      <TextField size="small" label="Search" value={q} onChange={(e) => setQ(e.target.value)} sx={{ mb: 1 }} slotProps={{ htmlInput: { "data-testid": "refdata-search" } }} />
      <ServerGrid key={entity} path={`/refdata/${entity}`} params={{ q: q || undefined }} rowId={rowId} testId={`refdata-${entity}`} columns={autoColumns(fields)}
        onData={(p) => {
          if (rowId === "_rid") p.items.forEach((r: any, i: number) => (r._rid = `${p.page}-${i}-${JSON.stringify(r).length}`));
          if (p.items[0]) setFields(Object.keys(p.items[0]).filter((k) => k !== "_rid"));
        }} />
    </>
  );
}

function Requests() {
  const { can } = useAuth();
  const [s, setS] = useState<Record<string, string>>({ owner_type: "COUNTERPARTY", effective_from: "" });
  const [c, setC] = useState<Record<string, string>>({ field: "kyc_expiry_date" });
  const ssi = useMutation({ mutationFn: () => api("/refdata/ssis", { method: "POST", body: { ...s, effective_to: s.effective_to || undefined } }) });
  const cl = useMutation({ mutationFn: () => api(`/refdata/clients/${c.client_id}/changes`, { method: "POST", body: { changes: { [c.field]: c.value }, reason: c.reason } }) });
  const disabled = !can("refdata.request");
  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, md: 6 }}>
        <Section title="Request a new SSI (maker-checker)">
          {disabled && <Alert severity="info">Middle office or admin can raise reference-data requests.</Alert>}
          <Grid container spacing={1}>
            {["owner_id", "currency", "settlement_location", "safekeeping_account", "cash_account", "effective_from", "effective_to", "reason"].map((k) => (
              <Grid key={k} size={{ xs: 6 }}><TextField fullWidth size="small" label={k.replaceAll("_", " ")} type={k.startsWith("effective") ? "date" : "text"} InputLabelProps={k.startsWith("effective") ? { shrink: true } : undefined}
                value={s[k] || ""} onChange={(e) => setS({ ...s, [k]: e.target.value })} slotProps={{ htmlInput: { "data-testid": `ssi-${k}` } }} /></Grid>
            ))}
            <Grid size={{ xs: 6 }}><TextField select fullWidth size="small" label="Owner type" value={s.owner_type} onChange={(e) => setS({ ...s, owner_type: e.target.value })}>{["CLIENT", "COUNTERPARTY", "FIRM"].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField></Grid>
          </Grid>
          <Button sx={{ mt: 1 }} variant="contained" disabled={disabled} onClick={() => ssi.mutate()} data-testid="ssi-submit">Submit for approval</Button>
          <ErrorAlert error={ssi.error} />
          {ssi.data && <Alert severity="success" sx={{ mt: 1 }} data-testid="ssi-result">Approval {(ssi.data as any).approval_id} raised. When approved, trades stuck on SSI_MISSING are re-enriched automatically.</Alert>}
        </Section>
      </Grid>
      <Grid size={{ xs: 12, md: 6 }}>
        <Section title="Request a client change (maker-checker)">
          <Grid container spacing={1}>
            <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Client id" value={c.client_id || ""} onChange={(e) => setC({ ...c, client_id: e.target.value })} slotProps={{ htmlInput: { "data-testid": "client-id" } }} /></Grid>
            <Grid size={{ xs: 6 }}><TextField select fullWidth size="small" label="Field" value={c.field} onChange={(e) => setC({ ...c, field: e.target.value })}>
              {["status", "kyc_expiry_date", "kyc_review_date", "exposure_limit_usd", "risk_rating", "client_category"].map((x) => <MenuItem key={x} value={x}>{x}</MenuItem>)}</TextField></Grid>
            <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="New value" value={c.value || ""} onChange={(e) => setC({ ...c, value: e.target.value })} slotProps={{ htmlInput: { "data-testid": "client-value" } }} /></Grid>
            <Grid size={{ xs: 6 }}><TextField fullWidth size="small" label="Reason" value={c.reason || ""} onChange={(e) => setC({ ...c, reason: e.target.value })} slotProps={{ htmlInput: { "data-testid": "client-reason" } }} /></Grid>
          </Grid>
          <Button sx={{ mt: 1 }} variant="contained" disabled={disabled} onClick={() => cl.mutate()} data-testid="client-submit">Submit for approval</Button>
          <ErrorAlert error={cl.error} />
          {cl.data && <Alert severity="success" sx={{ mt: 1 }}>Approval {(cl.data as any).approval_id} raised.</Alert>}
        </Section>
      </Grid>
    </Grid>
  );
}

export default function RefData() {
  const [tab, setTab] = useState("clients");
  const conv = useQuery({ queryKey: ["ref-entities"], queryFn: () => api("/refdata") });
  return (
    <Box>
      <PageHeader title="Reference data" subtitle="Effective-dated conventions, SSIs and commission schedules decide how each trade is processed on its own date. Changes go through maker-checker." />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} variant="scrollable" sx={{ mb: 2 }}>
        {ENTITIES.map((e) => <Tab key={e} value={e} label={e.replaceAll("-", " ")} data-testid={`ref-tab-${e}`} />)}
        <Tab value="requests" label="Change requests" data-testid="ref-tab-requests" />
      </Tabs>
      {tab === "requests" ? <Requests /> : <Card><CardContent><EntityGrid entity={tab} /></CardContent></Card>}
      <Typography variant="caption" color="text.secondary">{conv.data ? `${conv.data.entities.length} entities available` : ""}</Typography>
    </Box>
  );
}
