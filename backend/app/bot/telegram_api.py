"""Cliente fino da Bot API (SPECS §6/§8.1): getFile download + sendMessage.

Sem aiogram no MVP: httpx direto. Falhas de rede/API levantam TelegramError.
"""
import httpx


class TelegramError(Exception):
    pass


def _base(token: str) -> str:
    return f"https://api.telegram.org/bot{token}"


def get_file_path(token: str, file_id: str, timeout: float = 20.0) -> str:
    try:
        r = httpx.get(f"{_base(token)}/getFile", params={"file_id": file_id}, timeout=timeout)
    except Exception as exc:
        raise TelegramError(f"getFile rede: {exc}") from exc
    try:
        body = r.json()
    except Exception as exc:
        raise TelegramError(f"getFile resposta inválida: {exc}") from exc
    if not body.get("ok"):
        raise TelegramError(f"getFile falhou: {body.get('description', r.text[:200])}")
    path = (body.get("result") or {}).get("file_path")
    if not path:
        raise TelegramError("getFile sem file_path")
    return path


def download_bytes(token: str, file_path: str, timeout: float = 30.0) -> bytes:
    try:
        r = httpx.get(f"https://api.telegram.org/file/bot{token}/{file_path}", timeout=timeout)
    except Exception as exc:
        raise TelegramError(f"download rede: {exc}") from exc
    if r.status_code != 200:
        raise TelegramError(f"download HTTP {r.status_code}")
    if not r.content:
        raise TelegramError("download vazio")
    return r.content


def download_photo(token: str, photos: list, timeout: float = 30.0) -> bytes:
    """Baixa a maior foto do array message.photo."""
    if not photos:
        raise TelegramError("mensagem sem photo")
    biggest = max(photos, key=lambda p: (p.get("file_size") or 0, p.get("width") or 0))
    file_id = biggest.get("file_id")
    if not file_id:
        raise TelegramError("photo sem file_id")
    return download_bytes(token, get_file_path(token, file_id, timeout), timeout)


def send_message(token: str, chat_id: int, text: str, timeout: float = 20.0) -> int | None:
    """Envia mensagem; retorna telegram_message_id. Levanta TelegramError em falha."""
    try:
        r = httpx.post(f"{_base(token)}/sendMessage",
                       json={"chat_id": chat_id, "text": text}, timeout=timeout)
    except Exception as exc:
        raise TelegramError(f"sendMessage rede: {exc}") from exc
    try:
        body = r.json()
    except Exception as exc:
        raise TelegramError(f"sendMessage resposta inválida: {exc}") from exc
    if not body.get("ok"):
        raise TelegramError(f"sendMessage falhou: {body.get('description', r.text[:200])}")
    return (body.get("result") or {}).get("message_id")
