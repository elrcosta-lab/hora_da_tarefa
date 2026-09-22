"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, clearTokens, getAccess } from "@/lib/api";

type Child = { id: string; name: string; grade_level?: string | null };
type TaskItem = {
  id: string; child_id: string; subject?: string | null; title?: string | null;
  due_at?: string | null; status: string; extraction_confidence?: number | null;
  estimated_minutes?: number | null; scheduled_start?: string | null;
};
type Today = { date: string; due_today: TaskItem[]; overdue: TaskItem[]; scheduled_today: TaskItem[] };

function fmtDue(iso?: string | null) {
  if (!iso) return "sem prazo";
  const d = new Date(iso);
  return d.toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

function TaskCard({ t, onDone }: { t: TaskItem; onDone: (id: string) => void }) {
  return (
    <div className="task">
      <div className="row">
        {t.subject && <span className="chip-mat">{t.subject}</span>}
        <span className={`pill pill-${t.status}`}>{t.status.replace("_", " ")}</span>
        <span className="title">{t.title || "Tarefa"}</span>
      </div>
      <div className="meta">Entrega: {fmtDue(t.due_at)}
        {t.scheduled_start && <> · Agendada: {fmtDue(t.scheduled_start)}</>}
      </div>
      {(t.status === "pendente" || t.status === "agendada" || t.status === "em_andamento") && (
        <div style={{ marginTop: 8 }}>
          <button className="btn-success" onClick={() => onDone(t.id)}>✅ Concluir</button>
        </div>
      )}
    </div>
  );
}

export default function DashboardPage() {
  const router = useRouter();
  const [children, setChildren] = useState<Child[]>([]);
  const [childId, setChildId] = useState<string>("");
  const [today, setToday] = useState<Today | null>(null);
  const [kpis, setKpis] = useState({ pendentes: 0, agendadas: 0, atrasadas: 0, concluidas: 0 });
  const [upcoming, setUpcoming] = useState<TaskItem[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const load = useCallback(async (cid: string) => {
    const params = cid ? `?child_id=${cid}` : "";
    const [kids, t, all] = await Promise.all([
      api<{ items: Child[] }>("/children"),
      api<Today>(`/homeworks/today${params}`),
      api<{ items: TaskItem[]; total: number }>(`/homeworks${params}&page_size=100`.replace("?&", "?").replace("/homeworks&", "/homeworks?")),
    ]);
    setChildren(kids.items);
    if (!cid && kids.items.length > 0) {
      setChildId(kids.items[0].id);
      return;
    }
    setToday(t);
    const pend = all.items.filter((i) => i.status === "pendente").length;
    const ag = all.items.filter((i) => i.status === "agendada").length;
    const atr = all.items.filter((i) => i.status === "atrasada").length;
    const con = all.items.filter((i) => i.status === "concluida").length;
    setKpis({ pendentes: pend, agendadas: ag, atrasadas: atr, concluidas: con });
    setUpcoming(all.items.filter((i) => ["pendente", "agendada", "em_andamento", "atrasada"].includes(i.status)).slice(0, 8));
  }, []);

  useEffect(() => {
    if (!getAccess()) return;
    load(childId).catch(() => router.push("/login"));
  }, [childId, load, router]);

  async function doUpload(e: React.FormEvent) {
    e.preventDefault();
    if (!file || !childId) {
      setMsg("Escolha a criança e a foto da tarefa.");
      return;
    }
    setBusy(true);
    setMsg(null);
    try {
      const form = new FormData();
      form.append("file", file);
      form.append("child_id", childId);
      await api("/homeworks/upload", { method: "POST", body: form });
      setMsg("Recebida! Processando extração… atualize em alguns segundos.");
      setFile(null);
      await load(childId);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha no envio.");
    } finally {
      setBusy(false);
    }
  }

  async function conclude(id: string) {
    try {
      // avança pela FSM até concluir (agendada → em_andamento → concluída)
      for (const st of ["em_andamento", "concluida"]) {
        try {
          await api(`/homeworks/${id}/status`, { method: "PATCH", body: JSON.stringify({ status: st }) });
        } catch { /* ignora transição inválida e tenta a próxima */ }
      }
      await load(childId);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao concluir.");
    }
  }

  function logout() {
    clearTokens();
    router.push("/login");
  }

  return (
    <div className="layout">
      <nav className="sidebar" aria-label="Navegação principal">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <div className="brand"><img src="/icon.svg" alt="" />Hora da Tarefa</div>
        <a href="/" className="active">Dashboard</a>
        <a href="/tarefas">Tarefas</a>
        <a href="/calendario">Calendário</a>
        <a href="/criancas">Crianças</a>
        <a href="/configuracoes">Configurações</a>
        <div className="spacer" />
        <a href="#" onClick={(e) => { e.preventDefault(); logout(); }}>Sair</a>
      </nav>
      <main className="main">
        <div className="topbar">
          <h1>Dashboard</h1>
          <select aria-label="Criança" value={childId} onChange={(e) => setChildId(e.target.value)}>
            {children.map((k) => (
              <option key={k.id} value={k.id}>{k.name}{k.grade_level ? ` — ${k.grade_level}` : ""}</option>
            ))}
          </select>
          <div className="spacer" />
          <form onSubmit={doUpload} style={{ display: "flex", gap: 8 }}>
            <input
              type="file" accept="image/jpeg,image/png,image/webp"
              aria-label="Foto da tarefa"
              onChange={(e) => setFile(e.target.files?.[0] || null)}
            />
            <button className="btn-primary" disabled={busy || !file}>+ Enviar tarefa</button>
          </form>
        </div>
        {msg && <p className="muted" role="status">{msg}</p>}

        <div className="grid-kpi">
          <div className="card kpi warn"><div className="value">{kpis.pendentes}</div><div className="label">Pendentes</div></div>
          <div className="card kpi"><div className="value">{kpis.agendadas}</div><div className="label">Agendadas</div></div>
          <div className="card kpi danger"><div className="value">{kpis.atrasadas}</div><div className="label">Atrasadas</div></div>
          <div className="card kpi ok"><div className="value">{kpis.concluidas}</div><div className="label">Concluídas</div></div>
        </div>

        <div className="columns">
          <section className="card" aria-label="Próximas tarefas">
            <h2>Próximas tarefas</h2>
            {upcoming.length === 0 && <div className="empty">Nenhuma tarefa ativa. 🎉</div>}
            {upcoming.map((t) => <TaskCard key={t.id} t={t} onDone={conclude} />)}
          </section>
          <section className="card" aria-label="Hoje">
            <h2>Hoje {today ? `(${today.date.split("-").reverse().slice(0, 2).join("/")})` : ""}</h2>
            {today && today.overdue.length > 0 && (
              <>
                <h3 style={{ color: "var(--destructive)", fontSize: 14 }}>Atrasadas</h3>
                {today.overdue.map((t) => <TaskCard key={t.id} t={t} onDone={conclude} />)}
              </>
            )}
            <div className="timeline">
              {(today?.due_today || []).map((t) => (
                <div className="slot" key={t.id}>
                  <div className="hour">{t.scheduled_start ? fmtDue(t.scheduled_start) : fmtDue(t.due_at)}</div>
                  <div><strong>{t.subject}</strong> — {t.title}</div>
                </div>
              ))}
              {(today?.due_today || []).length === 0 && <div className="empty">Nada agendado para hoje.</div>}
            </div>
          </section>
        </div>
      </main>
    </div>
  );
}
