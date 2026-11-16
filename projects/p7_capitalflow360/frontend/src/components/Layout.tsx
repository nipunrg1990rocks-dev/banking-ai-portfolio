import { AppBar, Box, Chip, Divider, Drawer, IconButton, List, ListItemButton, ListItemIcon, ListItemText, ListSubheader, Stack, Toolbar, Tooltip, Typography, useMediaQuery } from "@mui/material";
import { useTheme } from "@mui/material/styles";
import MenuIcon from "@mui/icons-material/Menu";
import DarkMode from "@mui/icons-material/DarkMode";
import LightMode from "@mui/icons-material/LightMode";
import Logout from "@mui/icons-material/Logout";
import Home from "@mui/icons-material/Home";
import TableChart from "@mui/icons-material/TableChart";
import AddCircle from "@mui/icons-material/AddCircle";
import ShoppingCart from "@mui/icons-material/ShoppingCart";
import Inbox from "@mui/icons-material/Inbox";
import AccountBalance from "@mui/icons-material/AccountBalance";
import CompareArrows from "@mui/icons-material/CompareArrows";
import ReportProblem from "@mui/icons-material/ReportProblem";
import Event from "@mui/icons-material/Event";
import Gavel from "@mui/icons-material/Gavel";
import Storage from "@mui/icons-material/Storage";
import Insights from "@mui/icons-material/Insights";
import Science from "@mui/icons-material/Science";
import Description from "@mui/icons-material/Description";
import AdminPanelSettings from "@mui/icons-material/AdminPanelSettings";
import History from "@mui/icons-material/History";
import { useQuery } from "@tanstack/react-query";
import { useState, type ReactNode } from "react";
import { Link, useLocation } from "react-router";
import { api } from "../api";
import { useAuth } from "../auth";

type Item = { to: string; label: string; icon: ReactNode; roles?: string[] };
const NAV: { group: string; items: Item[] }[] = [
  { group: "Work", items: [
    { to: "/", label: "My workspace", icon: <Home /> },
    { to: "/blotter", label: "Trade blotter", icon: <TableChart /> },
    { to: "/book", label: "Book a trade", icon: <AddCircle />, roles: ["TRADER"] },
    { to: "/orders", label: "Orders & fills", icon: <ShoppingCart /> },
    { to: "/queues/matching", label: "Work queues", icon: <Inbox /> },
    { to: "/approvals", label: "Approvals", icon: <Gavel /> },
    { to: "/exceptions", label: "Exceptions", icon: <ReportProblem /> },
  ] },
  { group: "Books", items: [
    { to: "/positions", label: "Positions & cash", icon: <AccountBalance /> },
    { to: "/recon", label: "Reconciliation", icon: <CompareArrows /> },
    { to: "/events", label: "Lifecycle events", icon: <Event /> },
    { to: "/refdata", label: "Reference data", icon: <Storage /> },
  ] },
  { group: "Insight", items: [
    { to: "/dashboards/executive", label: "Dashboards", icon: <Insights /> },
    { to: "/test-runs", label: "Test runs", icon: <Science /> },
    { to: "/reports", label: "Reports", icon: <Description /> },
    { to: "/audit", label: "Audit trail", icon: <History /> },
    { to: "/admin", label: "Admin", icon: <AdminPanelSettings /> },
  ] },
];
const WIDTH = 236;

export default function Layout({ children, mode, toggleMode }: { children: ReactNode; mode: string; toggleMode: () => void }) {
  const { me, logout } = useAuth();
  const theme = useTheme();
  const wide = useMediaQuery(theme.breakpoints.up("md"));
  const [open, setOpen] = useState(false);
  const loc = useLocation();
  const health = useQuery({ queryKey: ["health"], queryFn: () => api("/health"), refetchInterval: 60000 });

  const drawer = (
    <Box sx={{ width: WIDTH }} role="navigation" data-testid="nav">
      <Toolbar />
      {NAV.map((g) => (
        <List key={g.group} dense subheader={<ListSubheader disableSticky>{g.group}</ListSubheader>}>
          {g.items.filter((i) => !i.roles || i.roles.includes(me?.role || "")).map((i) => {
            const active = i.to === "/" ? loc.pathname === "/" : loc.pathname.startsWith(i.to.split("/").slice(0, 2).join("/"));
            return (
              <ListItemButton key={i.to} component={Link} to={i.to} selected={active} onClick={() => setOpen(false)} data-testid={`nav-${i.to.split("/")[1] || "home"}`}>
                <ListItemIcon sx={{ minWidth: 36 }}>{i.icon}</ListItemIcon>
                <ListItemText primary={i.label} />
              </ListItemButton>
            );
          })}
        </List>
      ))}
      <Divider />
      <Typography variant="caption" color="text.secondary" sx={{ display: "block", p: 2 }}>
        Simulation with synthetic data. No real venues, prices or client data.
      </Typography>
    </Box>
  );

  return (
    <Box sx={{ display: "flex", minHeight: "100vh" }}>
      <AppBar position="fixed" color="default" elevation={0} sx={{ zIndex: (t) => t.zIndex.drawer + 1, borderBottom: 1, borderColor: "divider" }}>
        <Toolbar sx={{ gap: 1 }}>
          {!wide && <IconButton onClick={() => setOpen(true)} aria-label="Open menu" data-testid="menu-button"><MenuIcon /></IconButton>}
          <Typography variant="h6" noWrap sx={{ fontWeight: 800, color: "primary.main", fontSize: { xs: 17, sm: 20 } }}>CapitalFlow 360</Typography>
          <Box sx={{ flex: 1 }} />
          <Stack direction="row" spacing={{ xs: 0, sm: 1 }} alignItems="center">
            {health.data?.business_date && <Chip size="small" label={wide ? `Business date ${health.data.business_date}` : health.data.business_date} data-testid="business-date" />}
            {health.data?.defects_enabled?.length > 0 && <Chip size="small" color="error" label={`Defects ON: ${health.data.defects_enabled.join(",")}`} data-testid="defects-banner" />}
            {wide && <Chip size="small" color="primary" variant="outlined" label={`${me?.display_name} · ${me?.role}`} data-testid="current-user" />}
            <Tooltip title="Toggle theme"><IconButton onClick={toggleMode} aria-label="Toggle theme">{mode === "dark" ? <LightMode /> : <DarkMode />}</IconButton></Tooltip>
            <Tooltip title="Log out"><IconButton onClick={logout} aria-label="Log out" data-testid="logout"><Logout /></IconButton></Tooltip>
          </Stack>
        </Toolbar>
      </AppBar>
      {wide ? (
        <Drawer variant="permanent" sx={{ width: WIDTH, "& .MuiDrawer-paper": { width: WIDTH, boxSizing: "border-box" } }}>{drawer}</Drawer>
      ) : (
        <Drawer open={open} onClose={() => setOpen(false)}>{drawer}</Drawer>
      )}
      <Box component="main" sx={{ flex: 1, minWidth: 0, p: { xs: 1.5, md: 3 } }}>
        <Toolbar />
        {children}
      </Box>
    </Box>
  );
}
