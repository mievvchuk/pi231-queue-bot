# Project structure and environment configuration

**Goal:** Organize the existing bot without changing its queue rules or deployment entry command.
**Scope:** Structural refactor requested by the owner after approving bans/navigation. Continue the same feature PR; no automatic merge or deployment.

## Structure
- `queue_bot/config.py`: environment access, rooted paths and startup validation.
- `queue_bot/application.py`, `routing.py`, `web.py`: lifecycle, ordered routers, health server.
- `queue_bot/storage/database.py`: existing schema and adapter, unchanged migrations.
- `queue_bot/handlers/`: Telegram handlers; admin sections become domain modules with explicit ordered registration.
- `queue_bot/services/`: domain services and subject catalog.
- `data/`: bundled JSON catalog and schedule; preserve explicitly configured existing paths.
- `bot.py`: compatibility entrypoint; also support `python -m queue_bot`.

## Tasks
1. Capture the current handler order; add failing package/config tests. Mechanically move modules and rewrite imports. Run regression suite and compare registration order.
2. Split admin handlers by domain without changing registration order, state names or callback formats. Keep shared states/presentation separate and re-export the existing public admin API.
3. Centralize credentials, owner/chat IDs, recognition models/endpoints/timeouts, host/port/logging/timer settings. Add complete commented `.env.example`, robust project-root `.env` loading and validation before side effects. Do not place real keys or production database values in tracked files.
4. Preserve the launch command, update docs/CI, run the full SQLite/PostgreSQL suite, compile/import checks and independent review. Commit and push to the existing PR; report deployment environment requirements.

## Constraints
Preserve repeat swaps, replacement archival, bans, route ordering and database schema/data. Do not modify UPDATES or add update broadcasts. Never run the bot against production while testing. Root JSON files, when still present from an existing persistent deployment, take priority over bundled data defaults. The owner ID has no personal hardcoded fallback.
