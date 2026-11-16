import { createTheme } from "@mui/material/styles";

export function makeTheme(mode: "light" | "dark") {
  const dark = mode === "dark";
  return createTheme({
    palette: {
      mode,
      primary: { main: dark ? "#4fc3a1" : "#0b6e4f" },
      secondary: { main: dark ? "#90caf9" : "#1f4e79" },
      background: dark ? { default: "#0d1117", paper: "#161b22" } : { default: "#f4f6f8", paper: "#ffffff" },
      success: { main: "#2e7d32" },
      warning: { main: "#b26a00" },
      error: { main: "#c62828" },
    },
    shape: { borderRadius: 10 },
    typography: {
      fontFamily: '"Inter", "Segoe UI", Roboto, Arial, sans-serif',
      h5: { fontWeight: 700 },
      h6: { fontWeight: 650 },
      button: { textTransform: "none", fontWeight: 600 },
    },
    components: {
      MuiCard: { defaultProps: { variant: "outlined" } },
      MuiPaper: { styleOverrides: { root: { backgroundImage: "none" } } },
      MuiChip: { styleOverrides: { root: { fontWeight: 600 } } },
    },
  });
}

// Colour-blind-safe categorical palette used by every chart.
export const SERIES = ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd", "#8c564b", "#e377c2", "#7f7f7f", "#bcbd22", "#17becf"];
