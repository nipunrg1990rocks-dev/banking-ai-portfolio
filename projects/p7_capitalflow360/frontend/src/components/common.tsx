import { Alert, Box, Card, CardContent, Chip, Stack, Tooltip, Typography, type ChipProps } from "@mui/material";
import InfoOutlined from "@mui/icons-material/InfoOutlined";
import type { ReactNode } from "react";
import { useNavigate } from "react-router";
import { ApiError } from "../api";
import { kpiValue } from "../format";

const STATUS_COLOR: Record<string, ChipProps["color"]> = {
  SETTLED: "success", CLOSED: "success", MATURED: "success", EXPIRED: "default", EXERCISED: "success", APPROVED: "success",
  RESOLVED: "success", PROCESSED: "success", MATCHED: "success", FILLED: "success", passed: "success", COMPLETED: "success",
  OPEN: "info", CONFIRMED: "info", CLEARED: "info", INSTRUCTED: "info", ALLOCATED: "warning", ENRICHED: "default", CAPTURED: "default",
  PENDING: "warning", PARTIAL: "warning", PARTIALLY_SETTLED: "warning", IN_PROGRESS: "warning", PARKED: "warning", PARTIALLY_FILLED: "warning",
  FAILED: "error", SETTLEMENT_FAILED: "error", REJECTED: "error", CANCELLED: "default", failed: "error", HIGH: "error", MEDIUM: "warning",
  LOW: "default", WRITTEN_OFF: "default", IGNORED_DUPLICATE: "default", flaky: "warning", skipped: "default",
};

export function StatusChip({ value, size = "small" }: { value?: string | null; size?: "small" | "medium" }) {
  if (!value) return null;
  return <Chip size={size} label={value.replaceAll("_", " ")} color={STATUS_COLOR[value] || "default"} variant={STATUS_COLOR[value] ? "filled" : "outlined"} data-testid={`status-${value}`} />;
}

export function ErrorAlert({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as ApiError;
  const body = e?.body;
  const issues = (body?.details as any)?.issues as { rule_id: string; code: string; message: string }[] | undefined;
  return (
    <Alert severity="error" sx={{ my: 1 }} data-testid="error-alert">
      <strong>{body?.code || "Error"}</strong>: {body?.message || String(e?.message || e)}
      {issues && (
        <Box component="ul" sx={{ m: 0, pl: 2 }}>
          {issues.map((i, k) => (
            <li key={k} data-testid="error-issue">
              {i.rule_id} {i.code}: {i.message}
            </li>
          ))}
        </Box>
      )}
      {body?.correlation_id && <Typography variant="caption" display="block">Correlation id: {body.correlation_id}</Typography>}
    </Alert>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <Stack direction={{ xs: "column", sm: "row" }} justifyContent="space-between" alignItems={{ sm: "center" }} spacing={1} sx={{ mb: 2 }}>
      <Box>
        <Typography variant="h5" data-testid="page-title">{title}</Typography>
        {subtitle && <Typography variant="body2" color="text.secondary">{subtitle}</Typography>}
      </Box>
      {actions && <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>{actions}</Stack>}
    </Stack>
  );
}

export type Kpi = { id: string; label: string; value: any; unit?: string; definition: string; numerator?: any; denominator?: any; population?: any; drill?: { kind: string; filters: Record<string, any> } | null; format?: string | null };

export function drillPath(kind: string, filters: Record<string, any> = {}): string {
  const base: Record<string, string> = { trades: "/blotter", exceptions: "/exceptions", settlements: "/queues/settlement", approvals: "/approvals",
    recon: "/recon", rejections: "/admin?tab=rejections", events: "/events", tests: "/test-runs" };
  const p = new URLSearchParams();
  Object.entries(filters).forEach(([k, v]) => v !== null && v !== undefined && p.append(k, String(v)));
  const path = base[kind] || "/";
  const q = p.toString();
  return q ? `${path}${path.includes("?") ? "&" : "?"}${q}` : path;
}

export function KpiCard({ k }: { k: Kpi }) {
  const nav = useNavigate();
  const tip = [k.definition, k.numerator != null && k.denominator != null ? `= ${k.numerator} / ${k.denominator}` : null,
    k.population ? `Population: ${typeof k.population === "string" ? k.population : JSON.stringify(k.population)}` : null].filter(Boolean).join("  ");
  return (
    <Card
      data-testid={`kpi-${k.id}`}
      sx={{ height: "100%", cursor: k.drill ? "pointer" : "default", transition: "box-shadow .15s", "&:hover": k.drill ? { boxShadow: 4 } : {} }}
      onClick={() => k.drill && nav(drillPath(k.drill.kind, k.drill.filters))}
    >
      <CardContent sx={{ p: 2, "&:last-child": { pb: 2 } }}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Typography variant="caption" color="text.secondary" sx={{ textTransform: "uppercase", letterSpacing: 0.4 }}>{k.label}</Typography>
          <Tooltip title={tip}><InfoOutlined fontSize="inherit" color="disabled" data-testid={`kpi-def-${k.id}`} /></Tooltip>
        </Stack>
        <Typography variant="h5" sx={{ mt: 0.5 }} data-testid={`kpi-value-${k.id}`}>{kpiValue(k.value, k.unit, k.format)}</Typography>
        {k.numerator != null && k.denominator != null && (
          <Typography variant="caption" color="text.secondary">{k.numerator} of {k.denominator}</Typography>
        )}
      </CardContent>
    </Card>
  );
}

export function Section({ title, children, actions }: { title: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <Card sx={{ mb: 2 }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
          <Typography variant="subtitle1" fontWeight={700}>{title}</Typography>
          {actions}
        </Stack>
        {children}
      </CardContent>
    </Card>
  );
}

export function KeyValues({ data, keys }: { data: Record<string, any>; keys?: string[] }) {
  const ks = keys || Object.keys(data);
  return (
    <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr", sm: "repeat(2, 1fr)", md: "repeat(3, 1fr)" }, gap: 1.5 }}>
      {ks.map((k) => (
        <Box key={k}>
          <Typography variant="caption" color="text.secondary">{k.replaceAll("_", " ")}</Typography>
          <Typography variant="body2" sx={{ wordBreak: "break-word" }} data-testid={`field-${k}`}>
            {typeof data[k] === "object" && data[k] !== null ? JSON.stringify(data[k]) : String(data[k] ?? "")}
          </Typography>
        </Box>
      ))}
    </Box>
  );
}
