import { Box, Card, CardContent, Typography } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import { Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { useNavigate } from "react-router";
import { compact } from "../format";
import { SERIES } from "../theme";
import { drillPath } from "./common";

export type ChartSpec = { id: string; type: string; title: string; data: any[]; drill?: { kind: string; param: string } | null };

export function ChartView({ c, height = 260 }: { c: ChartSpec; height?: number }) {
  const nav = useNavigate();
  const theme = useTheme();
  const axis = { fontSize: 11, fill: theme.palette.text.secondary };
  const onClick = (name?: string) => {
    if (c.drill && name) nav(drillPath(c.drill.kind, { [c.drill.param]: name }));
  };
  let body;
  if (!c.data?.length) {
    body = <Typography variant="body2" color="text.secondary" sx={{ py: 6, textAlign: "center" }}>No data yet</Typography>;
  } else if (c.type === "pie") {
    body = (
      <ResponsiveContainer width="100%" height={height}>
        <PieChart>
          <Pie data={c.data} dataKey="value" nameKey="name" outerRadius="75%" label={(e: any) => e.name} onClick={(e: any) => onClick(e?.name)}>
            {c.data.map((_, i) => <Cell key={i} fill={SERIES[i % SERIES.length]} />)}
          </Pie>
          <Tooltip />
          <Legend />
        </PieChart>
      </ResponsiveContainer>
    );
  } else if (c.type === "line") {
    body = (
      <ResponsiveContainer width="100%" height={height}>
        <LineChart data={c.data} margin={{ left: 4, right: 12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
          <XAxis dataKey="name" tick={axis} minTickGap={24} />
          <YAxis tick={axis} tickFormatter={compact} width={48} />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke={SERIES[0]} dot={false} strokeWidth={2} />
        </LineChart>
      </ResponsiveContainer>
    );
  } else if (c.type === "stacked") {
    const x = Object.keys(c.data[0]).find((k) => typeof c.data[0][k] === "string") || "name";
    const series = Object.keys(c.data[0]).filter((k) => k !== x && typeof c.data[0][k] === "number");
    body = (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart data={c.data} margin={{ left: 4, right: 12 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
          <XAxis dataKey={x} tick={axis} minTickGap={16} />
          <YAxis tick={axis} tickFormatter={compact} width={48} />
          <Tooltip />
          <Legend />
          {series.map((s, i) => <Bar key={s} dataKey={s} stackId="a" fill={SERIES[i % SERIES.length]} />)}
        </BarChart>
      </ResponsiveContainer>
    );
  } else {
    // bar and funnel render as horizontal bars, sorted as given
    body = (
      <ResponsiveContainer width="100%" height={Math.max(height, c.data.length * 26)}>
        <BarChart data={c.data} layout="vertical" margin={{ left: 8, right: 16 }}>
          <CartesianGrid strokeDasharray="3 3" stroke={theme.palette.divider} />
          <XAxis type="number" tick={axis} tickFormatter={compact} />
          <YAxis type="category" dataKey="name" tick={axis} width={120} />
          <Tooltip />
          <Bar dataKey="value" fill={SERIES[c.type === "funnel" ? 2 : 0]} cursor={c.drill ? "pointer" : undefined} onClick={(e: any) => onClick(e?.name ?? e?.payload?.name)} />
        </BarChart>
      </ResponsiveContainer>
    );
  }
  return (
    <Card sx={{ height: "100%" }} data-testid={`chart-${c.id}`}>
      <CardContent>
        <Typography variant="subtitle2" fontWeight={700} gutterBottom>{c.title}</Typography>
        {c.drill && <Typography variant="caption" color="text.secondary">Click a bar or slice to drill down</Typography>}
        <Box sx={{ mt: 1 }}>{body}</Box>
      </CardContent>
    </Card>
  );
}
