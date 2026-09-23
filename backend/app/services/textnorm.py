"""Normalização taxonômica de matérias (aceso/apelido/professor grudado → canônico).

Observado ao vivo: grade ITC ("MATEMATICA ELOISA") e foto ("MATEMATICA" sem
acento) caíam em "Outro" por comparação exata, quebrando o elo tarefa→grade.
"""
import unicodedata

TAXONOMY = [
    "Matemática", "Português", "Redação", "Ciências", "Biologia",
    "Física", "Química", "História", "Geografia", "Inglês", "Espanhol",
    "Artes", "Educação Física", "Ensino Religioso",
]


def _norm(s: str | None) -> str:
    return unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().upper().strip()


def normalize_subject(raw: str | None) -> tuple[str, bool]:
    """Retorna (canônico, mudou?). Desconhecido → ("Outro", True se raw não vazio)."""
    if not (raw or "").strip():
        return "Outro", False
    n = _norm(raw)
    for canonical in sorted(TAXONOMY, key=lambda c: -len(_norm(c))):
        cn = _norm(canonical)
        token = n.split(" ")[0]
        if cn in n or cn.startswith(token) or token.startswith(cn):
            return canonical, canonical != raw.strip()
    return "Outro", True
