# Hora da Tarefa

SaaS que organiza a lição de casa dos filhos de ponta a ponta: o responsável envia a **foto da tarefa** (app web ou Telegram), a IA extrai matéria/prazo/enunciado, o motor sugere o melhor horário livre (grade + atividades + sono) e o bot lembra e cobra a conclusão.

> **Status:** beta na VPS · **Versão docs:** 1.9 (2026-10-04) · **Docs:** [PRD](PRD.md) (produto) · [SPECS](SPECS.md) (técnica) · [Go-Live](docs/GO-LIVE.md) (runbook) · [Auditoria](AUDITORIA.md) (segurança) · [Telas](design/SCREENS.md) (Stitch)

## Stack

| Camada | Tecnologia |
|---|---|
| Frontend | Next.js 14 (App Router) + TypeScript + Tailwind + shadcn/ui |
| Backend | Python 3.11 + FastAPI + SQLAlchemy 2 + Alembic |
| Banco / fila / storage | PostgreSQL 16 + Redis 7 + MinIO (S3-compatível) |
| IA | 100% via OpenRouter (`meta/muse-spark-1.3-contributor` — família nex delistada em 24-25/09/2026, 404 No endpoints) — nenhum modelo local |
| Bot Telegram | Handlers próprios sobre `httpx` (sem aiogram); **polling** em produção (`RUN_MODE=polling`), webhook opcional pós-DNS |
| Proxy | Caddy (TLS automático com domínio) |
| Lembretes | Beat APScheduler dentro da API (tick 1/min + purge diário) |

## Estrutura

```
├── frontend/          # Next.js: dashboard, tarefas, crianças, calendário, configurações, onboarding, login, área da criança (/crianca)
├── backend/
│   ├── app/
│   │   ├── api/       # routers FastAPI (auth, homeworks, children, child, suggestions, notifications, usage, admin, telegram)
│   │   ├── bot/       # handlers Telegram + polling + API HTTP (httpx)
│   │   ├── core/      # config, security (JWT/Argon2id), db, ratelimit, storage
│   │   ├── models/    # AppUser, Child (+ChildAccess), SchoolSchedule, Activity, Homework, SuggestionSlot, Notify…
│   │   ├── services/  # vision_openrouter, scheduling, textnorm
│   │   └── tasks/     # extract, routine, notify, users, admin, beat, child_access (persistência via SQLAlchemy)
│   ├── alembic/versions/  # migrations 0001–0014 (0014: lookup O(1) do código da criança)
│   └── tests/         # 28 arquivos de teste, 182 testes (pytest)
├── ai/prompts/        # system prompt da extração
├── design/SCREENS.md  # telas de referência (Stitch)
├── docker-compose.yml # postgres + redis + minio + api + web + caddy
└── docs/GO-LIVE.md    # runbook de deploy e operação
```

## Desenvolvimento local

```bash
cp .env.example .env        # preencher segredos (nunca commitar .env)
docker compose up -d --build
```

- Web: http://localhost:3100 · API (via Caddy): http://localhost:8081 (portas do `docker-compose.override.yml` local; no compose base são `:3000` e `:80`)
- `TELEGRAM_LIVE_SEND` e `OPENROUTER_MODEL` do `.env` são respeitados pelo compose (sem valor fixo).
- Migrations aplicam sozinhas no boot do container `api` (`alembic upgrade head`).
- Docs interativas (fora de prod): `/docs`

## Testes

```bash
PYTHONPATH=backend python3 -m pytest backend/tests -q   # 182 testes, sem IA (mocks)
```

Integração IA live é manual/noturna (`OPENROUTER_API_KEY` + fixtures sem PII) — ver SPECS §9.2.

## Deploy (VPS)

A VPS **não tem clone git** — o sync é via `rsync` + rebuild. Roteiro completo em [docs/GO-LIVE.md](docs/GO-LIVE.md):

```bash
rsync -avz -e "ssh -i $SSH_KEY" backend/app/bot/handlers.py root@$VPS:/opt/hora_da_tarefa/backend/app/bot/handlers.py
ssh -i $SSH_KEY root@$VPS "cd /opt/hora_da_tarefa && docker compose build -q api && docker compose up -d api"
```

Verificação: `GET /healthz` e `/readyz` no container `api`.

## Bot Telegram

Comandos: `/start` `/ajuda` `/hoje` `/tarefas` `/concluir <id>` `/criancas`. Acesso só com conta vinculada e **aprovada** (código de 6 dígitos gerado em Configurações → `/start <código>`). Sem vínculo, nada é criado nem listado. Listas mostram `• {criança} · {matéria} — {título} [status]` com **id curto (8 chars)** aceito pelo `/concluir`. Lembretes do beat saem direto (live) ou via outbox do polling.

## Contas e aprovação (RF-24)

Conta nova nasce **pendente** e só usa a plataforma após aprovação do admin (login, vínculo Telegram e bot retornam `ACCOUNT_PENDING`). Rejeitada → `ACCOUNT_REJECTED` + sessões revogadas. Admin aprova/rejeita via `POST /v1/admin/users/:id/approve|reject` (sem UI no beta — ver `docs/GO-LIVE.md` §4).

## Área da criança (RF-25)

A criança entra em `/crianca` com o **código de acesso** (8 chars, gerado pelo responsável em Crianças) e vê **todas as suas tarefas em modo somente leitura** (`/crianca/tarefas`, sem sidebar, mobile-first). Token JWT isolado `type="child_access"` (sessão de 2h) — sem acesso às rotas do responsável e vice-versa. Login com rate limit + lockout; revogar o código derruba o acesso na hora.

## Regras de contribuição

- **Spec-first:** comportamento novo/mudado exige atualização de `SPECS.md` (e `PRD.md`, se produto) **no mesmo commit**.
- **Um commit por fix**, TDD (RED→GREEN), sem segredo no git (`git diff --cached | grep -i -E "sk-or|AAH"` deve sair vazio).
- Convenções de API: `429 RATE_LIMITED` + `Retry-After`, `403` cross-account, FSM de status validada (`409` fora da matriz).
