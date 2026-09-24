# Auditoria de Segurança — Hora da Tarefa (VPS, 3ª rodada)

- Data: 2026-09-24
- Stack detectada: Next.js 14 (App Router) + FastAPI + SQLAlchemy 2 + Postgres 16 + Redis 7 + MinIO (S3) + JWT HS256 + Argon2id + bot Telegram próprio sobre httpx (polling) + OpenRouter (`nex-agi/nex-n2.5-mini:free`)
- Escopo: código em produção na VPS (`/opt/hora_da_tarefa`, sincronia com o repo verificada por hash em 9 arquivos — idênticos), `backend/` (api, core, tasks, bot, models, services, alembic até `0012`), `frontend/` (app, lib), `docker-compose.yml` + override da VPS, `Caddyfile`, Dockerfiles, `.env.example`, `.gitignore`, histórico git. Sem Supabase/Firebase (V1 adaptada para isolamento por dono no servidor). Somente leitura; nada foi modificado. Foco no delta desde a 2ª rodada (RF-16 aprovação de contas `0012`, aviso `extracao_falhou`, troca p/ modelo `:free`, inferência de entrega, diversidade de slots, bot com id curto, frontend mobile) + regressão dos fixes A1–A7 + estado real da VPS (env, portas, host multi-tenant).

## Status pós-correção (2026-09-24, verificado em produção)

| Item | Status | Evidência |
|---|---|---|
| O1 beat x polling | ✅ corrigido | `_select_sender` (`backend/app/tasks/beat.py:22`); compose respeita `.env`; modo efetivo polling+outbox verificado na VPS |
| A1 import sem teto / LLM no loop | ✅ corrigido | `ROUTINE_TEXT_MAX` + `run_in_threadpool` (`backend/app/api/children.py`); spec §3.7b |
| A2 callback `concluir:` sem escopo | ✅ corrigido | bloco único escopado a `owner_cb` (`backend/app/bot/handlers.py:227`) |
| A3 `S3_ACCESS_KEY` default | ✅ corrigido | valor rotacionado na VPS (gerado server-side); round-trip MinIO OK |
| A4 paginação em memória | ✅ corrigido | `count_homeworks` + `limit/offset` (`backend/app/tasks/extract.py`, rota) |
| Compose `LIVE_SEND`/`MODEL` fixos | ✅ corrigido | `${VAR:-default}` (commit `b7f3058`) |

## Regressão da 2ª rodada (verificada nesta auditoria)

| Achado | Status |
|---|---|
| A1 senha default do admin | ✅ mantém fail-closed (`backend/app/tasks/admin.py:27`); senha viva na VPS confirmada diferente do default histórico (prefixo difere de `Ui4u`) |
| A2 reprocess sem rate limit | ✅ mantém `limit(20/h)` + `limit(5/min)` (`backend/app/api/homeworks.py:181`) |
| A3 exclusão sem purga do storage | ✅ mantém coleta de `storage_key`s + delete via provider (`backend/app/tasks/admin.py:115-146`) |
| A4 backdoor `test_bytes_b64` | ✅ mantém gate `ALLOW_TEST_BYTES` (`backend/app/bot/handlers.py:64`) |
| A5 caption/hint sem teto | ✅ mantém `[:500]` (`backend/app/tasks/extract.py:67`) |
| A6 docs interativas abertas | ✅ mantém `docs_url=None` com `ENV=prod` (`backend/app/main.py:58`); VPS com `ENV=prod` |
| A7 containers como root | ✅ mantém `USER app` nos dois Dockerfiles |

## Resumo executivo

| Severidade | Quantidade |
|---|---|
| Crítica | 0 |
| Alta | 0 |
| Média | 1 |
| Baixa | 3 |

**Prioridade de correção:** A1, depois A2–A4. Atenção: O1 (observações) é defeito funcional ativo — lembretes do beat estão sendo descartados em silêncio na VPS; tratar antes ou junto do A1.

## Achados

### [A1] Import de rotina sem teto de texto + LLM síncrono no loop — backend/app/api/children.py:117 + backend/app/services/vision_openrouter.py:259
- **Severidade:** Média
- **Evidência:**
  ```python
  # backend/app/api/children.py:117-145 (async def, sem background task)
  file: UploadFile | None = File(default=None),
  text: str | None = Form(default=None),
  ...
  result = extract_routine(text=(text or None), image_bytes=image_bytes)  # chamada sync, sem teto
  ```
  ```python
  # backend/app/services/vision_openrouter.py:246-247
  parts: list = [{"type": "text",
                  "text": _ROUTINE_SYSTEM + "\n\n" + anchor + "\n\nRotina:\n" + (text or "").strip()}]
  ```
