# Auditoria de Segurança — Hora da Tarefa (4ª rodada: RF-25 + regressão)

- Data: 2026-10-04
- Stack detectada: Next.js 14 (App Router) + FastAPI + SQLAlchemy 2 + Postgres 16 + Redis 7 + MinIO (S3) + JWT HS256 (`access`/`refresh`/`child_access`) + Argon2id + bot Telegram próprio sobre httpx (polling) + OpenRouter (`meta/muse-spark-1.3-contributor`)
- Escopo: delta RF-25 desde a 3ª rodada (`backend/app/{api/child.py,api/auth.py,api/children.py,tasks/child_access.py,core/security.py,core/ratelimit.py,core/config.py,models/child.py,alembic/versions/0013_child_access.py,tests/test_child_access.py}`, `frontend/{lib/child-api.ts,app/crianca/,app/criancas/page.tsx,app/globals.css}`, `PRD.md` v1.7, `SPECS.md` §2.14/§3.12/§10.5) + regressão dos fixes A1–A4/O1 + re-varredura V1–V6 no repo local e compose dev. **VPS fora do escopo desta rodada** (sem acesso SSH): estado de produção segue o da 3ª rodada; o deploy da RF-25 na VPS ocorreu após a rodada (ver O13 atualizada e adendo v1.8 abaixo). Somente leitura; nada foi modificado.

## Resumo executivo (4ª rodada)

| Severidade | Quantidade |
|---|---|
| Crítica | 0 |
| Alta | 0 |
| Média | 1 |
| Baixa | 2 |

**Prioridade de correção:** A5, depois A6–A7. Nenhum bloqueador para o beta: o isolamento entre famílias e entre pai/filho foi verificado por código + 16 testes.

## Status pós-correção (2026-10-04, verificado)

| Item | Status | Evidência |
|---|---|---|
| A5 login O(n) Argon2 | ✅ corrigido | `code_lookup` HMAC-SHA256 indexado (`models/child.py`, migração `0014` aplicada no Postgres do compose, índice `ix_child_access_code_lookup`); `login_by_code` O(1) + fallback só p/ linhas legadas (`tasks/child_access.py`); 2 testes novos (`test_code_lookup_is_hmac_not_plaintext`, `test_legacy_row_without_lookup_still_logs_in`) |
| A6 sessão 12h em aparelho compartilhado | ✅ corrigido | `CHILD_TOKEN_EXPIRE_MINUTES` 720→120 (`core/config.py:37`, `.env.example`); expiração respeitada no cliente (`hdt.child.exp` em `lib/child-api.ts:5-25`); `expires_in=7200` coberto em teste; smoke E2E confirma |
| A7 lockout sem reset / conta formato inválido | ✅ corrigido | formato inválido → 400 sem contar (`api/auth.py:126-127`); `clear_child_login_failures()` em sucesso (`core/ratelimit.py:139`, `api/auth.py:132`); 2 testes novos (malformado não conta; 9+acerto+9 não trava) |
| Regressão | ✅ sem regressão | 63/63 em child/auth/ratelimit/routine/approval/admin/bot/FSM; suíte total 182 coletados, falhas só as ambientais pré-existentes (comprovadas em código pristino); `tsc` + build web OK; `/crianca` 200 |

## Achados (4ª rodada)

### [A5] Login da criança custa O(n) Argon2id por tentativa (amplificação) — backend/app/tasks/child_access.py:100-115
- **Severidade:** Média
- **Evidência:**
  ```python
  # backend/app/tasks/child_access.py:104-115
  rows = (s.query(ChildAccess, Child)
          .join(Child, Child.id == ChildAccess.child_id)
          .filter(ChildAccess.revoked.is_(False), Child.active.is_(True))
          .all())
  for access, child in rows:
      if verify_child_code(code, access.code_hash):  # Argon2id (~64 MB RAM + ~0,2-0,5 s CPU cada)
  ```
