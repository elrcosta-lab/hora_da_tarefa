"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess, getUserId } from "@/lib/api";

export default function OnboardingPage() {
  const router = useRouter();
  const [step, setStep] = useState(1);
  const [childId, setChildId] = useState("");
  const [name, setName] = useState("");
  const [grade, setGrade] = useState("5º ano");
  const [rows, setRows] = useState([{ weekday: 0, start: "07:30", end: "12:00", subject: "Aula" }]);
  const [code, setCode] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  async function step1(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      const c = await api<{ id: string }>("/children", {
        method: "POST",
        body: JSON.stringify({ name, grade_level: grade || null }),
      });
      setChildId(c.id);
      setStep(2);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha.");
    } finally {
      setBusy(false);
    }
  }

  async function step2(save: boolean) {
    setBusy(true);
    setMsg(null);
    try {
      if (save && rows.length > 0) {
        await api(`/children/${childId}/schedules`, {
          method: "POST",
          body: JSON.stringify({
            replace: true,
            entries: rows.map((r) => ({
              weekday: r.weekday, start_time: r.start, end_time: r.end, subject: r.subject,
            })),
          }),
        });
      }
      setStep(3);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao salvar grade.");
    } finally {
      setBusy(false);
    }
  }

  async function genCode() {
    const uid = getUserId();
    if (!uid) {
      setMsg("Entre novamente.");
      return;
    }
    setBusy(true);
    try {
      const body = await api<{ link_code: string }>("/auth/telegram/link", {
        method: "POST",
        body: JSON.stringify({ user_id: uid }),
      });
      setCode(body.link_code);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha.");
    } finally {
      setBusy(false);
    }
  }

  const DAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

  return (
    <main className="auth-wrap">
      <div className="card" style={{ width: "100%", maxWidth: 560 }}>
        <div style={{ display: "flex", gap: 8, marginBottom: 16 }} aria-label="Progresso">
          {[1, 2, 3].map((s) => (
            <div key={s} style={{
              flex: 1, height: 8, borderRadius: 4,
              background: s <= step ? "var(--primary)" : "var(--muted)",
            }} />
          ))}
        </div>

        {step === 1 && (
          <form onSubmit={step1}>
            <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>Passo 1 — Cadastre seu filho</h1>
            <p className="muted">Quem vai receber a agenda organizada?</p>
            <label htmlFor="ob-name">Nome</label>
            <input id="ob-name" type="text" value={name} onChange={(e) => setName(e.target.value)} required style={{ width: "100%" }} />
            <label htmlFor="ob-grade">Série</label>
            <input id="ob-grade" type="text" value={grade} onChange={(e) => setGrade(e.target.value)} style={{ width: "100%" }} />
            <button className="btn-primary" disabled={busy} style={{ width: "100%", marginTop: 16 }}>Continuar</button>
          </form>
        )}

        {step === 2 && (
          <>
            <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>Passo 2 — Grade escolar</h1>
            <p className="muted">Quando há aula? O motor nunca agenda por cima. Pode pular e completar depois.</p>
            {rows.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 8, marginBottom: 8 }}>
                <select aria-label="Dia" value={r.weekday} onChange={(e) => {
                  const c = [...rows]; c[i] = { ...r, weekday: Number(e.target.value) }; setRows(c);
                }}>
                  {DAYS.map((d, di) => <option key={d} value={di}>{d}</option>)}
                </select>
                <input type="text" aria-label="Início" value={r.start} onChange={(e) => {
                  const c = [...rows]; c[i] = { ...r, start: e.target.value }; setRows(c);
                }} style={{ width: 80 }} />
                <input type="text" aria-label="Fim" value={r.end} onChange={(e) => {
                  const c = [...rows]; c[i] = { ...r, end: e.target.value }; setRows(c);
                }} style={{ width: 80 }} />
                <button type="button" className="btn-secondary" onClick={() => setRows(rows.filter((_, j) => j !== i))}>×</button>
              </div>
            ))}
            <button type="button" className="btn-secondary" onClick={() => setRows([...rows, { weekday: 0, start: "07:30", end: "12:00", subject: "Aula" }])}>
              + Bloco de aula
            </button>
            <div style={{ display: "flex", gap: 8, marginTop: 16 }}>
              <button className="btn-primary" disabled={busy} onClick={() => step2(true)} style={{ flex: 1 }}>Salvar e continuar</button>
              <button className="btn-secondary" disabled={busy} onClick={() => step2(false)}>Pular</button>
            </div>
          </>
        )}

        {step === 3 && (
          <>
            <h1 style={{ fontSize: 22, margin: "0 0 4px" }}>Passo 3 — Conecte o Telegram</h1>
            <p className="muted">Receba lembretes e envie fotos da tarefa pelo chat.</p>
            <ol className="muted">
              <li>Abra o Telegram e inicie conversa com o bot{process.env.NEXT_PUBLIC_TELEGRAM_BOT ? <> <strong>@{process.env.NEXT_PUBLIC_TELEGRAM_BOT}</strong></> : " (nome configurado no BotFather)"}</li>
              <li>Envie o código abaixo (vale uma única vez)</li>
            </ol>
            {!code ? (
              <button className="btn-secondary" disabled={busy} onClick={genCode}>Gerar meu código</button>
            ) : (
              <div style={{ padding: 16, background: "#f1f5f9", borderRadius: 8, textAlign: "center", marginBottom: 12 }}>
                <div style={{ fontSize: 32, fontWeight: 800, letterSpacing: 8 }}>{code}</div>
              </div>
            )}
            <button className="btn-primary" style={{ width: "100%" }} onClick={() => router.push("/")}>
              Começar a usar 🎉
            </button>
          </>
        )}

        {msg && <div className="error" role="alert">{msg}</div>}
      </div>
    </main>
  );
}
