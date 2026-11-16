import type { GridColDef } from "@mui/x-data-grid";
import { StatusChip } from "./common";

const CHIP_FIELDS = new Set(["status", "severity", "match_result"]);

export function autoColumns(fields: string[], overrides: Record<string, Partial<GridColDef>> = {}): GridColDef[] {
  return fields.map((f) => ({
    field: f,
    headerName: f.replaceAll("_", " "),
    width: Math.min(260, Math.max(90, f.length * 9 + 30)),
    ...(CHIP_FIELDS.has(f) ? { renderCell: (p: any) => <StatusChip value={p.value} /> } : {}),
    valueFormatter: (v: any) => (v !== null && typeof v === "object" ? JSON.stringify(v) : v),
    ...(overrides[f] || {}),
  }));
}
