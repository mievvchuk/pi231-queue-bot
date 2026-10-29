# Ten-minute exchange requests

**Goal:** Implement the user's explicit rule: one pending exchange between a pair in a queue, ten minutes to answer, and retry after expiry.

**Architecture:** Persist a unique invitation token, participants, entry snapshots and UTC deadline in SQLite/PostgreSQL. Both invitation entry points share a service. Validate the deadline on every answer and retry, including after restart. Old Telegram messages can remain visible, but their expired buttons cannot perform an exchange. No update broadcasts.

**Decisions:** A fresh request is allowed immediately after an answer or delivery failure. Per the user's earlier explicit decision, accepted exchanges remain repeatable; only unanswered invitations expire after ten minutes. Reverse invitations count as duplicates. Pre-upgrade buttons lack a verifiable deadline and require a fresh invitation. Expiration is enforced on access without a background timer. Pair uniqueness uses student IDs plus session, so rejoining a new slot cannot create duplicate invitations. PostgreSQL locks session and entry rows before responding.

- [x] Add failing service tests for expiry, duplicates, restart, recipient identity, repeatable accepted exchanges, retry, and reused slots.
- [x] Add additive schemas, service and atomic swap helper; integrate both send paths and answers.
- [x] Exercise handlers, PostgreSQL and full regression suite.
- [x] Review; commit and push to the existing feature branch/PR as the final publication step.

**Verification:** 261 tests passed with disposable PostgreSQL 16; compileall, actionlint and diff whitespace checks passed. Static review findings (student-pair key and old migration fixture version) fixed. Accepted-exchange repeatability reviewed again with no blockers. Production DB, Telegram API and Render were not accessed.