- **Risco:** conta aprovada envia `text` de até ~11 MB (só o Caddy limita o corpo) direto ao OpenRouter — queima cota/tokens — e a chamada SDK síncrona (até 2×60 s) trava o event loop do worker único, degradando a API inteira para todos; o limite 5/min não impede 5 janelas de 120 s.
- **Correção:**
  ```python
  # backend/app/api/children.py — teto + fora do loop
  from starlette.concurrency import run_in_threadpool
  text = ((text or "").strip())[:20000]
  ...
  result = await run_in_threadpool(extract_routine, text=(text or None), image_bytes=image_bytes)
  ```

### [A2] Callback `concluir:` duplicado com lookup sem escopo de dono — backend/app/bot/handlers.py:227-237
- **Severidade:** Baixa
- **Evidência:**
  ```python
  if data.startswith("concluir:"):
      hid = data.split(":", 1)[1]
      target = _find_homework(hid, owner_cb)        # 1º bloco: resultado descartado
  if data.startswith("concluir:"):                  # condição duplicada
      hid = data.split(":", 1)[1]
      target = _find_homework(hid) or ({"homework_id": hid} if len(hid) >= 32 else None)  # SEM dono + alvo fabricado
      if target and _force_conclude(target["homework_id"]):  # _force_conclude não checa dono
  ```
  (`_find_homework` com `owner_user_id=None` varre **todas** as tarefas — `backend/app/tasks/extract.py:132` só filtra dono `if owner_user_id is not None`.)
- **Risco:** hoje o bloco é inalcançável em produção (o bot nunca envia botões — `send_message` não tem `reply_markup` — e UUIDs não são adivinháveis), mas no dia em que botões forem ligados, um callback forjado conclui tarefa de outra família.
- **Correção:**
  ```python
  if data.startswith("concluir:"):
      hid = data.split(":", 1)[1]
      target = _find_homework(hid, owner_cb)
      if target and _force_conclude(target["homework_id"]):
  ```

### [A3] `S3_ACCESS_KEY` possivelmente ainda default — VPS `.env`
- **Severidade:** Baixa
- **Evidência:** prefixo do valor na VPS é `min` (default do código em `backend/app/core/storage.py:102` é `minioadmin`); demais segredos confirmados rotacionados (prefixos diferem dos defaults/histórico). MinIO sem porta publicada (só rede interna).
- **Risco:** credencial adivinhável para quem alcançar a rede de containers; combinada a qualquer SSRF/RCE futuro, vira leitura das fotos das tarefas.
- **Correção:**
  ```bash
  grep S3_ACCESS_KEY .env | grep -qv minioadmin && echo OK || echo ROTACIONAR
  # rotação: gerar valor forte, atualizar .env, docker compose up -d minio api
  ```

### [A4] Listagem pagina em memória, sem LIMIT/OFFSET no SQL — backend/app/api/homeworks.py:99-104
- **Severidade:** Baixa
- **Evidência:**
  ```python
  items = list_homeworks(child_id=child_id, owner_user_id=owner, ...)
  total = len(items)          # carrega TODAS as linhas do dono...
  page_items = items[start : start + page_size]  # ...para fatiar em Python (saída ≤100)
  ```
- **Risco:** conta com dezenas de milhares de tarefas transforma cada listagem em full scan + desserialização (carga de banco/CPU por request autenticado; saída continua tetada em 100).
- **Correção:** paginar no SQL (`limit(page_size).offset(start)` + `count()` separado para `total`).

## Observações adicionais

