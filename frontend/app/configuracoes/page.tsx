"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess, getUserId } from "@/lib/api";
import Sidebar from "@/components/Sidebar";

type Child = { id: string; name: string };
type Settings = {
  child_id: string; lembrete_24h: boolean; lembrete_2h: boolean;
  quiet_start: string; quiet_end: string;
};

export default function ConfigPage() {
  const router = useRouter();
  const [children, setChildren] = useState<Child[]>([]);
  const [childId, setChildId] = useState("");
  const [s24, setS24] = useState(true);
  const [s2, setS2] = useState(true);
  const [qStart, setQStart] = useState("21:30");
  const [qEnd, setQEnd] = useState("07:00");
  const [code, setCode] = useState<string | null>(null);
  const [linked, setLinked] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const load = useCallback(async () => {
    const kids = await api<{ items: Child[] }>("/children");
    setChildren(kids.items);
    if (!childId && kids.items.length > 0) setChildId(kids.items[0].id);
  }, [childId]);

  useEffect(() => {
    if (!getAccess()) return;
    load().catch(() => router.push("/login"));
  }, [load, router]);

  // lê o estado real do servidor ao trocar de criança (nada de defaults ilusórios)
  useEffect(() => {
    if (!childId || !getAccess()) return;
    api<Settings>(`/notifications/settings?child_id=${childId}`)
      .then((s) => {
        setS24(s.lembrete_24h);
        setS2(s.lembrete_2h);
        setQStart(s.quiet_start);
        setQEnd(s.quiet_end);
      })
      .catch(() => {});
  }, [childId]);

  // polling de vínculo: confirma quando o bot consumir o código
  useEffect(() => {
    if (!getAccess()) return;
    let alive = true;
    const id = setInterval(async () => {
      try {
        const s = await api<{ linked: boolean }>("/auth/telegram/status");
        if (alive && s.linked) setLinked(true);
      } catch { /* próximo ciclo */ }
    }, 5000);
    api<{ linked: boolean }>("/auth/telegram/status")
      .then((s) => { if (alive) setLinked(s.linked); })
      .catch(() => {});
    return () => { alive = false; clearInterval(id); };
  }, []);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (!childId) return;
    setBusy(true);
    setMsg(null);
    try {
      const s = await api<Settings>("/notifications/settings", {
        method: "POST",
        body: JSON.stringify({
          child_id: childId, lembrete_24h: s24, lembrete_2h: s2,
          quiet_start: qStart, quiet_end: qEnd,
        }),
      });
      setS24(s.lembrete_24h);
      setS2(s.lembrete_2h);
      setQStart(s.quiet_start);
      setQEnd(s.quiet_end);
      setMsg("Preferências salvas! ✅");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  async function genCode() {
    const uid = getUserId();
    if (!uid) {
      setMsg("Entre novamente para gerar o código.");
      return;
    }
    setBusy(true);
    try {
      const body = await api<{ link_code: string }>("/auth/telegram/link", {
        method: "POST",
        body: JSON.stringify({ user_id: uid }),
      });
      setCode(body.link_code);
      setMsg("Código gerado. Ele só vale uma vez — envie no bot.");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao gerar código.");
    } finally {
      setBusy(false);
    }
  }

  function toggleRow(label: string, desc: string, value: boolean, set: (v: boolean) => void) {
    return (
      <label style={{ display: "flex", gap: 12, alignItems: "flex-start", padding: "12px 0", borderBottom: "1px solid #eef2f7" }}>
        <input
          type="checkbox" checked={value} onChange={(e) => set(e.target.checked)}
          style={{ minHeight: 24, width: 24, marginTop: 2 }} aria-label={label}
        />
        <span><strong>{label}</strong><br /><span className="muted">{desc}</span></span>
      </label>
    );
  }

  return (
    <div className="layout">
      <Sidebar active="/configuracoes" />
      <main className="main">
        <div className="topbar">
          <h1>Configurações</h1>
          <select aria-label="Criança" value={childId} onChange={(e) => setChildId(e.target.value)}>
            {children.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
          </select>
        </div>
        {msg && <p className="muted" role="status">{msg}</p>}

        <div className="columns">
          <form className="card" onSubmit={save} aria-label="Notificações">
            <h2>Notificações</h2>
            {toggleRow("Lembrete 24 horas antes", "Aviso na véspera da entrega.", s24, setS24)}
            {toggleRow("Lembrete 2 horas antes", "Último aviso antes do prazo.", s2, setS2)}
            <h3 style={{ marginTop: 16 }}>Não perturbe</h3>
            <div style={{ display: "flex", gap: 8 }}>
              <div style={{ flex: 1 }}>
                <label htmlFor="q-start">Início</label>
                <input id="q-start" type="text" value={qStart} onChange={(e) => setQStart(e.target.value)} style={{ width: "100%" }} />
              </div>
              <div style={{ flex: 1 }}>
                <label htmlFor="q-end">Fim</label>
                <input id="q-end" type="text" value={qEnd} onChange={(e) => setQEnd(e.target.value)} style={{ width: "100%" }} />
              </div>
            </div>
            <button className="btn-primary" disabled={busy || !childId} style={{ marginTop: 12 }}>Salvar</button>
          </form>

          <div className="card" aria-label="Telegram">
            <h2>Telegram {linked && <span className="pill pill-concluida">conectado</span>}</h2>
            {linked ? (
              <p className="muted">Conta vinculada — o bot já pode enviar fotos e lembretes.</p>
            ) : (
              <>
                <p className="muted">Vincule sua conta{process.env.NEXT_PUBLIC_TELEGRAM_BOT ? <> no bot <strong>@{process.env.NEXT_PUBLIC_TELEGRAM_BOT}</strong></> : ""} para enviar fotos e receber lembretes. O código vale uma única vez.</p>
                <button className="btn-secondary" disabled={busy} onClick={genCode}>Gerar código</button>
              </>
            )}
            {code && !linked && (
              <div style={{ marginTop: 12, padding: 16, background: "#f1f5f9", borderRadius: 8, textAlign: "center" }}>
                <div className="code-display">{code}</div>
                <p className="muted">Envie esse código no chat do bot (ou /start {code}).</p>
              </div>
            )}
          </div>
        </div>
      </main>
    </div>
  );
}
