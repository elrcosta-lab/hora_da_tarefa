# Runbook de Go-Live (beta) — Hora da Tarefa

> **Regra de ouro: nenhum segredo entra no git.** `OPENROUTER_API_KEY`,
> `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `POSTGRES_PASSWORD`,
> `JWT_SECRET` e `S3_SECRET_KEY` vivem **só** no `.env` local (gitignored).
> Este arquivo contém zero segredos — verifique com `git diff --cached | grep -i -E "sk-or|AAH|secret"`
> antes de cada push.

## 0. Pré-requisitos

- [ ] VPS com Docker Engine + plugin compose (este repo já subiu com `docker-ce`)
- [ ] Domínio apontando para a VPS (ex.: `app.horadatarefa.com` + `api.horadatarefa.com`)
- [ ] Conta OpenRouter com crédito/limite free + `OPENROUTER_API_KEY` (`sk-or-v1-...`)
- [ ] Bot `@hora_da_tarefa_bot` (BotFather) — token e comandos **já configurados** (ver §1)

## 1. Telegram — status atual

| Item | Estado |
|---|---|
| Bot `@hora_da_tarefa_bot` + token válido (`getMe`) | ✅ feito |
| Comandos (`/start /ajuda /hoje /tarefas /concluir /criancas`) | ✅ via `setMyCommands` |
| Descrição curta + sobre | ✅ via API |
| **Avatar** (`assets/brand/telegram-avatar-512.png`) | ⬜ manual: BotFather → `/setuserpic` → enviar o PNG |
| **Webhook** (`setWebhook`) | ⬜ após DNS: ver §3 |

Comandos para reinspecionar (sem gravar segredo em histórico — prefira variável de ambiente):

```bash
export TB="<TELEGRAM_BOT_TOKEN do .env>"
curl -s "https://api.telegram.org/bot$TB/getMe"
curl -s "https://api.telegram.org/bot$TB/getMyCommands"
curl -s "https://api.telegram.org/bot$TB/getWebhookInfo"
```

## 2. `.env` de produção (na VPS, nunca no git)

```bash
cp .env.example .env
# preencher: OPENROUTER_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_WEBHOOK_SECRET (gerar: python3 -c "import secrets; print(secrets.token_urlsafe(32))"),
# POSTGRES_PASSWORD, JWT_SECRET (≥32 chars), S3_SECRET_KEY
# ajustar: WEB_API_URL=https://api.<dominio>, CORS_ORIGINS=https://app.<dominio>
```

## 3. Webhook do Telegram (após DNS + TLS)

O Caddy emite TLS automático quando o domínio resolve para a VPS. Ajuste o
`Caddyfile` para os domínios reais e suba (`docker compose up -d --build`).
Depois registre o webhook **com o mesmo secret do `.env`**:

```bash
export TB="<TELEGRAM_BOT_TOKEN do .env>"
export SECRET="<TELEGRAM_WEBHOOK_SECRET do .env>"   # mesmo valor!
export HOOK="https://api.<dominio>/v1/telegram/webhook"
curl -s "https://api.telegram.org/bot$TB/setWebhook" \
  --data-urlencode "url=$HOOK" --data-urlencode "secret_token=$SECRET" \
  --data-urlencode "allowed_updates=[\"message\",\"callback_query\"]"
curl -s "https://api.telegram.org/bot$TB/getWebhookInfo"  # confere url + pending_update_count
```

Teste: gere um código em Configurações → envie `/start <código>` no bot → deve responder "vinculada".

## 4. Subida e verificação

```bash
docker compose up -d --build
docker compose ps                    # todos healthy
docker compose logs api | grep -i alembic   # 0001→0005 aplicadas
curl https://api.<dominio>/healthz   # {"ok":true}
```

Roteiro funcional (navegador + Telegram): registro com consentimento →
onboarding (filho → grade → código) → `/start <código>` → foto →
revisão → agendar → `/hoje` → concluir → CSV em Tarefas.

## 5. Operação e rollback

- **Logs:** `docker compose logs -f api` (sem PII/imagem/base64 por construção)
- **Beat:** tick 1/min + purge 1x/dia dentro da API (`BEAT_ENABLED=false` desliga)
- **Fila OpenRouter 429:** backoff automático; `AI_WORKER_CONCURRENCY` no `.env`
- **Rollback:** `git log --oneline` → `git revert <sha>` ou checkout da tag anterior +
  `docker compose up -d --build api web`; migrations reversíveis
  (`alembic -c backend/alembic.ini downgrade -1`)
- **Backup:** volume `pgdata` (`docker run --rm -v hora-da-tarefa_pgdata:/data -v $(pwd):/b ...`)

## 6. Pendências conhecidas (não bloqueiam o beta)

- Rate limit em memória se Redis cair (fail-open local, fail-closed não)
- Resumo diário 07:00 (opcional), gamificação, PWA — pós-MVP
