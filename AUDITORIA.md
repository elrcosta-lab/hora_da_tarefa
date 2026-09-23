# Auditoria de Segurança — Hora da Tarefa (pré-deploy VPS, 2ª rodada)

- Data: 2026-09-23
- Stack detectada: Next.js 14 (App Router) + FastAPI + SQLAlchemy + Postgres 16 + Redis + MinIO + JWT HS256 + Argon2id
- Escopo: `backend/` (api, core, tasks, bot, models, alembic até `0011`), `frontend/` (app, lib, components), `docker-compose.yml`, `Caddyfile`, Dockerfiles, `.env.example`, histórico git. Sem Supabase/Firebase (RLS não se aplica). Somente leitura; nada foi modificado. Foco no delta desde a 1ª auditoria (admin RBAC, import por inferência, reprocess, usage, supervisão) + regressão dos fixes anteriores + prontidão VPS.

## Status pós-correção (2026-09-23, verificado no código)

Todos os achados A1–A7 foram corrigidos (um commit por fix), e as observações O2/O6 confirmadas na operação do beta:

| Achado | Status | Evidência da correção |
|---|---|---|
| A1 senha default do admin | ✅ corrigido | `backend/app/tasks/admin.py:27` — fail-closed (`RuntimeError` sem `ADMIN_PASSWORD`) |
| A2 reprocess sem rate limit | ✅ corrigido | `backend/app/api/homeworks.py:181` — `limit(20/h)` + `limit(5/min)` por usuário |
| A3 exclusão sem purga do storage | ✅ corrigido | `backend/app/tasks/admin.py:82-106` — coleta `storage_key`s e deleta via provider |
| A4 backdoor `test_bytes_b64` | ✅ corrigido | `backend/app/bot/handlers.py:64` — só com `ALLOW_TEST_BYTES=true` |
| A5 caption/hint sem teto | ✅ corrigido | Teto de 500 chars na camada `tasks` (`get_or_create_homework`) |
| A6 docs interativas abertas | ✅ corrigido | `backend/app/main.py:58` — `docs_url=None` quando `ENV=prod` |
| A7 containers como root | ✅ corrigido | `USER app` em `backend/Dockerfile:14` e `frontend/Dockerfile:22` |

**Risco residual aceito no beta:** O1 (sem TLS até o DNS), O3 (trânsito internacional OpenRouter/Telegram — citar no termo LGPD), O4 (rotacionar segredos locais `smoke-*` + `chmod 600`). O2 resolvida operacionalmente: bot em polling (`RUN_MODE=polling`).

## Resumo executivo

| Severidade | Quantidade |
|---|---|
| Crítica | 0 |
| Alta | 1 |
| Média | 2 |
| Baixa | 4 |

**Prioridade de correção:** A1, A2, A3, depois A4–A7.

## Achados

### [A1] Senha default do admin no código — backend/app/tasks/admin.py:28
- **Severidade:** Alta
- **Evidência:**
  ```python
  return (os.environ.get("ADMIN_EMAIL", DEFAULT_ADMIN_EMAIL),
          os.environ.get("ADMIN_PASSWORD", "Ui4u%80D"))
  ```
- **Risco:** quem lê o repo (ou o histórico git, para sempre) sabe e-mail + senha do admin — se a VPS subir sem `ADMIN_PASSWORD` no ambiente, é takeover administrativo sem credencial.
- **Correção:** fail-closed como o `JWT_SECRET` — remover o default e exigir a variável, ou exigir troca no 1º login:
  ```python
  password = os.environ.get("ADMIN_PASSWORD")
  if not password:
      raise RuntimeError("ADMIN_PASSWORD ausente — recuse o boot")
  ```

### [A2] Reprocess sem rate limit — cada chamada queima IA paga — backend/app/api/homeworks.py:181
- **Severidade:** Média
- **Evidência:**
  ```python
  @router.post("/{homework_id}/reprocess", status_code=202)
  def reprocess_homework_view(homework_id: str, background: BackgroundTasks,
                              owner: str = Depends(get_current_user_id)):
  ```
  Dono checado (`_owned_or_error`), mas sem `Depends(limit(...))` — só o global de 300/min. Cada chamada = 1–2 inferências pagas (`run_extraction` + retry de conteúdo vazio).
- **Risco:** conta autenticada (registro aberto) drena créditos OpenRouter em loop.
- **Correção:** os mesmos limites do upload:
  ```python
  @router.post("/{homework_id}/reprocess", status_code=202,
               dependencies=[Depends(limit(20, 3600, key="user", prefix="rp-hour")),
                             Depends(limit(5, 60, key="user", prefix="rp-min"))])
  ```