- **Risco:** endpoint público (`POST /v1/auth/child/login`) executa até N verifies Argon2id por chamada; o custo por tentativa cresce com a base e, em 1 vCPU/4 GB, dezenas de crianças já dão segundos de CPU por request — rate limit 5/min + lockout 10/15 min mitigam por IP, mas rotação de IP multiplica o efeito.
- **Correção:**
  ```python
  # coluna indexada p/ lookup O(1) + 1 Argon2 final (pepper só no servidor, nunca no banco em claro)
  code_lookup: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
  # na geração: row.code_lookup = hmac.new(PEPPER, norm.encode(), hashlib.sha256).hexdigest()
  # no login: row = s.query(ChildAccess).filter_by(code_lookup=lookup).one_or_none()
  #           if row and verify_child_code(code, row.code_hash): ...
  ```

### [A6] Sessão da criança longa (12h) em `localStorage` de dispositivo compartilhado — backend/app/core/config.py:37 + frontend/lib/child-api.ts:5-25
- **Severidade:** Baixa
- **Evidência:**
  ```python
  # backend/app/core/config.py:37
  CHILD_TOKEN_EXPIRE_MINUTES: int = 720
  ```
  ```ts
  // frontend/lib/child-api.ts:5-17
  const CHILD_LS = "hdt.child.access";
  export function saveChildToken(token: string) {
    localStorage.setItem(CHILD_LS, token);  // sem expiração client-side nem idle timeout
  }
  ```
- **Risco:** no celular/tablet da família, quem abrir `/crianca/tarefas` após o uso vê as tarefas da criança se ela não tocou em Sair; dado exposto limita-se às tarefas (sem PII além de nome/série), e a revogação server-side continua valendo.
- **Correção:**
  ```python
  # backend/app/core/config.py — TTL menor p/ sessão infantil
  CHILD_TOKEN_EXPIRE_MINUTES: int = 120
  ```
  ```ts
  // frontend/lib/child-api.ts — respeitar exp e orientar a saída
  // guardar `expires_at` junto do token e, se vencido, limpar + redirecionar a /crianca
  // com aviso "Por segurança, entre com o código de novo"; reforçar o botão Sair na UI
  ```

### [A7] Lockout conta formato inválido e não reseta em sucesso — backend/app/api/auth.py:123-132
- **Severidade:** Baixa
- **Evidência:**
  ```python
  # backend/app/api/auth.py:123-131
  ip = request.client.host if request.client else "unknown"
  if child_login_locked(ip):
      raise RateLimited(900)
  if not (payload.code or "").strip():
      return _err("VALIDATION_ERROR", "Informe o código de acesso.", 400)
  child = CA.login_by_code(payload.code)
  if child is None:
      note_child_login_failure(ip)  # conta até formato inválido (rejeitado sem custo no §2.2)...
      return _err("INVALID_CODE", "Código inválido.", 401)
  # ...e o sucesso não limpa o contador: 11 erros (até de digitação) bloqueiam o IP por 15 min
  ```
- **Risco:** negação acidental do acesso da criança (auto-DoS por digitação inocente) e, no limite, terceiro que conheça o IP da casa trava o login por 15 min com 11 requests baratos.
- **Correção:**
  ```python
  from app.core.security import normalize_child_code
  norm = normalize_child_code(payload.code)
  if norm is None:
      return _err("VALIDATION_ERROR", "Código inválido.", 400)  # sem contar falha
  child = CA.login_by_code(norm)
  if child is None:
      note_child_login_failure(ip)
      return _err("INVALID_CODE", "Código inválido.", 401)
  clear_child_login_failures(ip)  # reset em sucesso (helper novo no ratelimit)
  ```

## Observações adicionais (4ª rodada)

