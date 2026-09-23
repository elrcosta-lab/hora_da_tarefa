"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getAccess } from "@/lib/api";
import Sidebar from "@/components/Sidebar";
type Child = { id: string; name: string };
type Item = {
  id: string; child_id: string; subject?: string | null; title?: string | null;
  due_at?: string | null; status: string; extraction_confidence?: number | null;
  estimated_minutes?: number | null;
};
type Detail = Item & { statement?: string | null; scheduled_start?: string | null; scheduled_end?: string | null };
type Suggestion = { rank: number; start_at: string; end_at: string; score: number; reason: string };

const STATUSES = ["pendente", "agendada", "em_andamento", "concluida", "atrasada", "cancelada", "arquivada"];
const SUBJECTS = ["Matemática", "Português", "Redação", "Ciências", "Biologia", "Física", "Química", "História", "Geografia", "Inglês", "Espanhol", "Artes", "Educação Física", "Ensino Religioso", "Outro"];

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
  const [dChild, setDChild] = useState("");
  const [dSubject, setDSubject] = useState("");
  const [dStatus, setDStatus] = useState("");
  const [dQ, setDQ] = useState("");
  // B1: debounce 400ms — evita request por tecla e respostas fora de ordem
  useEffect(() => {
    const t = setTimeout(() => {
      setDChild(fChild);
      setDSubject(fSubject);
      setDStatus(fStatus);
      setDQ(fQ);
    }, 400);
    return () => clearTimeout(t);
  }, [fChild, fSubject, fStatus, fQ]);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [sugs, setSugs] = useState<Suggestion[]>([]);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [imgUrl, setImgUrl] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const loadAbort = useRef<AbortController | null>(null);
  const detailReq = useRef(0);
  const imgUrlRef = useRef<string | null>(null);
  useEffect(() => {
    imgUrlRef.current = imgUrl;
    return () => {
      if (imgUrlRef.current) URL.revokeObjectURL(imgUrlRef.current);
    };
  }, [imgUrl]);
  const [editing, setEditing] = useState(false);
  const [eSubject, setESubject] = useState("");
  const [eTitle, setETitle] = useState("");
  const [eStatement, setEStatement] = useState("");
  const [eDue, setEDue] = useState("");
  const [eMinutes, setEMinutes] = useState("");

  useEffect(() => {
    if (!getAccess()) router.push("/login");
  }, [router]);

  const load = useCallback(async (signal?: AbortSignal) => {
    const kids = await api<{ items: Child[] }>("/children", { signal } as RequestInit);
    setChildren(kids.items);
    const p = new URLSearchParams({ page_size: "50", sort: "-due_at" });
    if (dChild) p.set("child_id", dChild);
    if (dSubject) p.set("subject", dSubject);
    if (dStatus) p.set("status", dStatus);
    if (dQ) p.set("q", dQ);
    const res = await api<{ items: Item[]; total: number }>(`/homeworks?${p}`, { signal } as RequestInit);
    setItems(res.items);
    setTotal(res.total);
  }, [dChild, dSubject, dStatus, dQ]);

  useEffect(() => {
    if (!msg) return;
    const t = setTimeout(() => setMsg(null), 8000);
    return () => clearTimeout(t);
  }, [msg]);

  // Escape fecha o detalhe (a11y)
  useEffect(() => {
    if (!detail) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setDetail(null);
        if (imgUrlRef.current) {
          URL.revokeObjectURL(imgUrlRef.current);
          imgUrlRef.current = null;
        }
        setImgUrl(null);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [detail]);

  useEffect(() => {
    if (!getAccess()) return;
    loadAbort.current?.abort();
    const ctl = new AbortController();
    loadAbort.current = ctl;
    setLoading(true);
    load(ctl.signal)
      .catch((err) => {
        if (err instanceof DOMException && err.name === "AbortError") return;
        router.push("/login");
      })
      .finally(() => {
        if (loadAbort.current === ctl) setLoading(false);
      });
    return () => ctl.abort();
  }, [load, router]);

  async function openDetail(id: string) {
    const req = ++detailReq.current;
    setDetailLoading(true);
    setSugs([]);
    if (imgUrlRef.current) {
      URL.revokeObjectURL(imgUrlRef.current);
      imgUrlRef.current = null;
    }
    setImgUrl(null);
    let d: Detail;
    try {
      d = await api<Detail>(`/homeworks/${id}`);
      if (detailReq.current !== req) return; // B2: resposta tardia descarta
      setDetail(d);
    } catch {
      if (detailReq.current === req) {
        setMsg("Falha ao abrir a tarefa.");
        setDetailLoading(false);
      }
      return;
    }
    try {
      const base = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
      const token = getAccess();
      const r = await fetch(`${base}/v1/homeworks/${id}/image`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (detailReq.current !== req) return;
      if (r.ok) setImgUrl(URL.createObjectURL(await r.blob()));
    } catch { /* sem foto: segue sem imagem */ }
    if (detailReq.current !== req) return;
    setEditing(false);
    setESubject(d.subject || "");
    setETitle(d.title || "");
    setEStatement(d.statement || "");
    setEDue(d.due_at ? d.due_at.slice(0, 10) : "");
    setEMinutes(d.estimated_minutes ? String(d.estimated_minutes) : "");
    try {
      const s = await api<{ suggestions: Suggestion[] }>(`/suggestions?homework_id=${id}&limit=5`);
      if (detailReq.current !== req) return;
      setSugs(s.suggestions);
    } catch {
      if (detailReq.current !== req) return;
      setSugs([]);
    } finally {
      if (detailReq.current === req) setDetailLoading(false);
    }
  }

  async function saveEdit() {
    if (!detail) return;
    setBusy(true);
    try {
      // B5: envia só campos alterados (vazio mantém o valor atual)
      const patch: Record<string, string | number | null> = {};
      if (eSubject !== (detail.subject || "")) patch.subject = eSubject || null;
      if (eTitle !== (detail.title || "")) patch.title = eTitle || null;
      if (eStatement !== (detail.statement || "")) patch.statement = eStatement || null;
      const dueIso = eDue || null;
      const curDue = detail.due_at ? detail.due_at.slice(0, 10) : null;
      if (dueIso !== curDue) patch.due_at = dueIso;
      const mins = eMinutes ? Number(eMinutes) : null;
      if ((mins || null) !== (detail.estimated_minutes ?? null)) patch.estimated_minutes = mins;
      if (Object.keys(patch).length === 0) {
        setEditing(false);
        return;
      }
      await api(`/homeworks/${detail.id}`, { method: "PATCH", body: JSON.stringify(patch) });
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

  // U1: ações válidas por estado — nunca exibe o impossível
  const TERMINAL = ["concluida", "cancelada", "nao_realizada", "arquivada"];
  const isTerminal = detail ? TERMINAL.includes(detail.status) : false;
  const canSchedule = detail ? ["pendente", "agendada"].includes(detail.status) : false;
  const canConclude = detail ? ["pendente", "agendada", "em_andamento", "atrasada"].includes(detail.status) : false;
  const canCancel = detail ? ["pendente", "agendada", "em_andamento", "atrasada"].includes(detail.status) : false;
  const canArchive = detail ? ["concluida", "cancelada", "nao_realizada"].includes(detail.status) : false;

  async function setStatus(status: string) {
    if (!detail) return;
    setBusy(true);
    try {
      // caminha a FSM até o alvo (ex.: pendente → em_andamento → concluída)
      // B4: arquivar nunca transita por concluída sozinho — só de estado terminal
      const path: string[] =
        status === "concluida"
          ? ["agendada", "em_andamento", "concluida"]
          : [status];
      let lastError: unknown = null;
      for (const st of path) {
        try {
          await api(`/homeworks/${detail.id}/status`, { method: "PATCH", body: JSON.stringify({ status: st }) });
          if (st === status) {
            lastError = null;
            break;
          }
          lastError = null;
        } catch (err) {
          lastError = err;
        }
      }
      if (lastError) throw lastError;
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
      <Sidebar active="/tarefas" />
      <main className="main">
        <div className="topbar">
          <h1>Tarefas</h1>
          <div className="spacer" />
          <button className="btn-secondary" onClick={exportCsv}>Exportar CSV</button>
        </div>
        {msg && (
          <p className="muted" role="status">
            {msg}{" "}
            <button className="btn-secondary" style={{ minHeight: 32, padding: "2px 10px" }} onClick={() => setMsg(null)} aria-label="Dispensar mensagem">
              ×
            </button>
          </p>
        )}

        <div className="card" style={{ marginBottom: 16 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <select aria-label="Filtrar por criança" value={fChild} onChange={(e) => setFChild(e.target.value)}>
              <option value="">Todas as crianças</option>
              {children.map((k) => <option key={k.id} value={k.id}>{k.name}</option>)}
            </select>
            <select aria-label="Filtrar por matéria" value={fSubject} onChange={(e) => setFSubject(e.target.value)}>
              <option value="">Todas as matérias</option>
              {SUBJECTS.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
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
          {loading && [0, 1, 2].map((i) => <div className="skeleton" key={i} style={{ height: 64, marginBottom: 10 }} />)}
          {!loading && items.length === 0 && <div className="empty">Nenhuma tarefa com esses filtros.</div>}
          {items.map((t) => (
            <div
              className="task" key={t.id} role="button" tabIndex={0}
              style={{ cursor: "pointer" }}
              onClick={() => openDetail(t.id)}
              onKeyDown={(e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openDetail(t.id); } }}
              aria-label={`Abrir detalhe: ${t.title || "Tarefa"}`}
            >
              <div className="row">
                {t.subject && <span className="chip-mat">{t.subject}</span>}
                <span className={`pill pill-${t.status}`}>{t.status.replace("_", " ")}</span>
                <span className="title">{t.title || "Tarefa"}</span>
              </div>
              <div className="meta">Entrega: {fmt(t.due_at)}{t.estimated_minutes ? ` · ~${t.estimated_minutes} min` : ""}</div>
            </div>
          ))}
        </div>

        {detail && (
          <div className="card" style={{ marginTop: 16 }} role="dialog" aria-modal="true" aria-label="Detalhe da tarefa">
            <div className="topbar">
              <h2 style={{ margin: 0 }}>{detail.title || "Tarefa"}</h2>
              <div className="spacer" />
              <button className="btn-secondary" onClick={() => { setDetail(null); if (imgUrl) { URL.revokeObjectURL(imgUrl); setImgUrl(null); } }}>Fechar</button>
            </div>
            <p><strong>Matéria:</strong> {detail.subject || "—"} · <strong>Status:</strong> {detail.status} · <strong>Entrega:</strong> {fmt(detail.due_at)}</p>
            {detailLoading ? (
              <div className="skeleton" style={{ height: 200 }} role="status" aria-label="Carregando detalhe" />
            ) : (
              <>
                {imgUrl ? (
                  <button
                    onClick={() => window.open(imgUrl, "_blank", "noopener")}
                    style={{ display: "block", width: "100%", padding: 0, border: 0, background: "#f1f5f9", borderRadius: 8, cursor: "zoom-in", minHeight: 200 }}
                    aria-label="Ampliar foto da tarefa"
                  >
                    {/* eslint-disable-next-line @next/next/no-img-element */}
                    <img src={imgUrl} alt={`Foto da tarefa ${detail.title || ""}`} style={{ width: "100%", height: 320, objectFit: "contain", borderRadius: 8, display: "block" }} />
                  </button>
                ) : (
                  <div className="empty">Sem foto disponível para esta tarefa.</div>
                )}
              </>
            )}
            {detail.statement && !editing && <p>{detail.statement}</p>}
            {!editing ? (
              <button className="btn-secondary" onClick={() => setEditing(true)}>Revisar dados</button>
            ) : (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", marginBottom: 8 }}>
                <input type="text" aria-label="Matéria" value={eSubject} onChange={(e) => setESubject(e.target.value)} style={{ width: 140 }} />
                <input type="text" aria-label="Título" value={eTitle} onChange={(e) => setETitle(e.target.value)} style={{ flex: "2 1 180px" }} />
                <input type="date" aria-label="Entrega" value={eDue} onChange={(e) => setEDue(e.target.value)} style={{ width: 150 }} />
                <input type="text" aria-label="Minutos" value={eMinutes} onChange={(e) => setEMinutes(e.target.value)} style={{ width: 70 }} />
                <input type="text" aria-label="Enunciado" value={eStatement} onChange={(e) => setEStatement(e.target.value)} style={{ flex: "1 1 100%" }} />
                <button className="btn-primary" disabled={busy} onClick={saveEdit}>Salvar revisão</button>
                <button className="btn-secondary" onClick={() => setEditing(false)}>Cancelar</button>
              </div>
            )}
            {detail.scheduled_start && <p className="muted">Agendada para: {fmt(detail.scheduled_start)}</p>}

            {canSchedule && sugs.length > 0 && (
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
              {canConclude && <button className="btn-success" disabled={busy} onClick={() => setStatus("concluida")}>Concluir</button>}
              {canCancel && <button className="btn-secondary" disabled={busy} onClick={() => setStatus("cancelada")}>Cancelar</button>}
              {canArchive && <button className="btn-secondary" disabled={busy} onClick={() => setStatus("arquivada")}>Arquivar</button>}
              {isTerminal && !canArchive && <span className="muted">Tarefa finalizada.</span>}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}
