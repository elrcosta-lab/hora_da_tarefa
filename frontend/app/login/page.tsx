"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { login, register } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      if (mode === "login") await login(email, password);
      else await register(name, email, password);
      router.push("/");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha inesperada.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth-wrap">
      <div className="card auth-card">
        <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 4 }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="Hora da Tarefa" width={40} height={40} />
          <h1 style={{ fontSize: 22, margin: 0 }}>Hora da Tarefa</h1>
        </div>
        <p className="muted">Foto → agenda → lembrete.</p>
        <form onSubmit={submit}>
          {mode === "register" && (
            <>
              <label htmlFor="name">Nome</label>
              <input id="name" value={name} onChange={(e) => setName(e.target.value)} required />
            </>
          )}
          <label htmlFor="email">E-mail</label>
          <input id="email" type="email" value={email} onChange={(e) => setEmail(e.target.value)} required />
          <label htmlFor="password">Senha (mín. 8 caracteres)</label>
          <input
            id="password" type="password" value={password} minLength={8}
            onChange={(e) => setPassword(e.target.value)} required
          />
          <button className="btn-primary" disabled={busy}>
            {busy ? "Aguarde..." : mode === "login" ? "Entrar" : "Criar conta"}
          </button>
        </form>
        {error && <div className="error" role="alert">{error}</div>}
        <p className="muted" style={{ marginTop: 16 }}>
          {mode === "login" ? (
            <>Sem conta? <a href="#" onClick={(e) => { e.preventDefault(); setMode("register"); }}>Criar conta</a></>
          ) : (
            <>Já tem conta? <a href="#" onClick={(e) => { e.preventDefault(); setMode("login"); }}>Entrar</a></>
          )}
        </p>
      </div>
    </main>
  );
}
