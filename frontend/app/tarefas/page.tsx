"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess } from "@/lib/api";

type Child = { id: string; name: string };
type Item = {
  id: string; child_id: string; subject?: string | null; title?: string | null;
  due_at?: string | null; status: string; extraction_confidence?: number | null;
  estimated_minutes?: number | null;
};
type Detail = Item & { statement?: string | null; scheduled_start?: string | null; scheduled_end?: string | null };
type Suggestion = { rank: number; start_at: string; end_at: string; score: number; reason: string };

const STATUSES = ["pendente", "agendada", "em_andamento", "concluida", "atrasada", "cancelada", "arquivada"];

function fmt(iso?: string | null) {
  if (!iso) return "—";
  return new Date(iso).toLocaleString("pt-BR", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" });
}

export default function TarefasPage() {
  const router = useRouter();
  const [children, setChildren] = useState<Child[]>([]);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [fChild, setFChild] = useState("");
  const [fSubject, setFSubject] = useState("");
  const [fStatus, setFStatus] = useState("");
  const [fQ, setFQ] = useState("");
  const [detail, setDetail] = useState<Detail | null>(null);
  const [sugs, setSugs] = useState<Suggestion[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [eSubject, setESubject] = useState("");
  const [eTitle, setETitle] = useState("");
  const [eStatement, setEStatement] = useState("");
  const [eDue, setEDue] = useState("");
  const [eMinutes, setEMinutes] = useState("");

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const load = useCallback(async () => {
    const kids = await api<{ items: Child[] }>("/children");
    setChildren(kids.items);
    const p = new URLSearchParams({ page_size: "50", sort: "-due_at" });
    if (fChild) p.set("child_id", fChild);
    if (fSubject) p.set("subject", fSubject);
    if (fStatus) p.set("status", fStatus);
    if (fQ) p.set("q", fQ);
    const res = await api<{ items: Item[]; total: number }>(`/homeworks?${p}`);
    setItems(res.items);
    setTotal(res.total);
  }, [fChild, fSubject, fStatus, fQ]);

  useEffect(() => {
    if (!getAccess()) return;
    load().catch(() => router.push("/login"));
  }, [load, router]);

  async function openDetail(id: string) {
    const d = await api<Detail>(`/homeworks/${id}`);
    setDetail(d);
    setEditing(false);
    setESubject(d.subject || "");
    setETitle(d.title || "");
    setEStatement(d.statement || "");
    setEDue(d.due_at ? d.due_at.slice(0, 10) : "");
    setEMinutes(d.estimated_minutes ? String(d.estimated_minutes) : "");
    try {
      const s = await api<{ suggestions: Suggestion[] }>(`/suggestions?homework_id=${id}&limit=5`);
      setSugs(s.suggestions);
    } catch {
      setSugs([]);
    }
  }

  async function saveEdit() {
    if (!detail) return;
    setBusy(true);
    try {
      await api(`/homeworks/${detail.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          subject: eSubject || null,
          title: eTitle || null,
          statement: eStatement || null,
          due_at: eDue || null,
          estimated_minutes: eMinutes ? Number(eMinutes) : null,
        }),
      });
      setEditing(false);
      await openDetail(detail.id);
      await load();
      setMsg("Revisão salva! ✅");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao salvar.");
    } finally {
      setBusy(false);
    }
  }

  async function accept(start_at: string) {
    if (!detail) return;
    setBusy(true);
    try {
      await api(`/homeworks/${detail.id}/accept`, { method: "POST", body: JSON.stringify({ start_at }) });
      await openDetail(detail.id);
      await load();
      setMsg("Horário agendado! ✅");
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Falha ao agendar.");
    } finally {
      setBusy(false);
    }
  }

  async function setStatus(status: string) {
    if (!detail) return;
    setBusy(true);
    try {
      await api(`/homeworks/${detail.id}/status`, { method: "PATCH", body: JSON.stringify({ status }) });
      await openDetail(detail.id);
      await load();
    } catch (err) {
      setMsg(err instanceof Error ? err.message : "Transição inválida.");
    } finally {
      setBusy(false);
    }
  }

  async function exportCsv() {
    const p = new URLSearchParams();
    if (fChild) p.set("child_id", fChild);
    if (fSubject) p.set("subject", fSubject);
    if (fStatus) p.set("status", fStatus);
    if (fQ) p.set("q", fQ);
    const token = getAccess();
    const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
    const r = await fetch(`${base}/v1/homeworks/export?${p}`, { headers: { Authorization: `Bearer ${token}` } });
    if (!r.ok) {
      setMsg("Falha ao exportar.");
      return;
    }
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = "tarefas.csv";
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div className="layout">
      <nav className="sidebar" aria-label="Navegação principal">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <div className="brand"><img src="/icon.svg" alt="" />Hora da Tarefa</div>
        <a href="/">Dashboard</a>
        <a href="/tarefas" className="active">Tarefas</a>
        <a href="/calendario">Calendário</a>
        <a href="/criancas">Crianças</a>
        <a href="/configuracoes">Configurações</a>
      </nav>
      <main className="main">
        <div className="topbar">
          <h1>Tarefas</h1>
          <div className="spacer" />
          <button className="btn-secondary" onClick={exportCsv}>Exportar CSV</button>
        </div>
        {msg && <p className="muted" role="status">{msg}</p>}

        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select aria-label="Filtrar por criança" value={fChild} onChange={(e) => setFChild(e.target.value)}>
              <option value="">Todas as crianças</option>
              {children.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
            </select>
            <input
              type="text" placeholder="Matéria" aria-label="Filtrar por matéria"
              value={fSubject} onChange={(e) => setFSubject(e.target.value)} style={{ width: 140 }}
            />
            <select aria-label="Filtrar por status" value={fStatus} onChange={(e) => setFStatus(e.target.value)}>
              <option value="">Todos os status</option>
              {STATUSES.map((s) => <option key={s} value={s}>{s.replace("_", " ")}</option>)}
            </select>
            <input
              type="text" placeholder="Buscar no enunciado…" aria-label="Busca"
              value={fQ} onChange={(e) => setFQ(e.target.value)} style={{ flex: 1, minWidth: 160 }}
            />
            <button className="btn-secondary" onClick={() => { setFChild(""); setFSubject(""); setFStatus(""); setFQ(""); }}>
              Limpar
            </button>
          </div>
        </div>

        <div className="card">
          <p className="muted">Mostrando {items.length} de {total}</p>
          {items.length === 0 && <div className="empty">Nenhuma tarefa com esses filtros.</div>}
          {items.map((t) => (
            <div className="task" key={t.id}>
              <div className="row">
                {t.subject && <span className="chip-mat">{t.subject}</span>}
                <span className={`pill pill-${t.status}`}>{t.status.replace("_", " ")}</span>
                <a href="#" onClick={(e) => { e.preventDefault(); openDetail(t.id); }} className="title">
                  {t.title || "Tarefa"}
                </a>
              </div>
              <div className="meta">Entrega: {fmt(t.due_at)}{t.estimated_minutes ? ` · ~${t.estimated_minutes} min` : ""}</div>
            </div>
          ))}
        </div>

        {detail && (
          <div className="card" style={{ marginTop: 16 }} role="dialog" aria-label="Detalhe da tarefa">
            <div className="topbar">
              <h2 style={{ margin: 0 }}>{detail.title || "Tarefa"}</h2>
              <div className="spacer" />
              <button className="btn-secondary" onClick={() => setDetail(null)}>Fechar</button>
            </div>
            <p><strong>Matéria:</strong> {detail.subject || "—"} · <strong>Status:</strong> {detail.status} · <strong>Entrega:</strong> {fmt(detail.due_at)}</p>
            {detail.statement && !editing && <p>{detail.statement}</p>}
            {!editing ? (
              <button className="btn-secondary" onClick={() => setEditing(true)}>Revisar dados</button>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                <input type="text" aria-label="Matéria" value={eSubject} onChange={(e) => setESubject(e.target.value)} style={{ width: 140 }} />
                <input type="text" aria-label="Título" value={eTitle} onChange={(e) => setETitle(e.target.value)} style={{ flex: "2 1 180px" }} />
                <input type="text" aria-label="Entrega AAAA-MM-DD" value={eDue} onChange={(e) => setEDue(e.target.value)} style={{ width: 130 }} />
                <input type="text" aria-label="Minutos" value={eMinutes} onChange={(e) => setEMinutes(e.target.value)} style={{ width: 70 }} />
                <input type="text" aria-label="Enunciado" value={eStatement} onChange={(e) => setEStatement(e.target.value)} style={{ flex: "1 1 100%" }} />
                <button className="btn-primary" disabled={busy} onClick={saveEdit}>Salvar revisão</button>
                <button className="btn-secondary" onClick={() => setEditing(false)}>Cancelar</button>
              </div>
            )}
            {detail.scheduled_start && <p className="muted">Agendada para: {fmt(detail.scheduled_start)}</p>}

            {sugs.length > 0 && (
              <>
                <h3>Melhores horários</h3>
                {sugs.map((s) => (
                  <div className="task" key={s.rank}>
                    <div className="row">
                      <strong>#{s.rank} {fmt(s.start_at)}</strong>
                      <span className="muted">score {s.score}</span>
                      <button className="btn-primary" disabled={busy} onClick={() => accept(s.start_at)}>Agendar</button>
                    </div>
                    <div className="meta">{s.reason}</div>
                  </div>
                ))}
              </>
            )}

            <div style={{ display: "flex", gap: 8, marginTop: 12, flexWrap: "wrap" }}>
              <button className="btn-success" disabled={busy} onClick={() => setStatus("concluida")}>Concluir</button>
              <button className="btn-secondary" disabled={busy} onClick={() => setStatus("cancelada")}>Cancelar</button>
              <button className="btn-secondary" disabled={busy} onClick={() => setStatus("arquivada")}>Arquivar</button>
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
