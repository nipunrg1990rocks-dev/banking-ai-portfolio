import { Box, Chip, Stack } from "@mui/material";
import { useState } from "react";
import { Link } from "react-router";
import type { Page } from "../api";
import { autoColumns } from "../components/autoColumns";
import { PageHeader } from "../components/common";
import ServerGrid from "../components/ServerGrid";
import { money } from "../format";

export default function Events() {
  const [type, setType] = useState("");
  const [counts, setCounts] = useState<Record<string, number>>({});
  return (
    <Box>
      <PageHeader title="Lifecycle events" subtitle="Coupons, dividends, variation margin, option expiry and exercise, swap resets and payments, repo and loan legs, margin calls: each with its posting." />
      <Stack direction="row" spacing={1} useFlexGap flexWrap="wrap" sx={{ mb: 2 }}>
        <Chip label="All" color={!type ? "primary" : "default"} onClick={() => setType("")} />
        {Object.entries(counts).sort().map(([k, v]) => <Chip key={k} label={`${k} (${v})`} color={type === k ? "primary" : "default"} onClick={() => setType(k)} data-testid={`event-type-${k}`} />)}
      </Stack>
      <ServerGrid path="/events" params={{ event_type: type || undefined }} rowId="event_id" testId="events-grid" onData={(p: Page<any>) => setCounts(p.type_counts || {})}
        columns={autoColumns(["event_date", "event_type", "trade_id", "allocation_id", "account_id", "amount", "currency", "status", "details", "event_id"], {
          trade_id: { renderCell: (p: any) => p.value && <Link to={`/trades/${p.value}`}>{p.value}</Link> }, amount: { width: 150, renderCell: (p: any) => money(p.value, p.row.currency) },
          details: { width: 360 }, event_id: { width: 260 },
        })} />
    </Box>
  );
}
