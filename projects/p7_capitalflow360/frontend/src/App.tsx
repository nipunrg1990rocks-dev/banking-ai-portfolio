import { CircularProgress, CssBaseline, Stack } from "@mui/material";
import { ThemeProvider } from "@mui/material/styles";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router";
import { AuthProvider, useAuth } from "./auth";
import Layout from "./components/Layout";
import Admin from "./pages/Admin";
import Approvals from "./pages/Approvals";
import AuditTrail from "./pages/AuditTrail";
import Blotter from "./pages/Blotter";
import BookTrade from "./pages/BookTrade";
import Dashboard from "./pages/Dashboard";
import Events from "./pages/Events";
import Exceptions from "./pages/Exceptions";
import Login from "./pages/Login";
import Orders from "./pages/Orders";
import Positions from "./pages/Positions";
import Queues from "./pages/Queues";
import Recon from "./pages/Recon";
import RefData from "./pages/RefData";
import Reports from "./pages/Reports";
import TestRuns from "./pages/TestRuns";
import TradeDetail from "./pages/TradeDetail";
import Workspace from "./pages/Workspace";
import { makeTheme } from "./theme";

const qc = new QueryClient({ defaultOptions: { queries: { retry: 1, staleTime: 15000, refetchOnWindowFocus: false } } });

function Shell({ mode, toggle }: { mode: "light" | "dark"; toggle: () => void }) {
  const { me, loading } = useAuth();
  if (loading) return <Stack alignItems="center" sx={{ mt: 20 }}><CircularProgress /></Stack>;
  if (!me) return <Login />;
  return (
    <Layout mode={mode} toggleMode={toggle}>
      <Routes>
        <Route path="/" element={<Workspace />} />
        <Route path="/blotter" element={<Blotter />} />
        <Route path="/trades/:id" element={<TradeDetail />} />
        <Route path="/book" element={<BookTrade />} />
        <Route path="/orders" element={<Orders />} />
        <Route path="/orders/:id" element={<Orders />} />
        <Route path="/queues/:queue" element={<Queues />} />
        <Route path="/approvals" element={<Approvals />} />
        <Route path="/exceptions" element={<Exceptions />} />
        <Route path="/positions" element={<Positions />} />
        <Route path="/recon" element={<Recon />} />
        <Route path="/events" element={<Events />} />
        <Route path="/refdata" element={<RefData />} />
        <Route path="/dashboards/:name" element={<Dashboard />} />
        <Route path="/test-runs" element={<TestRuns />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/audit" element={<AuditTrail />} />
        <Route path="/admin" element={<Admin />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Layout>
  );
}

export default function App() {
  const [mode, setMode] = useState<"light" | "dark">(() => {
    try {
      return (localStorage.getItem("cf360.mode") as "light" | "dark") || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    } catch {
      return "light";
    }
  });
  const theme = useMemo(() => makeTheme(mode), [mode]);
  const toggle = () => {
    const m = mode === "dark" ? "light" : "dark";
    setMode(m);
    try {
      localStorage.setItem("cf360.mode", m);
    } catch {
      /* ignore */
    }
  };
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <QueryClientProvider client={qc}>
        <AuthProvider>
          <BrowserRouter>
            <Shell mode={mode} toggle={toggle} />
          </BrowserRouter>
        </AuthProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
