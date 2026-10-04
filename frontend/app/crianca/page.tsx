"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChildApiError, childLogin, getChildAccess, normalizeCode } from "@/lib/child-api";

// RF-25: login da criança — 1 campo (código de acesso), visual infantil, sem sidebar.
export default function CriancaLoginPage() {
  const router = useRouter();
  const [code, setCode] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (getChildAccess()) router.replace("/crianca/tarefas");
  }, [router]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await childLogin(code);
      router.replace("/crianca/tarefas");
    } catch (err) {
      setError(err instanceof ChildApiError ? err.message : "Falha inesperada.");
    } finally {
      setBusy(false);
    }
  }

  const norm = normalizeCode(code);

  return (
    <main className="auth-wrap child-wrap">
      <div className="card auth-card child-card">
        <div className="child-hero" aria-hidden>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="" width={64} height={64} />
        </div>
        <h1 className="child-title">Hora da Tarefa 🎒</h1>
        <p className="muted child-sub">Digite seu código para ver suas tarefas.</p>
        <form onSubmit={submit}>
          <label htmlFor="code">Código de acesso</label>
          <input
            id="code" type="text" value={code}
            onChange={(e) => setCode(e.target.value.toUpperCase().replace(/[^0-9A-Z-]/gi, ""))}
            placeholder="K7M2-P9QT" maxLength={9} autoCapitalize="characters"
            autoComplete="one-time-code" autoFocus required
            aria-describedby="code-hint" className="child-code-input"
          />
          <p id="code-hint" className="muted">
            {norm.length > 0 ? `${norm.length}/8 letras e números` : "Peça o código ao responsável."}
          </p>
          <button className="btn-primary child-enter" disabled={busy || norm.length !== 8}>
            {busy ? "Entrando..." : "Entrar ✨"}
          </button>
        </form>
        {error && <div className="error" role="alert">{error}</div>}
      </div>
    </main>
  );
}
