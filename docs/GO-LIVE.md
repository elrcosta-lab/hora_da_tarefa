# Runbook de Go-Live (beta) — Hora da Tarefa (v1.4, 2026-09-25)

> **Regra de ouro: nenhum segredo entra no git.** `OPENROUTER_API_KEY`,
> `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `POSTGRES_PASSWORD`,
> `JWT_SECRET` e `S3_SECRET_KEY` vivem **só** no `.env` local (gitignored).
> Este arquivo contém zero segredos — verifique com `git diff --cached | grep -i -E "sk-or|AAH|secret"`
> antes de cada push.

## 0. Pré-requisitos

- [ ] VPS com Docker Engine + plugin compose (este repo já subiu com `docker-ce`)
- [ ] Domínio apontando para a VPS (ex.: `app.horadatarefa.com` + `api.horadatarefa.com`)
- [ ] Conta OpenRouter + `OPENROUTER_API_KEY` (`sk-or-v1-...`) — modelo padrão `nex-agi/nex-n2.5-mini:free` (tier pago delistado em 2026-09-24; sem crédito obrigatório)
- [ ] Bot `@hora_da_tarefa_bot` (BotFather) — token e comandos **já configurados** (ver §1)

## 1. Telegram — status atual

| Item | Estado |
|---|---|
| Bot `@hora_da_tarefa_bot` + token válido (`getMe`) | ✅ feito |
| Comandos (`/start /ajuda /hoje /tarefas /concluir /criancas`) | ✅ via `setMyCommands` |
| Descrição curta + sobre | ✅ via API |
| **Avatar** (`assets/brand/telegram-avatar-512.png`) | ⬜ manual: BotFather → `/setuserpic` → enviar o PNG |
| **Modo atual: polling** (`RUN_MODE=polling`) | ✅ ativo — funciona sem URL pública (só egress) |
| **Webhook** (`setWebhook`) | ⬜ opcional, após DNS: ver §3 |

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

## 3. Webhook do Telegram (opcional — após DNS + TLS)

O beta opera em **polling** e não precisa desta seção. Quando houver domínio, o Caddy emite TLS automático: ajuste o `Caddyfile` para os domínios reais e suba (`docker compose up -d --build`).
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

## 4. Deploy e verificação

> A VPS **não tem clone git** — o sync é via `rsync` (só arquivos, nunca o `.env`).

```bash
export SSH_KEY="$HOME/.ssh/<chave-vps>" VPS="root@<ip-vps>"
# sync (exemplo: um arquivo do backend)
rsync -avz -e "ssh -i $SSH_KEY" backend/app/bot/handlers.py $VPS:/opt/hora_da_tarefa/backend/app/bot/handlers.py
# rebuild + restart + verificação
ssh -i $SSH_KEY $VPS "cd /opt/hora_da_tarefa && docker compose build -q api \
  && docker compose up -d api && sleep 12 \
  && docker compose exec -T api python -c \"import urllib.request; print(urllib.request.urlopen('http://localhost:8000/readyz').read().decode())\""
# esperado: {"ok":true,"checks":{"api":"up"}}
```

> Portas: no compose base o Caddy escuta `:80` e o web `:3000`. No QA local o `docker-compose.override.yml` (gitignored) remapeia para `8081` e `3100` — é por isso que o runbook e o README citam `localhost:8081/3100`. Na VPS há override próprio (só remapeia `caddy→8081`, `web→3100`, pois o nginx do host ocupa 80/443).

```bash
# primeira subida (na VPS)
docker compose up -d --build
docker compose ps                    # todos healthy
docker compose logs api | grep -i alembic   # 0001→0012 aplicadas
```

Roteiro funcional (navegador + Telegram): registro com consentimento → **aprovação do admin (abaixo, RF-24)** → onboarding (filho → grade → código) → `/start <código>` → foto → revisão → agendar → `/hoje` → concluir (bot aceita id curto de 8 chars) → CSV em Tarefas.

**Aprovar/rejeitar contas (RF-24):** sem UI de admin no beta — via API com token do admin:
```bash
export AT="<access_token do admin>"
curl -s http://localhost:8081/v1/admin/users | python3 -c "import json,sys; [print(u['user_id'], u['email'], u['status']) for u in json.load(sys.stdin)['items']]"
curl -s -X POST http://localhost:8081/v1/admin/users/<user_id>/approve -H "Authorization: Bearer $AT"
# rejeitar: POST .../reject (revoga sessões; admin não pode ser rejeitado)
```

## 5. Operação e rollback

- **Logs:** `docker compose logs -f api` (sem PII/imagem/base64 por construção)
- **Beat:** tick 1/min + purge 1x/dia dentro da API (`BEAT_ENABLED=false` desliga). Entrega via `_select_sender`: direta (Bot API) em live, outbox descarregado pelo polling em modo polling, nada fora disso. Tarefa em status terminal nunca gera envio (pendentes viram `cancelled`).
- **Fila OpenRouter 429 (tier `:free`):** backoff automático 1/5/30 min (3 retries); 404 No-endpoints não retenta (modelo morto — trocar `OPENROUTER_MODEL`); `AI_WORKER_CONCURRENCY=3` no `.env`; custo por conta em `GET /v1/usage`
- **Compose respeita o `.env`:** `TELEGRAM_LIVE_SEND` e `OPENROUTER_MODEL` usam `${VAR:-default}` (sem valor fixo no YAML)
- **Rollback:** sem git na VPS — reenvie via `rsync` a versão anterior do arquivo + `docker compose build -q api && docker compose up -d api`; migrations reversíveis (`docker compose exec api alembic -c /app/alembic.ini downgrade -1`)
- **Backup:** volume `pgdata` (descubra o nome real com `docker volume ls | grep pgdata`): `docker run --rm -v <projeto>_pgdata:/data -v /opt/hora_da_tarefa/backup:/b ...`

## 6. Pendências conhecidas (não bloqueiam o beta)

- Rate limit em memória se Redis cair (fail-open local, fail-closed não)
- Resumo diário 07:00 (opcional), gamificação, PWA — pós-MVP
