# Configure

## Environment variables

Copy `.env.example` to `.env`:

```bash
cp .env.example .env
```

Required:

| Variable | Description |
|----------|-------------|
| `TELEGRAM_BOT_TOKEN` | Bot API token from [@BotFather](https://t.me/BotFather). |
| `TELEGRAM_ALLOWED_CHAT_IDS` | Comma-separated list of chat ids permitted to invoke commands. Empty list rejects ALL (fail-secure). |
| `GITHUB_TOKEN` | GitHub PAT used by slice-9's delivery subsystem. |

Optional:

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_MODE` | `polling` | `polling` (no public URL needed) or `webhook` (requires public HTTPS). |
| `HARNESS_DASHBOARD_HOST` | `127.0.0.1` | Loopback only — public bind is rejected. |
| `HARNESS_DASHBOARD_PORT` | `8765` | Dashboard port. |
| `OPENCLAW_HOME` | `~/.openclaw` | Workspace root. |

## controller.yaml

The slice-12 `ApplicationServiceFactory` consumes `controller.yaml`:

```yaml
ci_monitor: stub
task_executor: stub
run_ledger: stub
```

Each slot accepts `stub` (test mode) or a fully qualified Python path to a production implementation.

See [`examples/controller.yaml`](../../examples/controller.yaml).
