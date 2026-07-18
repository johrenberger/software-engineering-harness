# Run

## Telegram bot

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_ALLOWED_CHAT_IDS=111,222
harness-telegram-bot
```

The bot supports the following commands:

| Command | Description |
|---------|-------------|
| `/start` | Show help. |
| `/help` | Show help. |
| `/status` | Current slice + last green commit. |
| `/runs` | Recent run ids. |
| `/feature <repo> <req>` | Start a feature run. |
| `/pr <branch>` | Check PR readiness. |
| `/resume <run_id>` | Resume a paused run. |
| `/cancel <run_id>` | Cancel a running run. |
| `/dashboard` | Text dashboard summary. |

## Dashboard

```bash
harness-dashboard
# Open http://127.0.0.1:8765
```

The dashboard exposes:
- `GET /` — HTML snapshot.
- `GET /api/state` — JSON snapshot.
- `GET /healthz` — `200 ok`.

The dashboard binds to `127.0.0.1` only. Public bind is rejected.

## Docker Compose

```bash
docker compose -f docker/docker-compose.yml up
```

This starts `harness-bot` + `harness-dashboard` with shared `.env`.