- **O10 — Burst 5/min conta sucessos:** `limit(5, 60, key="ip")` (`auth.py:116`) soma logins válidos e inválidos por IP; casa com 2+ crianças digitando junto + 1 erro cada já toma 429 legítimo. Robustez/UX, não vulnerabilidade — avaliar `key` por código normalizado além do IP.
- **O11 — Oráculo de timing desprezível:** `login_by_code` retorna cedo no match e varre tudo no miss; sob rate limit + lockout não há orçamento para explorar a diferença. Sem ação (some se A5 for corrigido com lookup O(1)).
- **O12 — Higiene menor do `child_access`:** `last_login_at` não reseta ao regenerar e o hash do código revogado permanece no banco — inofensivo (Argon2id), sem PII. Sem ação obrigatória.
- **O13 — RF-25 em produção (atualizado pós-deploy 2026-10-04):** deploy via rsync + rebuild concluído na VPS (backup prévio, `0012→0013→0014` aplicadas no boot, `/crianca` 200, smoke read-only OK). O runbook do deploy está em `docs/GO-LIVE.md` §4 (bloco RF-25).
- **O14 — 3ª rodada segue válida p/ a VPS:** O2 (sem TLS), O3 (LGPD/transbordo), O5–O9 não foram re-verificados por falta de acesso SSH — nada no delta RF-25 os altera.
- **O15 — `GET /docs` aberto localmente é esperado** (`ENV=dev` aqui; prod mantém `docs_url=None` — A6 da 2ª rodada intacto em `main.py:59`).
- **Adendo v1.8 (2026-10-04, sem nova rodada):** 3 fixes web pós-4ª rodada, todos sem impacto de segurança — (a) código da criança agora exibido após gerar (reordenação `loadAccess`→`setShownCode`; o código já transitava na resposta `201`, nenhuma exposição nova); (b) fallback de cópia sem clipboard API p/ HTTP (textarea temporária no próprio DOM, mesmo contexto de origem); (c) `/crianca/tarefas` oculta `concluída` (filtro só-apresentação no cliente; API e autorização inalteradas). Nenhum achado novo.

## Pontos verificados sem achados (4ª rodada)

- **V1 (isolamento ~ RLS):** rotas `/v1/child/*` filtram sempre pelo `sub` do token (sem `child_id` de entrada — cross-child impossível por construção); `access-code` exige dono (`_owned_or_error` → 404/403); JWT cross-tipo rejeitado nos dois sentidos (`expect="access"` × `expect="child_access"`); revogação rechecada no banco a cada request. Cobertura: 16 testes novos (`test_child_access.py`) + smoke E2E no Postgres.
- **V2:** nenhuma decisão de permissão no frontend infantil (páginas só leem via `childApi`; `normalizeCode` client-side é UX); sem `isAdmin`/`role` no cliente; sem tela admin.
- **V3:** `:id` com 404 + 403; `accept`/`delete_activity`/FSM inalterados; `page/page_size` clampados, `offset` com `max(0, …)`; `sort` sem SQL cru (só `due_at` especial, resto `created_at`).
- **V4:** `.env`/`.env.local` ignorados, só `.env.example` rastreado (placeholders); `NEXT_PUBLIC_*` só com URL e handle público do bot; sem JWT/chave privada hardcoded; sem `sk-or` real no histórico; CORS restrito a localhost neste ambiente; `CHILD_TOKEN_EXPIRE_MINUTES` não é segredo; código de acesso nunca em log (grep em `api/child.py`, `tasks/child_access.py`, `auth.py`, `security.py` limpo; teste `test_login_does_not_log_code` verde).
- **V5:** `ChildLoginIn` (Pydantic) nas entradas novas; sem `req.body` cru, sem mass assignment (upsert só toca `code_hash`/`revoked` server-side); sem sinks XSS (`dangerouslySetInnerHTML`/`innerHTML` ausentes nas telas novas); upload/validação antigos intactos (magic bytes, 10 MB, Pillow, tetos, allowlists).
- **V6:** login criança com 5/min + lockout 10/15 min e `Retry-After`; global 300/min, demais limits intactos; `GET /v1/child/homeworks` paginado no SQL (limit 100 + count); export tetado; beat/polling inalterados; ressalva A5 registrada acima.
- **Regressão 2ª/3ª rodada:** A1 (teto 20k + threadpool), A2 (`concluir:` escopado, bloco único), A4 (paginação SQL), O1 (`_select_sender` + `coalesce`/`max_instances=1`), backdoor `test_bytes_b64` sob `ALLOW_TEST_BYTES`, `ProxyHeadersMiddleware`, `USER app` nos Dockerfiles, `ENV`/`LIVE_SEND`/`POLLING` — tudo verificado e mantido.
- **Infra local:** só `3100`/`8081` no host (nossos); postgres/redis/minio sem porta publicada; containers não-root.

