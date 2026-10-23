# Bans, navigation and Gitflow Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Ship the approved ban and navigation behavior without changing repeatable swaps or replacement archival.
**Architecture:** Add a focused restriction service and ban wizard; preserve existing queue services and routers. Extend both database schemas additively. Build on the existing isolated audit clone and publish a feature PR to develop.
**Tech Stack:** Python 3.11, aiogram 3, SQLite, psycopg/PostgreSQL, pytest, GitHub Actions.
**Spec:** docs/superpowers/specs/2026-10-23-bans-navigation.md

## Global Constraints
- Repeat swaps and existing replacement archival remain unchanged.
- Ban duration is N times 24 hours; store UTC, display Europe/Kyiv.
- Remove banned users from active queues atomically; preserve history and never restore old places automatically.
- Never broadcast bot updates or use production credentials for tests.
- Do not merge or deploy automatically; commit and push verified changes as required by AGENTS.md.

## Review Focus
- Bans must block old callbacks, private messages, group commands and internal poll answers.
- Expiration must work after restarting; indefinite blocks must not expire.
- Admin-to-student switching must preserve queue context while discarding incomplete dialogs.
- Missing/archived queue during switching must lead to a useful menu.
- Additive migrations must preserve pre-existing users, queue entries and history on both databases.

### Task 1: Restriction service and migration
Files: db.py, services/restrictions.py, services/users.py, services/queues.py, handlers/common.py, handlers/polls.py; tests/test_restrictions.py.
Interfaces: restriction_message(conn, telegram_id, now=None) -> str | None; require_available(conn, telegram_id); set_restriction(conn, actor_id, user_id, days=None, now=None); clear_restriction(conn, actor_id, user_id).
- [ ] Write tests for timed/indefinite restrictions, expiry/restart, permission denial, administrator protection, atomic removal and rollback, middleware and service denial.
- [ ] Run tests and confirm missing-feature failures.
- [ ] Implement appended SQLite migration and matching PostgreSQL ALTER; restriction service and enforcement at user and student mutation boundaries.
- [ ] Run tests and commit.

### Task 2: Ban wizard and explicit navigation
Files: handlers/bans.py, handlers/navigation.py, handlers/common.py, handlers/admin.py, handlers/start.py, handlers/student.py, bot.py; tests/test_navigation_bans.py.
Interfaces: NavigationFlow.student_queue, NavigationFlow.admin_queue; switch_student/switch_admin preserve session_id and set explicit mode state. BanFlow.entering_days/confirming; registered before generic admin/student handlers.
- [ ] Write Dispatcher tests for ban input/confirmation/cancel, stale context, permission checks and queue-mode roundtrip.
- [ ] Confirm failures, then implement focused routers and compatible legacy button aliases.
- [ ] Route home/back within the active mode, discard input drafts on switches, show expiration on user cards.
- [ ] Run focused tests and commit.

### Task 3: Consultation and startup configuration
Files: services/queues.py, config.py, bot.py, .env.example; tests/test_release_regressions.py.
- [ ] Test consultation renewal with simultaneous lesson queue, repeat swaps, existing archival policy and no embedded database fallback.
- [ ] Confirm intended existing behaviors pass and consultation/config tests fail.
- [ ] Preserve queue_type in finish_session; require environment DATABASE_URL before web server/background startup.
- [ ] Run tests and commit.

### Task 4: Gitflow, CI and PostgreSQL validation
Files: .github/workflows/tests.yml, .github/ISSUE_TEMPLATE/*, .github/pull_request_template.md, CONTRIBUTING.md, README.md, tests/test_postgres.py.
- [ ] Add isolated PostgreSQL-schema tests for migrations, ban removal, expiry and consultation alongside SQLite suite.
- [ ] Add PostgreSQL CI service and documented Gitflow/rollout steps. Create develop at the pre-change master SHA and push the feature branch.
- [ ] Run full tests, compile/import checks and independent whole-branch review; address important findings with regression tests.
- [ ] Create and attach a PR targeting develop; verify GitHub CI. Report any unavailable branch-protection or runtime integration checks precisely.
