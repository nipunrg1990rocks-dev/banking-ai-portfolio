import { Alert, Box, Button, Card, CardActionArea, CardContent, Chip, Container, Stack, TextField, Typography } from "@mui/material";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../api";
import { useAuth } from "../auth";
import { ErrorAlert } from "../components/common";

const ROLE_TEXT: Record<string, string> = {
  TRADER: "Books orders and trades", MIDDLE_OFFICE: "Matching, enrichment, amendments", SETTLEMENTS: "Instructions, fails, retries",
  RECONCILIATION: "Custodian breaks", RISK: "Limits, controls, exceptions", CHECKER: "Approves maker requests", ADMIN: "Clock, processing, test runs",
};

export default function Login() {
  const { login } = useAuth();
  const users = useQuery({ queryKey: ["demo-users"], queryFn: () => api("/auth/demo-users") });
  const [username, setUsername] = useState("trader1");
  const [password, setPassword] = useState("");
  const [err, setErr] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      await login(username, password);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Container maxWidth="md" sx={{ py: { xs: 3, md: 8 } }}>
      <Typography variant="h4" fontWeight={800} color="primary.main">CapitalFlow 360</Typography>
      <Typography color="text.secondary" sx={{ mb: 2 }}>
        Multi-asset trade lifecycle simulator: capture to settlement, reconciliation and controls, across equities, bonds, FX, listed
        derivatives, swaps and financing.
      </Typography>
      <Alert severity="info" sx={{ mb: 3 }}>Synthetic data only. Simulated venues, brokers, CCPs and custodians. Not connected to any real system.</Alert>
      <Typography variant="subtitle1" fontWeight={700} gutterBottom>Choose a demo role</Typography>
      <Box sx={{ display: "grid", gridTemplateColumns: { xs: "1fr 1fr", md: "repeat(3, 1fr)" }, gap: 1.5, mb: 3 }}>
        {(users.data?.users || []).map((u: any) => (
          <Card key={u.username} sx={{ borderColor: username === u.username ? "primary.main" : undefined, borderWidth: username === u.username ? 2 : 1 }}>
            <CardActionArea onClick={() => setUsername(u.username)} data-testid={`role-${u.username}`}>
              <CardContent sx={{ p: 1.5 }}>
                <Typography fontWeight={700}>{u.display_name}</Typography>
                <Chip size="small" label={u.role} sx={{ my: 0.5 }} />
                <Typography variant="caption" display="block" color="text.secondary">{ROLE_TEXT[u.role]}</Typography>
              </CardContent>
            </CardActionArea>
          </Card>
        ))}
      </Box>
      <Stack direction={{ xs: "column", sm: "row" }} spacing={1.5} component="form" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <TextField label="Username" value={username} onChange={(e) => setUsername(e.target.value)} size="small" inputProps={{ "data-testid": "login-username" }} />
        <TextField label="Demo password" type="password" value={password} onChange={(e) => setPassword(e.target.value)} size="small"
          helperText="Set by CF360_DEMO_PASSWORD (see README)" inputProps={{ "data-testid": "login-password" }} />
        <Button type="submit" variant="contained" disabled={busy || !password} data-testid="login-submit" sx={{ height: 40 }}>Sign in</Button>
      </Stack>
      <ErrorAlert error={err} />
    </Container>
  );
}
