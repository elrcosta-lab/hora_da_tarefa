"""Bot Telegram — handlers puros + outbox in-memory (SPECS §6 v1.1, RF-08).

MVP sem aiogram: parseia Update do Telegram direto no webhook.
Download real do arquivo (getFile) entra na fase infra; aqui a foto gera
homework via bytes sintetizados válidos (pipeline real de download só injeta bytes).
Idempotência por update_id (TTL 24h na fase Redis; aqui set em memória).
"""
import base64
import hashlib
import io


_SEEN: set[int] = set()
_OUTBOX: dict[int, list[str]] = {}


def clear_bot() -> None:
    _SEEN.clear()
    _OUTBOX.clear()


def _send(chat_id: int, text: str) -> None:
    _OUTBOX.setdefault(int(chat_id), []).append(text)


def last_sent(chat_id: int) -> str | None:
    msgs = _OUTBOX.get(int(chat_id), [])
    return msgs[-1] if msgs else None


def sent_count(chat_id: int) -> int:
    return len(_OUTBOX.get(int(chat_id), []))


def _link_code(chat_id: int) -> str:
    h = hashlib.sha256(f"hora-da-tarefa:{chat_id}".encode()).hexdigest()
    return f"{int(h[:8], 16) % 900000 + 100000}"


def _ensure_child():
    from app.tasks import routine as R

    kids = R.list_children()
    if kids:
        return kids[0]
    return R.create_child("Filho")


