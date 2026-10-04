// Cliente da área da criança (RF-25): token isolado, sem refresh.
// Storage separado do responsável (hdt.access) — nunca se misturam.
const API = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const CHILD_LS = "hdt.child.access";
const CHILD_EXP_LS = "hdt.child.exp";

export function getChildAccess(): string | null {
  if (typeof window === "undefined") return null;
  const exp = Number(localStorage.getItem(CHILD_EXP_LS) || 0);
  if (exp && Date.now() > exp) {
    // A6: sessão infantil expirou — limpa sem exibir nada
    clearChildToken();
    return null;
  }
  return localStorage.getItem(CHILD_LS);
}

export function saveChildToken(token: string, expiresInSec = 7200) {
  localStorage.setItem(CHILD_LS, token);
  localStorage.setItem(CHILD_EXP_LS, String(Date.now() + expiresInSec * 1000));
}

export function clearChildToken() {
  localStorage.removeItem(CHILD_LS);
  localStorage.removeItem(CHILD_EXP_LS);
}

export function normalizeCode(raw: string): string {
  return (raw || "")
    .trim()
    .toUpperCase()
    .replace(/[^0-9A-Z]/g, "")
    .replace(/I/g, "1")
    .replace(/L/g, "1")
    .replace(/O/g, "0");
}

export class ChildApiError extends Error {
  code: string;
  status: number;
  retryAfter: number;
  constructor(message: string, code = "UNKNOWN", status = 0, retryAfter = 0) {
    super(message);
    this.code = code;
    this.status = status;
    this.retryAfter = retryAfter;
  }
}

export async function childApi<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(init.headers as Record<string, string>) };
  const token = getChildAccess();
  if (token) headers["Authorization"] = `Bearer ${token}`;
  headers["Content-Type"] = headers["Content-Type"] || "application/json";
  const r = await fetch(`${API}/v1${path}`, { ...init, headers });
  if (r.status === 401) {
    clearChildToken();
    throw new ChildApiError("Sessão expirada. Entre com o código de novo.", "UNAUTHORIZED", 401);
  }
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new ChildApiError(
      err?.error?.message || `HTTP ${r.status}`,
      err?.error?.code || "UNKNOWN",
      r.status
    );
  }
  return r.json() as Promise<T>;
}

export type ChildMe = { id: string; name: string; grade_level?: string | null };

export async function childLogin(rawCode: string): Promise<ChildMe> {
  const code = normalizeCode(rawCode);
  const r = await fetch(`${API}/v1/auth/child/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (r.status === 429) {
    const retry = Number(r.headers.get("Retry-After") || 60);
    throw new ChildApiError(
      `Muitas tentativas. Tente de novo em ${retry}s.`,
      "RATE_LIMITED",
      429,
      retry
    );
  }
  if (!r.ok) {
    throw new ChildApiError("Código inválido. Confira com o responsável.", "INVALID_CODE", r.status);
  }
  const body = await r.json();
  saveChildToken(body.access_token, body.expires_in || 7200);
  return body.child as ChildMe;
}
