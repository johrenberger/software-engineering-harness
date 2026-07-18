# Install

## From PyPI

```bash
pip install seharness
```

## From a clone

```bash
git clone https://github.com/johrenberger/software-engineering-harness.git
cd software-engineering-harness
pip install -e ".[dev]"
```

The `[dev]` extra installs pytest, ruff, mypy, bandit, pip-audit, and mutmut.

## Docker

```bash
docker build -t seharness:0.1.0 -f docker/Dockerfile .
```

The image is `<200MB` and uses `python:3.13-slim`.

## Verify

```bash
seharness --help
harness-telegram-bot --help
harness-dashboard --help
```