def _synthesize_photo_bytes(caption: str | None = None) -> bytes:
    from PIL import Image

    seed = abs(hash(caption or "tarefa")) % 200
    img = Image.new("RGB", (800, 600), (100 + seed % 100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _extract_photo_bytes(message: dict) -> bytes:
    b64 = message.get("test_bytes_b64")
    if b64:
        try:
            return base64.b64decode(b64)
        except Exception:
            pass
    return _synthesize_photo_bytes(message.get("caption"))


def _find_homework(prefix: str):
    from app.tasks.extract import list_homeworks

    prefix = (prefix or "").strip()
    if not prefix:
        return None
    for r in list_homeworks():
        if r["homework_id"].startswith(prefix):
            return r
    return None


def _force_conclude(homework_id: str) -> bool:
    from app.tasks.extract import get_homework, transition_homework

    rec = get_homework(homework_id)
    if rec is None:
        return False
    if rec.get("status") == "concluida":
        return True
    for nxt in ("agendada", "em_andamento", "concluida"):
        try:
            transition_homework(homework_id, nxt)
        except Exception:
            pass
        if get_homework(homework_id).get("status") == "concluida":
            return True
    # atrasada → concluida direto
    try:
        transition_homework(homework_id, "concluida")
        return True
    except Exception:
        return False


def _render_start(name: str, code: str) -> str:
    return (
        f"Olá, {name}! 👋 Eu sou o Hora da Tarefa.\n"
        f"Envie a foto da tarefa de casa e eu organizo a agenda do seu filho.\n\n"
        f"Para vincular sua conta, use o código: `{code}`"
    )


def handle_update(update: dict) -> dict:
    """Processa um Update do Telegram. Sempre retorna {ok, ...} (nunca levanta)."""
    from app.tasks.extract import get_or_create_homework, list_homeworks

    update_id = update.get("update_id")
    if update_id is None:
        return {"ok": True}
    if update_id in _SEEN:
        return {"ok": True, "deduplicated": True}
    _SEEN.add(update_id)

    # callback_query (botões inline)
    cb = update.get("callback_query")
    if cb:
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id") or cb.get("from", {}).get("id")
        data = cb.get("data", "")
        if data.startswith("concluir:"):
            hid = data.split(":", 1)[1]
            target = _find_homework(hid) or ({"homework_id": hid} if len(hid) >= 32 else None)
            if target and _force_conclude(target["homework_id"]):
                _send(chat_id, "Tudo certo! Tarefa concluída. 🎉")
            else:
                _send(chat_id, "Não encontrei essa tarefa.")
            return {"ok": True}
        if data.startswith("nao_tarefa:"):
            from app.tasks.extract import transition_homework

            hid = data.split(":", 1)[1]
            target = _find_homework(hid)
            if target:
                for nxt in ("cancelada", "arquivada"):
                    try:
                        transition_homework(target["homework_id"], nxt)
                    except Exception:
                        pass
                _send(chat_id, "Entendido, descartei essa tarefa.")
            return {"ok": True}
        _send(chat_id, "Use /ajuda para ver os comandos.")
        return {"ok": True}

    message = update.get("message") or update.get("edited_message") or {}
    chat = message.get("chat", {})
    chat_id = chat.get("id")
    from_user = message.get("from", {})
    name = from_user.get("first_name") or from_user.get("username") or "responsável"
    if chat_id is None:
        return {"ok": True}

    # foto → upload (RF-04)
    if message.get("photo"):
        child = _ensure_child()
        image_bytes = _extract_photo_bytes(message)
        rec, dedup = get_or_create_homework(
            image_bytes, child_id=child["id"], hint_text=message.get("caption")
        )
        if dedup:
            _send(chat_id, "Essa foto já foi registrada. Estou processando. ⏳")
        else:
            _send(chat_id, "Recebi! Analisando... status: processando. ⏳")
        return {"ok": True, "homework_id": rec["homework_id"], "deduplicated": dedup}

    text = (message.get("text") or "").strip()
    if text.startswith("/start"):
        _send(chat_id, _render_start(name, _link_code(chat_id)))
        return {"ok": True}
    if text.startswith("/ajuda") or text.startswith("/help"):
        _send(chat_id, "Comandos: /start /ajuda /hoje /tarefas /criancas /concluir <id>")
        return {"ok": True}
    if text.startswith("/criancas"):
        from app.tasks import routine as R

        kids = R.list_children()
        if not kids:
            _send(chat_id, "Nenhuma criança cadastrada. Use o app web para cadastrar.")
        else:
            _send(chat_id, "Crianças:\n" + "\n".join(f"• {k['name']} ({k['id'][:8]})" for k in kids))
        return {"ok": True}
    if text.startswith("/tarefas") or text.startswith("/pendentes"):
        actives = [r for r in list_homeworks() if r.get("status") in ("pendente", "agendada", "em_andamento", "atrasada")]
        if not actives:
            _send(chat_id, "Nenhuma tarefa ativa. 🎉")
        else:
            lines = [f"• {(r.get('subject') or '?')} — {(r.get('title') or r['homework_id'][:8])} [{r.get('status')}]" for r in actives[:10]]
            _send(chat_id, "Tarefas ativas:\n" + "\n".join(lines))
        return {"ok": True}
    if text.startswith("/hoje"):
        actives = [r for r in list_homeworks() if r.get("status") in ("pendente", "agendada", "em_andamento")]
        if not actives:
            _send(chat_id, "Hoje está livre. Nenhuma tarefa agendada. 🎉")
        else:
            lines = [f"• {(r.get('subject') or '?')} — {(r.get('title') or r['homework_id'][:8])}" for r in actives[:10]]
            _send(chat_id, "Agenda de hoje:\n" + "\n".join(lines))
        return {"ok": True}
    if text.startswith("/concluir"):
        parts = text.split()
        target = _find_homework(parts[1] if len(parts) > 1 else "")
        if target and _force_conclude(target["homework_id"]):
            _send(chat_id, "Tudo certo! Tarefa concluída. 🎉")
        else:
            _send(chat_id, "Uso: /concluir <id> (início do id da tarefa). Veja em /tarefas.")
        return {"ok": True}
    _send(chat_id, "Envie a foto da tarefa ou use /ajuda.")
    return {"ok": True}
