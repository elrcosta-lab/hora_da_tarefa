"use client";

import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { ChildApiError, ChildMe, childApi, clearChildToken, getChildAccess } from "@/lib/child-api";

// RF-25: tarefas da criança — somente leitura. Sem sidebar, mobile-first.
type Item = {
  id: string; subject?: string | null; title?: string | null; statement?: string | null;
  due_at?: string | null; status: string; scheduled_start?: string | null;
  scheduled_end?: string | null; estimated_minutes?: number | null;
};

const STATUS_EMOJI: Record<string, string> = {
  pendente: "📌",
  agendada: "🗓️",
  em_andamento: "🚀",
  concluida: "✅",
  atrasada: "⏰",
  cancelada: "🚫",
  arquivada: "📦",
};

function fmt(iso?: string | null) {
  if (!iso) return "sem data";
  return new Date(iso).toLocaleString("pt-BR", {
    weekday: "short", day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit",
  });
}

export default function CriancaTarefasPage() {
  const router = useRouter();
  const [me, setMe] = useState<ChildMe | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const logout = useCallback(() => {
    clearChildToken();
    router.replace("/crianca");
  }, [router]);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [m, res] = await Promise.all([
        childApi<ChildMe>("/child/me"),
        childApi<{ items: Item[]; total: number }>("/child/homeworks?page_size=100&sort=due_at"),
      ]);
      setMe(m);
      setItems(res.items);
      setTotal(res.total);
    } catch (err) {
      if (err instanceof ChildApiError && err.code === "UNAUTHORIZED") {
        logout();
        return;
      }
      setError("Não consegui carregar suas tarefas. Tente de novo. 😢");
    } finally {
      setLoading(false);
    }
  }, [logout]);

  useEffect(() => {
    if (!getChildAccess()) {
      router.replace("/crianca");
      return;
    }
    load();
  }, [load, router]);

  return (
    <main className="child-wrap">
      <header className="child-header">
        <div>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/icon.svg" alt="" width={40} height={40} />
          <h1>Minhas tarefas 🎒</h1>
          {me && <p className="muted">{me.name}{me.grade_level ? ` · ${me.grade_level}` : ""}</p>}
        </div>
        <button className="btn-secondary child-logout" onClick={logout} aria-label="Sair">
          Sair 👋
        </button>
      </header>

      {loading && (
        <div role="status" aria-label="Carregando tarefas">
          {[0, 1, 2].map((i) => <div className="skeleton child-skeleton" key={i} />)}
        </div>
      )}

      {!loading && error && (
        <div className="card" role="alert">
          <p>{error}</p>
          <button className="btn-primary" onClick={load}>Tentar de novo 🔄</button>
        </div>
      )}

      {!loading && !error && items.length === 0 && (
        <div className="card child-empty" role="status">
          <p className="child-empty-emoji" aria-hidden>🎉</p>
          <p><strong>Nenhuma tarefa no momento!</strong></p>
          <p className="muted">Aproveite para brincar. 😊</p>
        </div>
      )}

      {!loading && !error && items.length > 0 && (
        <>
          <p className="muted" role="status">
            {total} {total === 1 ? "tarefa" : "tarefas"} para você 📚
          </p>
          <ul className="child-list">
            {items.map((t) => (
              <li className="card child-task" key={t.id}>
                <div className="row">
                  <span aria-hidden>{STATUS_EMOJI[t.status] || "📌"}</span>
                  {t.subject && <span className="chip-mat">{t.subject}</span>}
                  <span className={`pill pill-${t.status}`}>{t.status.replace("_", " ")}</span>
                </div>
                <p className="child-task-title">{t.title || "Tarefa"}</p>
                {t.statement && <p className="child-task-stmt">{t.statement}</p>}
                <p className="meta">
                  📅 Entrega: {fmt(t.due_at)}
                  {t.estimated_minutes ? ` · ⏱️ ~${t.estimated_minutes} min` : ""}
                </p>
                {t.scheduled_start && (
                  <p className="meta">🗓️ Agendada para: {fmt(t.scheduled_start)}</p>
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </main>
  );
}
