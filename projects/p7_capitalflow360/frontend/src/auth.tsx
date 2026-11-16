import { createContext, useCallback, useContext, useEffect, useState, type ReactNode } from "react";
import { api, getToken, setToken } from "./api";

export type Me = { username: string; display_name: string; role: string; permissions: string[] };
type Ctx = { me: Me | null; loading: boolean; login: (u: string, p: string) => Promise<void>; logout: () => void; can: (perm: string) => boolean };

const AuthCtx = createContext<Ctx>(null as unknown as Ctx);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [me, setMe] = useState<Me | null>(null);
  const [loading, setLoading] = useState(!!getToken());

  useEffect(() => {
    if (!getToken()) return;
    api<Me>("/auth/me")
      .then(setMe)
      .catch(() => setToken(null))
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    const h = () => setMe(null);
    window.addEventListener("cf360:logout", h);
    return () => window.removeEventListener("cf360:logout", h);
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await api<{ access_token: string }>("/auth/login", { method: "POST", body: { username, password } });
    setToken(r.access_token);
    setMe(await api<Me>("/auth/me"));
  }, []);

  const logout = useCallback(() => {
    setToken(null);
    setMe(null);
  }, []);

  const can = useCallback((perm: string) => !!me?.permissions.includes(perm), [me]);
  return <AuthCtx.Provider value={{ me, loading, login, logout, can }}>{children}</AuthCtx.Provider>;
}

export const useAuth = () => useContext(AuthCtx);
