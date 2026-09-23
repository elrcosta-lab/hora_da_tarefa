"""Bot Telegram — handlers puros + outbox (SPECS §6 v1.1, RF-08).

MVP sem aiogram: parseia Update direto no webhook. Fotos baixadas via getFile
(app.bot.telegram_api); test_bytes_b64 existe só como backdoor de testes.
Outbox em memória alimenta os testes; com TELEGRAM_LIVE_SEND=true o router
descarrega via sendMessage em background. Idempotência por update_id.
"""
import hashlib


def drain_outbox() -> list[tuple[int, str]]:
    """Remove e retorna todas as mensagens pendentes (flush p/ Bot API)."""
    items: list[tuple[int, str]] = []
    for chat_id, msgs in list(_OUTBOX.items()):
        for text in msgs:
            items.append((chat_id, text))
    _OUTBOX.clear()
    return items


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


def _ensure_child(owner_user_id: str | None = None):
    from app.tasks import routine as R

    kids = R.list_children(owner_user_id=owner_user_id) if owner_user_id else R.list_children()
    if kids:
        return kids[0]
    return R.create_child("Filho", owner_user_id=owner_user_id)


def _extract_photo_bytes(message: dict, token: str | None = None) -> bytes:
    import base64 as _b64
    import os as _os

    from app.bot import telegram_api as _tg

    # A4: backdoor só com flag explícita (fora dos testes, Telegram nunca envia esse campo)
    if _os.environ.get("ALLOW_TEST_BYTES") == "true":
        b64 = message.get("test_bytes_b64")
        if b64:
            try:
                return _b64.b64decode(b64)
            except Exception:
                pass
    photos = message.get("photo") or []
    if not photos:
        raise _tg.TelegramError("mensagem sem photo")
    return _tg.download_photo(token or "", photos)


def _find_homework(prefix: str, owner_user_id: str | None = None):
    from app.tasks.extract import list_homeworks

    prefix = (prefix or "").strip()
    if not prefix:
        return None
    for r in list_homeworks(owner_user_id=owner_user_id):
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


RESTRICTED_MSG = (
    "🔒 Acesso restrito a usuários cadastrados na plataforma.\n"
    "Peça seu código de vínculo de 6 dígitos e envie aqui (ou /start <código>)."
)


def _render_dedupe(rec: dict) -> str:
    """Reenvio: mostra o estado atual em vez de 'processando' genérico."""
    subject = rec.get("subject") or "tarefa"
    title = rec.get("title") or "sem título"
    status = rec.get("status") or "pendente"
    if rec.get("extraction_status") == "processando":
        return f"Essa foto já foi registrada ({subject} — {title}). Ainda analisando… ⏳"
    due = rec.get("due_at") or "a confirmar"
    sched = rec.get("scheduled_start")
    extra = f"\nAgendada para: {sched[:16].replace('T', ' ')}" if sched else ""
    return (f"Essa foto já está registrada:\n{subject} — {title}\n"
            f"Status: {status} · Entrega: {str(due)[:10]}{extra}\n"
            f"Concluiu? /concluir {rec['homework_id'][:8]}")


def _resolve_user(telegram_user_id: int) -> dict | None:
    from app.tasks import users as U

    try:
        return U.get_by_telegram_id(telegram_user_id)
    except Exception:
        return None


def _try_link(chat_id: int, telegram_user_id: int, code: str) -> bool:
    from app.tasks import users as U

    try:
        user = U.link_telegram(code, telegram_user_id)
    except Exception:
        return False
    if user is None:
        return False
    _send(chat_id, f"Conta vinculada com sucesso, {user['name']}! ✅ Agora envie a foto da tarefa.")
    return True