---

# Auditoria de Segurança — Hora da Tarefa (VPS, 3ª rodada + adendos v1.4/v1.6)

- Adendo 2026-09-29 (v1.6, sem nova rodada): (a) bug funcional de fuso corrigido — atraso agora compara data em SP (`_sp_day`/`_sp_iso`) e o KPI conta pelo vencimento real (SPECS §4.6; sem impacto de segurança); (b) incidente operacional na VPS local resolvido sem perda — `api` em loop por `DuplicateTable` (`init_db`/`create_all` × alembic, versão travada em `0007` com tabela já existente), sanado com backup + `stamp 0009` + `upgrade head` (`0010–0012` aditivas; runbook em `docs/GO-LIVE.md` §5) — reforça nunca expor `POSTGRES_PASSWORD` divergente e manter alembic como único gestor de schema em prod; (c) docs sincronizadas (modelo `muse-spark`, 160 testes). Nenhum achado novo de segurança.

- Adendo 2026-09-25 (v1.4, sem nova rodada): achados A1–A4 e O1 da 3ª rodada já estão **corrigidos e verificados em produção** (tabela "Status pós-correção" acima: teto+threadpool no import, `concluir` escopado ao dono, `S3_ACCESS_KEY` rotacionada, paginação SQL, compose respeita `.env`). Nenhum achado novo; este arquivo segue como registro point-in-time da 3ª rodada. Aprovação de contas renumerada RF-16 → **RF-24** nos docs (RF-16 volta a ser calendário semanal no PRD); modelo padrão confirmado `:free` (pago delistado 2026-09-24).

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
- **V2:** sem decisão de permissão no frontend (sem tela admin, sem `isAdmin`/`role`); `require_admin` com 403 em todas as rotas `/v1/admin/*`; aprovação (RF-24; então RF-16) com gates no servidor (login 403, link 403, vínculo e bot com `PENDING_MSG`).
- **V3 (rotas HTTP):** todos os `:id` checam existência (404) + posse (403) antes de ler/gravar; `accept` só aceita slot das sugestões atuais; `delete_activity` confere `child_id`; autoexclusão e último-admin bloqueados.
- **V4:** `.env` nunca rastreado no git; histórico sem chave real (só placeholders `...`/`CHANGE_ME`/dummy); `NEXT_PUBLIC_*` só com URL e handle público do bot; `.env` da VPS com permissão 600; `JWT_SECRET`/`POSTGRES_PASSWORD`/`ADMIN_PASSWORD`/`S3_SECRET_KEY` rotacionados (prefixos conferidos).
- **V5 (demais):** magic bytes (não confia em content-type), 10 MB web+bot+import, rewrite via Pillow, teto anti-bomba 25 MP, chaves UUID + `_check_key` anti-traversal, hint 500 chars, Pydantic + allowlists (`PATCH`, settings, activities), `int()`/`weekday`/`HH:MM` sob `Validation` (400), sem SQL cru (ORM), sem sinks XSS, sem `innerHTML`.
- **V6 (demais):** limits em upload/login(10/900 ip+email)/register/reprocess/import/webhook(120/min IP)/global 300/min com 429 + `Retry-After`; corpo 11 MB no Caddy; export tetado em 5000; beat com `coalesce`/`max_instances=1` e disjuntor de tentativas; polling com backoff até 30 s; Argon2id (login caro por design, coberto pelo lockout).
- **Infra/host:** somente `3100`/`8081` públicos (nossos); postgres/redis/minio sem porta no host; `ENV=prod` (docs fechadas); containers não-root; host multi-tenant sem colisão envolvendo nossos serviços (`127.0.0.1:8000` é do tenant `amarelinho`, não nosso — verificado via `docker ps`).
