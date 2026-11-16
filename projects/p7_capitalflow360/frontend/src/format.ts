const MINOR: Record<string, number> = { JPY: 0 };

export function money(v: unknown, ccy?: string | null): string {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  const dp = ccy ? (MINOR[ccy] ?? 2) : 2;
  return n.toLocaleString("en-US", { minimumFractionDigits: dp, maximumFractionDigits: dp });
}

export function num(v: unknown, dp = 0): string {
  if (v === null || v === undefined || v === "") return "";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: dp });
}

export function compact(v: unknown): string {
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v ?? "");
  return Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 }).format(n);
}

export function kpiValue(value: unknown, unit?: string, fmt?: string | null): string {
  if (value === null || value === undefined) return "n/a";
  if (fmt === "money" || unit === "USD") return `${compact(value)}${unit ? " " + unit : ""}`;
  if (unit === "%") return `${num(value, 2)}%`;
  if (typeof value === "number") return `${num(value, 2)}${unit ? " " + unit : ""}`;
  return `${value}${unit ? " " + unit : ""}`;
}

export function dt(v?: string | null) {
  if (!v) return "";
  return v.replace("T", " ").slice(0, 19);
}