def handle_update(update: dict) -> dict:
    """Processa um Update do Telegram. Sempre retorna {ok, ...} (nunca levanta)."""
    from app.tasks.extract import get_or_create_homework, list_homeworks

    update_id = update.get("update_id")
    if update_id is None:
        return {"ok": True}
    if update_id in _SEEN:
        return {"ok": True, "deduplicated": True}
    _SEEN.add(update_id)

    # callback_query (botões inline) — exige vínculo
    cb = update.get("callback_query")
    if cb:
        chat_id = (cb.get("message") or {}).get("chat", {}).get("id") or cb.get("from", {}).get("id")
        cb_user = (cb.get("from") or {}).get("id")
        owner_cb = (_resolve_user(cb_user) or {}).get("user_id") if cb_user is not None else None
        if owner_cb is None:
            _send(chat_id, RESTRICTED_MSG)
            return {"ok": True, "restricted": True}
        data = cb.get("data", "")
        if data.startswith("concluir:"):
            hid = data.split(":", 1)[1]
            target = _find_homework(hid, owner_cb)
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
            target = _find_homework(hid, owner_cb)
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
    telegram_user_id = from_user.get("id")
    name = from_user.get("first_name") or from_user.get("username") or "responsável"
    if chat_id is None:
        return {"ok": True}

    text = (message.get("text") or "").strip()

    # pareamento: código puro ou /start <código> funcionam mesmo sem vínculo
    code_candidate = None
    if text.startswith("/start"):
        parts = text.split()
        if len(parts) > 1 and parts[1].isdigit():
            code_candidate = parts[1]
    elif text.isdigit() and len(text) == 6:
        code_candidate = text
    if code_candidate and telegram_user_id is not None:
        if _try_link(chat_id, telegram_user_id, code_candidate):
            return {"ok": True, "linked": True}
        _send(chat_id, "Código inválido ou já utilizado. Peça um novo código na plataforma.")
        return {"ok": True}

    # gate: só telegram_user_id vinculados passam daqui
    if telegram_user_id is None or _resolve_user(telegram_user_id) is None:
        if text.startswith("/start"):
            _send(chat_id,
                  f"Olá, {name}! 👋 Eu sou o Hora da Tarefa.\n"
                  f"Envie seu código de vínculo de 6 dígitos aqui (ou /start <código>).")
        else:
            _send(chat_id, RESTRICTED_MSG)
        return {"ok": True, "restricted": True}

    # foto → upload (RF-04): download real via getFile; falha → erro, nada criado
    if message.get("photo"):
        from app.api.homeworks import MAX_BYTES
        from app.bot import telegram_api as _tg
        from app.core.config import get_settings as _get_settings

        try:
            image_bytes = _extract_photo_bytes(message, token=_get_settings().TELEGRAM_BOT_TOKEN)
        except Exception:
            _send(chat_id, "Não consegui baixar a foto. Tente enviar novamente. 📷")
            return {"ok": True, "download_failed": True}
        if len(image_bytes) > MAX_BYTES:
            _send(chat_id, "Foto muito grande (máx. 10 MB). Tente com menos resolução. 📷")
            return {"ok": True, "too_large": True}
        user = _resolve_user(telegram_user_id)
        child = _ensure_child((user or {}).get("user_id"))
        rec, dedup = get_or_create_homework(
            image_bytes, child_id=child["id"], hint_text=message.get("caption"),
            created_by_user_id=(user or {}).get("user_id"),
        )
        if dedup:
            _send(chat_id, _render_dedupe(rec))
        else:
            _send(chat_id, "Recebi! Analisando... status: processando. ⏳")
        return {"ok": True, "homework_id": rec["homework_id"], "deduplicated": dedup}

    if text.startswith("/start"):
        owner = (_resolve_user(telegram_user_id) or {}).get("user_id")
        actives = [r for r in list_homeworks(owner_user_id=owner)
                   if r.get("status") in ("pendente", "agendada", "em_andamento", "atrasada")]
        late = [r for r in actives if r.get("status") == "atrasada"]
        if not actives:
            _send(chat_id, f"Olá, {name}! 👋 Nenhuma tarefa ativa. Envie a foto da próxima lição e eu organizo. 🎉")
        else:
            _send(chat_id, f"Olá, {name}! 👋 Você tem {len(actives)} tarefa(s) ativa(s)"
                           f"{f', {len(late)} atrasada(s) ⚠️' if late else ''}.\n"
                           f"Envie uma foto nova ou use /tarefas, /hoje e /concluir <id>.")
        return {"ok": True}
    if text.startswith("/ajuda") or text.startswith("/help"):
        _send(chat_id, "Comandos: /start /ajuda /hoje /tarefas /criancas /concluir <id>")
        return {"ok": True}
    if text.startswith("/criancas"):
        from app.tasks import routine as R

        owner = (_resolve_user(telegram_user_id) or {}).get("user_id")
        kids = R.list_children(owner_user_id=owner)
        if not kids:
            _send(chat_id, "Nenhuma criança cadastrada. Use o app web para cadastrar.")
        else:
            _send(chat_id, "Crianças:\n" + "\n".join(f"• {k['name']} ({k['id'][:8]})" for k in kids))
        return {"ok": True}
    if text.startswith("/tarefas") or text.startswith("/pendentes"):
        owner = (_resolve_user(telegram_user_id) or {}).get("user_id")
        actives = [r for r in list_homeworks(owner_user_id=owner)
                   if r.get("status") in ("pendente", "agendada", "em_andamento", "atrasada")]
        if not actives:
            _send(chat_id, "Nenhuma tarefa ativa. 🎉")
        else:
            lines = [f"• {(r.get('subject') or '?')} — {(r.get('title') or r['homework_id'][:8])} [{r.get('status')}]" for r in actives[:10]]
            _send(chat_id, "Tarefas ativas:\n" + "\n".join(lines))
        return {"ok": True}
    if text.startswith("/hoje"):
        owner = (_resolve_user(telegram_user_id) or {}).get("user_id")
        actives = [r for r in list_homeworks(owner_user_id=owner)
                   if r.get("status") in ("pendente", "agendada", "em_andamento")]
        if not actives:
            _send(chat_id, "Hoje está livre. Nenhuma tarefa agendada. 🎉")
        else:
            lines = [f"• {(r.get('subject') or '?')} — {(r.get('title') or r['homework_id'][:8])}" for r in actives[:10]]
            _send(chat_id, "Agenda de hoje:\n" + "\n".join(lines))
        return {"ok": True}
    if text.startswith("/concluir"):
        parts = text.split()
        owner = (_resolve_user(telegram_user_id) or {}).get("user_id")
        target = _find_homework(parts[1] if len(parts) > 1 else "", owner)
        if target and _force_conclude(target["homework_id"]):
            _send(chat_id, "Tudo certo! Tarefa concluída. 🎉")
        else:
            _send(chat_id, "Uso: /concluir <id> (início do id da tarefa). Veja em /tarefas.")
        return {"ok": True}
    _send(chat_id, "Envie a foto da tarefa ou use /ajuda.")
    return {"ok": True}
