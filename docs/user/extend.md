# Extend

## Add a new Telegram command

1. Add the command to `CommandKind` in `seharness/telegram/commands.py`.
2. Add a handler method in `seharness/telegram/handlers.py`.
3. Map the new `CommandKind` → handler class in `COMMAND_HANDLERS`.
4. Register the command in `TelegramBotRuntime.install_handlers()`.
5. Add a regression test in `tests/unit/telegram/test_*_invokes_application.py`.
6. Add a slice skill at `src/seharness/skills/harness-<cmd>/SKILL.md`.

## Add a new dashboard route

1. Add a `_Route(path, method, handler)` to `DashboardServer.__init__`.
2. Implement the async handler method.
3. Add a regression test in `tests/unit/dashboard/test_dashboard_server.py`.

## Add a new skill

1. Create `src/seharness/skills/harness-<name>/SKILL.md` with frontmatter:

```yaml
---
name: harness-<name>
description: ...
allowed-tools: [seharness.cli.<name>]
---
```

2. The skill is auto-discovered by `SkillRegistry.default()`.

## Add a new slice

See SPEC §"Build sequence" for the 12-slice sequence. Each slice is one PR; RED first, GREEN second, mutation testing on every new logical unit.
