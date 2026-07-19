# Software Engineering Harness

Framework-neutral Python software-engineering harness for OpenClaw.

The harness standardizes feature delivery across MiniMax and Codex using deterministic workflow state, validated artifacts, automated testing/remediation, Telegram intake, and GitHub pull-request delivery.

## Install

```bash
pip install seharness
```

or from a clone:

```bash
git clone https://github.com/johrenberger/software-engineering-harness.git
cd software-engineering-harness
pip install -e ".[dev]"
```

## Usage

### Run the Telegram bot

```bash
export TELEGRAM_BOT_TOKEN=...
export TELEGRAM_ALLOWED_CHAT_IDS=...
harness-telegram-bot
```

### Run the dashboard server

```bash
harness-dashboard
# Open http://127.0.0.1:8765
```

### Run the CLI

```bash
seharness --help
```

## Container image

```bash
docker build -t seharness:0.1.0 -f docker/Dockerfile .
docker compose -f docker/docker-compose.yml up
```

## Documentation

- [Install](docs/user/install.md)
- [Configure](docs/user/configure.md)
- [Run](docs/user/run.md)
- [Extend](docs/user/extend.md)
- [Sandbox threat model](docs/user/sandbox.md)

## Security

To report a vulnerability, **do not file a public issue** &mdash; use
[GitHub Security Advisories](https://github.com/johrenberger/software-engineering-harness/security/advisories/new)
or email `security@openclaw.eu`. See [SECURITY.md](SECURITY.md) for
the response timeline and supported versions.

## License

MIT.