- **O1 — Beat x polling (CORRIGIDO; leitura inicial ajustada):** a primeira versão deste relatório afirmava perda total em polling — a verificação de deploy revelou que o `docker-compose.yml` fixava `TELEGRAM_LIVE_SEND: "true"`, que prevalece sobre o `.env` (`false`); ou seja, o beat entregava direto via Bot API e não havia perda. A contradição (env morto + fallback ausente) era a armadilha real: corrigida com `_select_sender` (`backend/app/tasks/beat.py`) — live→direto, polling→outbox (descarregado pelo flush), senão nada — e compose passando a respeitar o `.env`. Modo efetivo na VPS agora: polling+outbox (verificado em produção).
- **O2 — Sem TLS (persiste da 2ª rodada):** API (`:8081` via Caddy) e web (`:3100`) em HTTP puro, sem domínio; JWT/senhas trafegam em claro até a VPS. O nginx do host tem TLS, mas serve só os outros tenants (`amarelinho`, `forensis`, `uga-carbon`) — nada nosso passa por ele.
- **O3 — LGPD/trânsito internacional:** extração agora no `:free` (Nex AGI) + Telegram seguem recebendo dado de menor — citar nominalmente no termo de consentimento.
- **O4 — Override na VPS contradiz a doc:** `/opt/hora_da_tarefa/docker-compose.override.yml` existe na VPS (só remapeia `caddy→8081`, `web→3100`; inofensivo e na prática obrigatório, pois o nginx do host ocupa 80/443) — atualizar O6 da rodada anterior em vez de remover.
- **O5 — Rewrite `/api/*` morto no `frontend/next.config.*`:** nenhuma tela usa `/api/` (tudo via `NEXT_PUBLIC_API_URL`); se um dia for usado, todo o rate limit por IP colapsa para o IP do container web. Remover ou documentar.
- **O6 — `ensure_admin` promove conta existente sem conferir senha** (`backend/app/tasks/admin.py:52-54`): janela de race ~zero (seed roda no boot antes de servir), mas endurecer (só promover se `password_hash` nulo) elimina a classe.
- **O7 — Redis/MinIO sem senha em rede interna** (persiste): aceitável enquanto sem porta publicada — nunca expor.
- **O8 — Tokens em `localStorage`** (`frontend/lib/api.ts:20-23`): sem sink XSS no frontend hoje, impacto contido; `httpOnly` seria o ideal.
- **O9 — Histórico git retém o default antigo do admin** (`Ui4u%80D`, commits `5d78df7`→`abf2ff5`): valor vivo na VPS confirmado diferente; sem ação além de nunca reutilizar.

## Pontos verificados sem achados

- **V1 (RLS):** não aplicável — sem BaaS com chave anônima; isolamento equivalente por dono verificado em 100% das rotas de recurso (`list_homeworks`/`export_csv`/`usage_summary`/`today_overview` filtram `owner_user_id`; `owns`/`owned_by` em todos os `:id` de children, homeworks, agenda, image, reprocess, status, edit, accept, suggestions, notifications, settings).
- **V2:** sem decisão de permissão no frontend (sem tela admin, sem `isAdmin`/`role`); `require_admin` com 403 em todas as rotas `/v1/admin/*`; RF-16 com gates no servidor (login 403, link 403, vínculo e bot com `PENDING_MSG`).
- **V3 (rotas HTTP):** todos os `:id` checam existência (404) + posse (403) antes de ler/gravar; `accept` só aceita slot das sugestões atuais; `delete_activity` confere `child_id`; autoexclusão e último-admin bloqueados.
- **V4:** `.env` nunca rastreado no git; histórico sem chave real (só placeholders `...`/`CHANGE_ME`/dummy); `NEXT_PUBLIC_*` só com URL e handle público do bot; `.env` da VPS com permissão 600; `JWT_SECRET`/`POSTGRES_PASSWORD`/`ADMIN_PASSWORD`/`S3_SECRET_KEY` rotacionados (prefixos conferidos).
- **V5 (demais):** magic bytes (não confia em content-type), 10 MB web+bot+import, rewrite via Pillow, teto anti-bomba 25 MP, chaves UUID + `_check_key` anti-traversal, hint 500 chars, Pydantic + allowlists (`PATCH`, settings, activities), `int()`/`weekday`/`HH:MM` sob `Validation` (400), sem SQL cru (ORM), sem sinks XSS, sem `innerHTML`.
- **V6 (demais):** limits em upload/login(10/900 ip+email)/register/reprocess/import/webhook(120/min IP)/global 300/min com 429 + `Retry-After`; corpo 11 MB no Caddy; export tetado em 5000; beat com `coalesce`/`max_instances=1` e disjuntor de tentativas; polling com backoff até 30 s; Argon2id (login caro por design, coberto pelo lockout).
- **Infra/host:** somente `3100`/`8081` públicos (nossos); postgres/redis/minio sem porta no host; `ENV=prod` (docs fechadas); containers não-root; host multi-tenant sem colisão envolvendo nossos serviços (`127.0.0.1:8000` é do tenant `amarelinho`, não nosso — verificado via `docker ps`).
