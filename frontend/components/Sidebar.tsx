"use client";

import { useState } from "react";

const LINKS: [string, string][] = [
  ["/", "Dashboard"],
  ["/tarefas", "Tarefas"],
  ["/calendario", "Calendário"],
  ["/criancas", "Crianças"],
  ["/configuracoes", "Configurações"],
];

export default function Sidebar({ active, onLogout }: { active: string; onLogout?: () => void }) {
  const [open, setOpen] = useState(false);
  return (
    <>
      <button
        className="menu-btn" aria-label="Abrir menu" aria-expanded={open}
        onClick={() => setOpen((o) => !o)}
      >
        ☰
      </button>
      {open && <div className="backdrop" aria-hidden onClick={() => setOpen(false)} />}
      <nav className={`sidebar${open ? " open" : ""}`} aria-label="Navegação principal">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <div className="brand"><img src="/icon.svg" alt="" />Hora da Tarefa</div>
        {LINKS.map(([href, label]) => (
          <a
            key={href} href={href}
            className={active === href ? "active" : undefined}
            aria-current={active === href ? "page" : undefined}
            onClick={() => setOpen(false)}
          >
            {label}
          </a>
        ))}
        <div className="spacer" />
        {onLogout && (
          <a href="#" onClick={(e) => { e.preventDefault(); onLogout(); }}>Sair</a>
        )}
      </nav>
    </>
  );
}
