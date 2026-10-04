# Runbook de Go-Live (beta) — Hora da Tarefa (v1.9, 2026-10-04)

> **Regra de ouro: nenhum segredo entra no git.** `OPENROUTER_API_KEY`,
> `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, `POSTGRES_PASSWORD`,
> `JWT_SECRET` e `S3_SECRET_KEY` vivem **só** no `.env` local (gitignored).
> Este arquivo contém zero segredos — verifique com `git diff --cached | grep -i -E "sk-or|AAH|secret"`
> antes de cada push.

## 0. Pré-requisitos

- [ ] VPS com Docker Engine + plugin compose (este repo já subiu com `docker-ce`)
- [ ] Domínio apontando para a VPS (ex.: `app.horadatarefa.com` + `api.horadatarefa.com`)
- [ ] Conta OpenRouter + `OPENROUTER_API_KEY` (`sk-or-v1-...`) — modelo padrão `meta/muse-spark-1.3-contributor` (família nex delistada 24-25/09/2026, 404 No endpoints; sem crédito obrigatório)
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
# RF-25: CHILD_TOKEN_EXPIRE_MINUTES (default 120; sessão somente-leitura da criança, sem refresh)
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
docker compose logs api | grep -i alembic   # 0001→0014 aplicadas (0013 child_access, 0014 lookup HMAC)
```

Roteiro funcional (navegador + Telegram): registro com consentimento → **aprovação do admin (abaixo, RF-24)** → onboarding (filho → grade → código) → `/start <código>` → foto → revisão → agendar → `/hoje` → concluir (bot aceita id curto de 8 chars) → CSV em Tarefas → **área da criança (RF-25)**: gerar código em Crianças (exibido 1 vez + Copiar) → entrar em `/crianca` → ver `/crianca/tarefas` (concluídas ocultas) → Sair → revogar e confirmar bloqueio.

**Aprovar/rejeitar contas (RF-24):** sem UI de admin no beta — via API com token do admin:
```bash
export AT="<access_token do admin>"
curl -s http://localhost:8081/v1/admin/users | python3 -c "import json,sys; [print(u['user_id'], u['email'], u['status']) for u in json.load(sys.stdin)['items']]"
curl -s -X POST http://localhost:8081/v1/admin/users/<user_id>/approve -H "Authorization: Bearer $AT"
# rejeitar: POST .../reject (revoga sessões; admin não pode ser rejeitado)
```

**Deploy da RF-25 (área da criança):** arquivos novos/alterados além do fluxo acima — sincronizar todos antes do rebuild (o boot aplica `0013→0014` sozinho, aditivas, sem backfill):
```bash
for f in backend/alembic/versions/0013_child_access.py backend/alembic/versions/0014_child_access_lookup.py \
         backend/app/api/child.py backend/app/tasks/child_access.py backend/app/api/auth.py \
         backend/app/api/children.py backend/app/core/security.py backend/app/core/ratelimit.py \
         backend/app/core/config.py backend/app/main.py backend/app/models/child.py \
         backend/app/models/__init__.py frontend/lib/child-api.ts \
         frontend/app/crianca/page.tsx frontend/app/crianca/tarefas/page.tsx \
         frontend/app/criancas/page.tsx frontend/app/globals.css; do
  rsync -avz -e "ssh -i $SSH_KEY" "$f" "$VPS:/opt/hora_da_tarefa/$f"
done
ssh -i $SSH_KEY $VPS "cd /opt/hora_da_tarefa && docker compose build -q api web && docker compose up -d api web \
  && sleep 15 && docker compose logs api | grep -i 'upgrade 0013' \
  && curl -s -o /dev/null -w 'web=%{http_code}\n' http://localhost:3100/crianca"
# rollback das migrations (se preciso): docker compose exec api alembic -c /app/alembic.ini downgrade -1  (0014), de novo (0013)
```

## 5. Operação e rollback

- **Logs:** `docker compose logs -f api` (sem PII/imagem/base64 por construção)
- **Beat:** tick 1/min + purge 1x/dia dentro da API (`BEAT_ENABLED=false` desliga). Entrega via `_select_sender`: direta (Bot API) em live, outbox descarregado pelo polling em modo polling, nada fora disso. Tarefa em status terminal nunca gera envio (pendentes viram `cancelled`).
- **Fila OpenRouter 429 (tier `:free`):** backoff automático 1/5/30 min (3 retries); 404 No-endpoints não retenta (modelo morto — trocar `OPENROUTER_MODEL`); `AI_WORKER_CONCURRENCY=3` no `.env`; custo por conta em `GET /v1/usage`
- **Compose respeita o `.env`:** `TELEGRAM_LIVE_SEND` e `OPENROUTER_MODEL` usam `${VAR:-default}` (sem valor fixo no YAML)
- **Rollback:** sem git na VPS — reenvie via `rsync` a versão anterior do arquivo + `docker compose build -q api && docker compose up -d api`; migrations reversíveis (`docker compose exec api alembic -c /app/alembic.ini downgrade -1`)
- **Backup:** volume `pgdata` (descubra o nome real com `docker volume ls | grep pgdata`): `docker run --rm -v <projeto>_pgdata:/data -v /opt/hora_da_tarefa/backup:/b ...`
  Método provado em 2026-09-29 (rápido e sem downtime): `docker exec hora-da-tarefa-postgres-1 sh -c 'pg_dump -U hora hora_da_tarefa' > /tmp/pre-deploy.backup.sql`
- **`.env` dessincronizado:** se `docker compose` reclamar de variável ausente (`ADMIN_PASSWORD ausente no .env`), o `.env` local está mais velho que o da VPS — complete a partir do ambiente do container em produção (nunca o inverso) e rode `docker compose config -q` para validar.
- **Recuperação de versionamento (incidente real 2026-09-29):** se o `api` entrar em loop com `DuplicateTable` no `alembic upgrade head`, a tabela já foi criada fora do alembic (`init_db`/`create_all` com o modelo novo) e a versão travou para trás. Sintomas: `docker inspect` com `RestartCount` alto, log em `000X -> 000Y`. Roteiro (com backup prévio acima):
  ```bash
  # 1. conferir: versão travada + objeto já existe
  docker exec hora-da-tarefa-postgres-1 psql -U hora -d hora_da_tarefa -t -c "SELECT version_num FROM alembic_version;"
  # 2. carimbar como aplicada a migration cuja tabela/coluna já existe (ex.: 0009 cobre 0008+0009)
  docker run --rm --network hora-da-tarefa_internal --env-file /tmp/alembic.env hora-da-tarefa-api alembic -c /app/alembic.ini stamp 0009
  # 3. aplicar o restante (só DDL aditivo deve passar; confira o conteúdo das migrations pendentes antes)
  docker run --rm --network hora-da-tarefa_internal --env-file /tmp/alembic.env hora-da-tarefa-api alembic -c /app/alembic.ini upgrade head
  # 4. validar versão + contagens e reiniciar: docker restart hora-da-tarefa-api-1
  ```
  Atenção: a senha do `DATABASE_URL` é a configurada na API (o `POSTGRES_PASSWORD` do container postgres pode divergir — é ignorado com volume já inicializado). Nunca edite migration publicada; `stamp` só quando o objeto existir idêntico. Prevenção estrutural: `init_db()`/`create_all` só fora de prod (alembic é o único gestor de schema em prod).

## 6. Pendências conhecidas (não bloqueiam o beta)

- Rate limit em memória se Redis cair (fail-open local, fail-closed não)
- Resumo diário 07:00 (opcional), gamificação, PWA — pós-MVP
