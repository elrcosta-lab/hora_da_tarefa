"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess } from "@/lib/api";

type Child = { id: string; name: string };
type Sched = { weekday: number; start_time: string; end_time: string; subject: string };
type Act = {
  id: string; title: string; weekday?: number | null; start_time: string; end_time: string;
  travel_before_min: number; is_blocking: boolean;
};
type Task = {
  id: string; subject?: string | null; title?: string | null; due_at?: string | null;
  status: string; scheduled_start?: string | null; scheduled_end?: string | null;
};

const DAYS = ["Seg", "Ter", "Qua", "Qui", "Sex", "Sáb", "Dom"];

function mondayOf(offsetWeeks: number): Date {
  const now = new Date();
  const dow = (now.getDay() + 6) % 7; // 0 = segunda
  const m = new Date(now);
  m.setDate(now.getDate() - dow + offsetWeeks * 7);
  m.setHours(0, 0, 0, 0);
  return m;
}

function sameDay(a: Date, iso?: string | null) {
  if (!iso) return false;
  const d = new Date(iso);
  return d.getFullYear() === a.getFullYear() && d.getMonth() === a.getMonth() && d.getDate() === a.getDate();
}

function hm(iso?: string | null) {
  if (!iso) return "";
  return new Date(iso).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
}

export default function CalendarioPage() {
  const router = useRouter();
  const [children, setChildren] = useState<Child[]>([]);
  const [childId, setChildId] = useState("");
  const [weekOffset, setWeekOffset] = useState(0);
  const [schedules, setSchedules] = useState<Sched[]>([]);
  const [activities, setActivities] = useState<Act[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const loadKids = useCallback(async () => {
    const kids = await api<{ items: Child[] }>("/children");
    setChildren(kids.items);
    if (!childId && kids.items.length > 0) setChildId(kids.items[0].id);
  }, [childId]);

  useEffect(() => {
    if (!getAccess()) return;
    loadKids().catch(() => router.push("/login"));
  }, [loadKids, router]);

  useEffect(() => {
    if (!childId) return;
    (async () => {
      try {
        const ag = await api<{ schedules: Sched[]; activities: Act[] }>(`/children/${childId}/agenda`);
        setSchedules(ag.schedules);
        setActivities(ag.activities);
        const hw = await api<{ items: Task[] }>(`/homeworks?child_id=${childId}&page_size=100`);
        setTasks(hw.items);
      } catch (err) {
        setMsg(err instanceof Error ? err.message : "Falha.");
      }
    })();
  }, [childId]);

  const days = useMemo(() => {
    const m = mondayOf(weekOffset);
    return Array.from({ length: 7 }, (_, i) => {
      const d = new Date(m);
      d.setDate(m.getDate() + i);
      return d;
    });
  }, [weekOffset]);

  const weekLabel = useMemo(() => {
    const f = (d: Date) => d.toLocaleDateString("pt-BR", { day: "2-digit", month: "short" });
    return `${f(days[0])} – ${f(days[6])}`;
  }, [days]);

  return (
    <div className="layout">
      <nav className="sidebar" aria-label="Navegação principal">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <div className="brand"><img src="/icon.svg" alt="" />Hora da Tarefa</div>
        <a href="/">Dashboard</a>
        <a href="/tarefas">Tarefas</a>
        <a href="/calendario" className="active">Calendário</a>
        <a href="/criancas">Crianças</a>
        <a href="/configuracoes">Configurações</a>
      </nav>
      <main className="main">
        <div className="topbar">
          <h1>Calendário</h1>
          <select aria-label="Criança" value={childId} onChange={(e) => setChildId(e.target.value)}>
            {children.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
          </select>
          <div className="spacer" />
          <button className="btn-secondary" onClick={() => setWeekOffset((o) => o - 1)}>‹</button>
          <button className="btn-secondary" onClick={() => setWeekOffset(0)}>Hoje</button>
          <button className="btn-secondary" onClick={() => setWeekOffset((o) => o + 1)}>›</button>
          <strong>{weekLabel}</strong>
        </div>
        {msg && <p className="muted" role="status">{msg}</p>}

        <div style={{ display: "grid", gridTemplateColumns: "repeat(7, 1fr)", gap: 8, overflowX: "auto" }}>
          {days.map((day, wi) => {
            const daySched = schedules.filter((s) => s.weekday === wi).sort((a, b) => a.start_time.localeCompare(b.start_time));
            const dayActs = activities.filter((a) => a.weekday === wi);
            const dayTasks = tasks.filter(
              (t) => (t.scheduled_start && sameDay(day, t.scheduled_start)) || (t.due_at && sameDay(day, t.due_at))
            );
            const isToday = sameDay(new Date(), day.toISOString());
            return (
              <div className="card" key={wi} style={{ minWidth: 140, borderTop: isToday ? "3px solid var(--primary)" : undefined }}>
                <div style={{ fontWeight: 800 }}>{DAYS[wi]}</div>
                <div className="muted">{day.toLocaleDateString("pt-BR", { day: "2-digit", month: "2-digit" })}</div>
                <div style={{ marginTop: 8, display: "flex", flexDirection: "column", gap: 6 }}>
                  {daySched.map((s, i) => (
                    <div key={`s${i}`} style={{ background: "#f1f5f9", borderRadius: 6, padding: "4px 6px", fontSize: 12 }}>
                      <strong>{s.start_time}–{s.end_time}</strong><br />{s.subject}
                    </div>
                  ))}
                  {dayActs.map((a) => (
                    <div key={a.id} style={{ background: "#f3e8ff", borderLeft: "4px solid var(--tertiary)", borderRadius: 6, padding: "4px 6px", fontSize: 12 }}>
                      <strong>{a.start_time}–{a.end_time}</strong><br />{a.title}
                      {a.travel_before_min > 0 && <><br /><span className="muted">+{a.travel_before_min} min desloc.</span></>}
                    </div>
                  ))}
                  {dayTasks.map((t) => (
                    <div key={t.id} style={{ background: "#dcfce7", borderLeft: "4px solid var(--secondary)", borderRadius: 6, padding: "4px 6px", fontSize: 12 }}>
                      <strong>{t.scheduled_start ? hm(t.scheduled_start) : `Entrega ${hm(t.due_at)}`}</strong><br />
                      {t.subject ? `[${t.subject}] ` : ""}{t.title}
                    </div>
                  ))}
                  {daySched.length + dayActs.length + dayTasks.length === 0 && (
                    <span className="muted" style={{ fontSize: 12 }}>Livre</span>
                  )}
                </div>
              </div>
            );
          })}
        </div>

        <div className="card" style={{ marginTop: 16 }}>
          <span className="muted">Legenda: </span>
          <span style={{ background: "#f1f5f9", borderRadius: 6, padding: "2px 8px", fontSize: 12 }}>Aulas</span>{" "}
          <span style={{ background: "#f3e8ff", borderRadius: 6, padding: "2px 8px", fontSize: 12 }}>Atividades extras</span>{" "}
          <span style={{ background: "#dcfce7", borderRadius: 6, padding: "2px 8px", fontSize: 12 }}>Tarefas agendadas/entregas</span>
        </div>
      </main>
    </div>
  );
}