### [A3] Exclusão de conta não purga o storage — backend/app/tasks/admin.py
- **Severidade:** Média
- **Evidência:** `delete_user` remove linhas (`HomeworkImage`, `Homework`, `Child`…) mas grep por `storage|minio|s3` no arquivo retorna vazio — os bytes permanecem no MinIO/disco.
- **Risco:** exclusão LGPD incompleta (dado da criança segue armazenado) + lixo pago acumulando.
- **Correção:** coletar `storage_key`s antes do `delete` e removê-las via provider, com `purge_expired_images` como padrão:
  ```python
  keys = [i.storage_key for i in s.query(HomeworkImage).filter(
      HomeworkImage.homework_id.in_(hw_ids)).all()]
  # ...deletes...
  from app.core.storage import get_storage
  for k in keys:
      try: get_storage().delete(k)
      except Exception: pass
  ```

### [A4] Backdoor de teste sem gate — backend/app/bot/handlers.py:62
- **Severidade:** Baixa
- **Evidência:** `b64 = message.get("test_bytes_b64")` — qualquer update com o `secret_token` pode injetar bytes arbitrários, pulando o download e alimentando a extração paga.
- **Risco:** baixo (exige o secret do webhook), mas é bypass de validação + custo.
- **Correção:** só honrar com flag explícita:
  ```python
  import os
  b64 = message.get("test_bytes_b64") if os.environ.get("ALLOW_TEST_BYTES") == "true" else None
  ```

### [A5] Caption/hint sem teto — backend/app/bot/handlers.py:260
- **Severidade:** Baixa
- **Evidência:** `hint_text=message.get("caption")` vai cru para `extraction_json.meta` (repetido da 1ª auditoria, ainda aberto).
- **Risco:** metadado gigante por tarefa no JSONB.
- **Correção:** `hint_text=(message.get("caption") or "")[:500]` (e o mesmo no `Form hint_text` do upload).

### [A6] Docs interativas abertas — backend/app/main.py
- **Severidade:** Baixa
- **Evidência:** `FastAPI(...)` sem `docs_url`/`redoc_url` (grep vazio; repetido da 1ª auditoria).
- **Risco:** mapa completo da API para quem varre a VPS.
- **Correção:** `docs_url=None, redoc_url=None` quando `ENV=prod`.

### [A7] Containers como root — backend/Dockerfile, frontend/Dockerfile
- **Severidade:** Baixa
- **Evidência:** grep por `USER` vazio nos dois Dockerfiles (repetido da 1ª auditoria).
- **Risco:** escape de container com privilégio total.
- **Correção:** `RUN adduser --disabled-password app && USER app` (+ `chown` dos volumes da app).

## Observações adicionais (deploy VPS)

- **O1 — Sem TLS:** `Caddyfile` com `auto_https off` e só `:80` — na VPS apontar domínio e remover o `off`; até lá, JWT/senhas em claro até em rede interna.
- **O2 — Webhook x polling:** sem URL pública o Telegram não entrega updates (bot mudo nos dois sentidos); usar `RUN_MODE=polling` (só egress) ou expor o webhook. O `secret_token` + `compare_digest` só valem no modo webhook.
- **O3 — Egresso + LGPD:** OpenRouter e Telegram exigem saída 443 e recebem dado de menor — citar nominalmente no termo de consentimento.
- **O4 — Segredos locais:** o `.env` desta máquina ainda usa valores `smoke-*` com permissão 664 — **rotacionar tudo** (`JWT_SECRET`, `POSTGRES_PASSWORD`, `S3_SECRET_KEY`, `TELEGRAM_WEBHOOK_SECRET`, `ADMIN_PASSWORD`) e `chmod 600` antes da VPS. Nada disso está no git (verificado).
- **O5 — Redis/MinIO internos:** sem portas publicadas e sem senha — aceitável restrito à rede `internal`; nunca expor sem credencial + TLS.
- **O6 — Override local:** `docker-compose.override.yml` (8081/3100) é gitignored e específico daqui — não copiar para a VPS.
- **O7 — Refresh rotation, lockout de login e purge LGPD** verificados e operantes; `GET /usage` escopado por dono.

## Pontos verificados sem achados

- **Autorização:** 100% das rotas de recurso com Bearer + 403 cross-account, incluindo as novas (`usage`, `settings` GET, `activities` DELETE, `reprocess`, `PATCH` edit, `/image`, `routine/import`); bot restrito ao vínculo; admin com `require_admin` + handler 403.
- **IDOR/admin:** `owns`/`owned_by` em todos os `:id`; autoexclusão e último-admin bloqueados; reset revoga refresh e exige ≥8.
- **Upload/storage:** magic bytes, 10MB web e bot, chaves UUID, `_check_key` anti-traversal, teto Pillow, data passada rejeitada, dedupe SHA-256.
- **Validação:** Pydantic + allowlists (`PATCH`, settings, link); `int()` sob `try` (400); sem SQL cru; sem sinks XSS.
- **Segredos:** histórico git limpo, `.env` nunca rastreado, `NEXT_PUBLIC_*` só com URL e handle público.
- **DoS:** rate limits em upload/login/register/webhook/global com 429 + `Retry-After`; corpo 11MB; export com teto; beat com disjuntor.
- **Sessões:** Argon2id, JWT fail-closed, refresh single-use com detecção de reuso, login 401 genérico.
- **RLS (V1):** não aplicável — sem BaaS com chave anônima.
