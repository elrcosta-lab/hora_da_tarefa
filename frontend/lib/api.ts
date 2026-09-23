// Cliente da API FastAPI (SPECS §3) com JWT + refresh automático.
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

export type Tokens = { access_token: string; refresh_token: string };

const LS_ACCESS = "hdt.access";
const LS_REFRESH = "hdt.refresh";
const LS_USER = "hdt.user";

export function getAccess(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LS_ACCESS);
}

export function getUserId(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(LS_USER);
}

export function saveTokens(t: Tokens) {
  localStorage.setItem(LS_ACCESS, t.access_token);
  localStorage.setItem(LS_REFRESH, t.refresh_token);
}

export function clearTokens() {
  localStorage.removeItem(LS_ACCESS);
  localStorage.removeItem(LS_REFRESH);
  localStorage.removeItem(LS_USER);
}

async function refresh(): Promise<boolean> {
  const rt = typeof window !== "undefined" ? localStorage.getItem(LS_REFRESH) : null;
  if (!rt) return false;
  const r = await fetch(`${API}/v1/auth/refresh`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh_token: rt }),
  });
  if (!r.ok) {
    clearTokens();
    return false;
  }
  const body = await r.json();
  saveTokens({ access_token: body.access_token, refresh_token: body.refresh_token });
  return true;
}

export class ApiError extends Error {
  code: string;
  status: number;
  retryable: boolean;
  constructor(message: string, code = "UNKNOWN", status = 0, retryable = false) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryable = retryable;
  }
}
export async function api<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = getAccess();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (!(init.body instanceof FormData)) headers["Content-Type"] = headers["Content-Type"] || "application/json";
  const r = await fetch(`${API}/v1${path}`, { ...init, headers });
  if (r.status === 401 && retry && (await refresh())) {
    return api<T>(path, init, false);
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new ApiError(
      err?.error?.message || `HTTP ${r.status}`,
      err?.error?.code || "UNKNOWN",
      r.status,
      err?.error?.details?.retryable === true
    );
  }
  return r.json() as Promise<T>;
}

export async function login(email: string, password: string) {
  const r = await fetch(`${API}/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    // RF-16: conta pendente/rejeitada tem mensagem própria (não é erro de credencial)
    if (err?.error?.message) throw new Error(err.error.message);
    throw new Error("E-mail ou senha inválidos.");
  }
  const body = await r.json();
  saveTokens({ access_token: body.access_token, refresh_token: body.refresh_token });
  if (typeof window !== "undefined" && body.user_id) localStorage.setItem(LS_USER, body.user_id);
  return body.user_id as string | undefined;
}

export async function register(name: string, email: string, password: string) {
  const r = await fetch(`${API}/v1/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name, email, password, lgpd_consent: true, lgpd_version: "termos-v1" }),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error(err?.error?.message || "Falha no cadastro.");
  }
  // RF-16: conta nasce pendente — sem auto-login; liberação é do administrador.
}
