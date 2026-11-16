import { Box, Stack, TextField } from "@mui/material";
import { useState } from "react";
import { autoColumns } from "../components/autoColumns";
import { PageHeader } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { dt } from "../format";

export default function AuditTrail() {
  const [f, setF] = useState<Record<string, string>>({});
  return (
    <Box>
      <PageHeader title="Audit trail" subtitle="Append-only: the database rejects updates and deletes on this table. Every API request carries a correlation id that ties its events together." />
      <Stack direction="row" spacing={1.5} sx={{ mb: 2 }} useFlexGap flexWrap="wrap">
        {["entity_id", "actor", "action", "correlation_id"].map((k) => <TextField key={k} size="small" label={k.replaceAll("_", " ")} value={f[k] || ""} onChange={(e) => setF({ ...f, [k]: e.target.value })} slotProps={{ htmlInput: { "data-testid": `audit-${k}` } }} />)}
      </Stack>
      <ServerGrid path="/audit" params={Object.fromEntries(Object.entries(f).filter(([, v]) => v))} rowId="audit_id" testId="audit-grid"
        columns={autoColumns(["audit_id", "ts", "business_date", "actor", "role", "action", "entity_type", "entity_id", "correlation_id", "details", "after"], { ts: { width: 170, valueFormatter: (v: any) => dt(v) }, details: { width: 300 }, after: { width: 300 }, correlation_id: { width: 190 } })} />
    </Box>
  );
}
