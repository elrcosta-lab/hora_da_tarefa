"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess } from "@/lib/api";
import Sidebar from "@/components/Sidebar";

type Child = { id: string; name: string; grade_level?: string | null };
type SchedEntry = { weekday: number; start_time: string; end_time: string; subject: string; kind?: string };
type Activity = {
  id: string; title: string; weekday?: number | null; start_time: string; end_time: string;
  recurrence: string; travel_before_min: number; travel_after_min: number; is_blocking: boolean;
};

const DAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

export default function CriancasPage() {
  const router = useRouter();
  const [children, setChildren] = useState<Child[]>([]);
  const [childId, setChildId] = useState("");
  const [newName, setNewName] = useState("");
  const [newGrade, setNewGrade] = useState("");
  const [tab, setTab] = useState<"grade" | "atividades">("grade");
  const [schedules, setSchedules] = useState<SchedEntry[]>([]);
  const [activities, setActivities] = useState<Activity[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // form grade
  const [gDay, setGDay] = useState(0);
  const [gStart, setGStart] = useState("07:30");
  const [gEnd, setGEnd] = useState("08:20");
  const [gSubject, setGSubject] = useState("Matemática");
  // form atividade
  const [aTitle, setATitle] = useState("Natação");
  const [aDay, setADay] = useState(2);
  const [aStart, setAStart] = useState("17:00");
  const [aEnd, setAEnd] = useState("18:00");
  const [aTravel, setATravel] = useState(20);
  const [aBlocking, setABlocking] = useState(true);

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const loadChildren = useCallback(async () => {
    const kids = await api<{ items: Child[] }>("/children");
    setChildren(kids.items);
    if (!childId && kids.items.length > 0) setChildId(kids.items[0].id);
  }, [childId]);

  const loadAgenda = useCallback(async (cid: string) => {
    if (!cid) return;
    const ag = await api<{ schedules: SchedEntry[]; activities: Activity[] }>(`/children/${cid}/agenda`);
    setSchedules(ag.schedules.map((s) => ({ ...s })));
    setActivities(ag.activities);
  }, []);

  useEffect(() => {
    if (!getAccess()) return;
    loadChildren().catch(() => router.push("/login"));
  }, [loadChildren, router]);

  useEffect(() => {
    if (childId) loadAgenda(childId).catch((e) => setMsg(e instanceof Error ? e.message : "Falha."));
  }, [childId, loadAgenda]);

  async function addChild(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    try {
      const c = await api<Child>("/children", { method: "POST", body: JSON.stringify({ name: newName, grade_level: newGrade || null }) });
      setNewName("");
      setNewGrade("");
      await loadChildren();
      setChildId(c.id);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha.");
    } finally {
      setBusy(false);
    }
  }

  function addScheduleRow() {
    if (gEnd <= gStart) {
      setMsg("Fim deve ser depois do início.");
      return;
    }
    setSchedules([...schedules, { weekday: gDay, start_time: gStart, end_time: gEnd, subject: gSubject }]);
  }

  async function saveSchedules() {
    setBusy(true);
    setMsg(null);
    try {
      await api(`/children/${childId}/schedules`, {
        method: "POST",
        body: JSON.stringify({ replace: true, entries: schedules }),
      });
      setMsg("Grade salva! ✅");
      await loadAgenda(childId);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao salvar grade.");
    } finally {
      setBusy(false);
    }
  }

  async function addActivity(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api(`/children/${childId}/activities`, {
        method: "POST",
        body: JSON.stringify({
          title: aTitle, weekday: aDay, start_time: aStart, end_time: aEnd,
          recurrence: "weekly", travel_before_min: aTravel, travel_after_min: 0, is_blocking: aBlocking,
        }),
      });
      setMsg("Atividade salva! ✅");
      await loadAgenda(childId);
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="layout">
      <Sidebar active="/criancas" />
      <main className="main">
        <div className="topbar">
          <h1>Crianças</h1>
          <select aria-label="Criança" value={childId} onChange={(e) => setChildId(e.target.value)}>
            {children.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
          </select>
          <div className="spacer" />
          <form onSubmit={addChild} style={{ display: "flex", gap: 8 }}>
            <input type="text" placeholder="Nome" aria-label="Nome da criança" value={newName} onChange={(e) => setNewName(e.target.value)} required style={{ width: 140 }} />
            <input type="text" placeholder="Série (ex. 5º ano)" aria-label="Série" value={newGrade} onChange={(e) => setNewGrade(e.target.value)} style={{ width: 140 }} />
            <button className="btn-primary" disabled={busy}>+ Adicionar</button>
          </form>
        </div>
        {msg && <p className="muted" role="status">{msg}</p>}

        {!childId && <div className="empty">Cadastre uma criança para montar a rotina.</div>}

        {childId && (
          <>
            <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
              <button className={tab === "grade" ? "btn-primary" : "btn-secondary"} onClick={() => setTab("grade")}>Grade escolar</button>
              <button className={tab === "atividades" ? "btn-primary" : "btn-secondary"} onClick={() => setTab("atividades")}>Atividades extras</button>
            </div>

            {tab === "grade" && (
              <div className="card">
                <h2>Grade semanal</h2>
                {schedules.length === 0 && <div className="empty">Sem aulas cadastradas.</div>}
                {schedules.map((s, i) => (
                  <div className="task" key={i}>
                    <div className="row">
                      <strong>{DAYS[s.weekday]}</strong>
                      <span>{s.start_time}–{s.end_time}</span>
                      <span className="chip-mat">{s.subject}</span>
                      <div className="spacer" style={{ flex: 1 }} />
                      <button className="btn-secondary" onClick={() => setSchedules(schedules.filter((_, j) => j !== i))}>Remover</button>
                    </div>
                  </div>
                ))}
                <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginTop: 12 }}>
                  <select aria-label="Dia" value={gDay} onChange={(e) => setGDay(Number(e.target.value))}>
                    {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                  </select>
                  <input type="text" aria-label="Início" value={gStart} onChange={(e) => setGStart(e.target.value)} style={{ width: 80 }} />
                  <input type="text" aria-label="Fim" value={gEnd} onChange={(e) => setGEnd(e.target.value)} style={{ width: 80 }} />
                  <input type="text" aria-label="Matéria" value={gSubject} onChange={(e) => setGSubject(e.target.value)} style={{ width: 150 }} />
                  <button className="btn-secondary" onClick={addScheduleRow}>+ Aula</button>
                  <button className="btn-primary" disabled={busy || schedules.length === 0} onClick={saveSchedules}>Salvar grade</button>
                </div>
                <p className="muted">Salvar substitui a grade inteira (valida sobreposição e duração 30min–8h).</p>
              </div>
            )}

            {tab === "atividades" && (
              <div className="columns">
                <div className="card">
                  <h2>Atividades</h2>
                  {activities.length === 0 && <div className="empty">Nenhuma atividade extra.</div>}
                  {activities.map((a) => (
                    <div className="task" key={a.id}>
                      <div className="row">
                        <strong>{a.title}</strong>
                        <span className="muted">{a.weekday !== null && a.weekday !== undefined ? DAYS[a.weekday] : "—"} {a.start_time}–{a.end_time}</span>
                        {a.is_blocking && <span className="pill pill-agendada">bloqueia agenda</span>}
                      </div>
                      <div className="meta">Deslocamento: {a.travel_before_min} min antes · {a.recurrence}</div>
                    </div>
                  ))}
                </div>
                <div className="card">
                  <h2>Adicionar atividade extra</h2>
                  <form onSubmit={addActivity}>
                    <label htmlFor="a-title">Nome</label>
                    <input id="a-title" type="text" value={aTitle} onChange={(e) => setATitle(e.target.value)} required style={{ width: "100%" }} />
                    <label htmlFor="a-day">Dia da semana</label>
                    <select id="a-day" value={aDay} onChange={(e) => setADay(Number(e.target.value))} style={{ width: "100%" }}>
                      {DAYS.map((d, i) => <option key={d} value={i}>{d}</option>)}
                    </select>
                    <div style={{ display: "flex", gap: 8 }}>
                      <div style={{ flex: 1 }}>
                        <label htmlFor="a-start">Início</label>
                        <input id="a-start" type="text" value={aStart} onChange={(e) => setAStart(e.target.value)} style={{ width: "100%" }} />
                      </div>
                      <div style={{ flex: 1 }}>
                        <label htmlFor="a-end">Fim</label>
                        <input id="a-end" type="text" value={aEnd} onChange={(e) => setAEnd(e.target.value)} style={{ width: "100%" }} />
                      </div>
                    </div>
                    <label htmlFor="a-travel">Deslocamento antes (min)</label>
                    <input id="a-travel" type="text" inputMode="numeric" value={aTravel} onChange={(e) => setATravel(Number(e.target.value) || 0)} style={{ width: "100%" }} />
                    <label style={{ display: "flex", gap: 8, alignItems: "center", marginTop: 12 }}>
                      <input type="checkbox" checked={aBlocking} onChange={(e) => setABlocking(e.target.checked)} style={{ minHeight: 24, width: 24 }} />
                      Bloqueia agenda (motor de slots respeita)
                    </label>
                    <button className="btn-primary" disabled={busy} style={{ width: "100%", marginTop: 12 }}>Salvar atividade</button>
                  </form>
                </div>
              </div>
            )}
          </>
        )}
      </main>
    </div>
  );
}
