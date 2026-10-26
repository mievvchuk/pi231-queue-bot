# Manual Duration Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans; verify RED→GREEN and the full suite.

**Goal:** Replace automatic duration calculation with student-entered duration, minimum 5 whole minutes.

**Architecture:** Nullable planned_minutes per entry; signup asks quantity then duration before committing. Quantity editor asks both; separate time button edits duration without moving the student. Existing navigation, ban and ownership checks remain.

**Tech Stack:** Python, aiogram, SQLite/PostgreSQL, pytest.

**Approved change:** User requested manual duration and minimum 5 minutes on 2026-10-26. This supersedes the automatic-time part of ../specs/2026-10-26-lab-count-design.md. Keep count 1–20/unknown. Technical integer limit matches PostgreSQL INTEGER, with no extra business maximum. Legacy/admin entries remain unknown.

## Review Focus
- No entry before valid duration, including skipped quantity.
- Values 0–4, negative, decimal and nonnumeric are rejected.
- Navigation cancels both stages; known commands remain accessible.
- Duration follows student through swap/transfer/history and clears in empty slots.
- Legacy entries have no guessed time; edits require own active entry and enforce bans.

### Task 1: Storage and display
- [ ] Watch tests fail for manual duration and minimum 5.
- [ ] Add nullable migrations, join/update services, history/carry and manual-only formatting.
- [ ] Remove obsolete automatic-time env settings and update current docs.

### Task 2: Two-stage input
- [ ] Watch dispatch tests fail for quantity→duration signup, validation and time-only editing.
- [ ] Add duration input and edit shortcut; retain cancellation in both stages.
- [ ] Run full PostgreSQL16 suite, compileall, actionlint, diff check; fresh review and fixes; update PR#3 without merge/deploy.

### Task 3: Confirm actual completed lab count
User additionally approved: after «Здав», ask actual quantity with «Усі»/numeric buttons. If 1 of 3 passed, keep position with 2 remaining. Full completion removes from queue. Preserve the student's declared time and offer manual editing, never automatically rescale it.
- [ ] RED tests for partial/full completion, invalid or stale confirmations, cancellation and unknown planned quantity.
- [ ] Add labs_passed counter per entry; partial updates remaining lab_count without moving, all closes entry. Expected entry ID/count prevent stale confirmation after edit/rejoin. Counter follows history, resets in vacant slots and new carried entry (old transferred history retains the completed work).
- [ ] Wire student button and legacy passed callback to confirmation; quantity/time/completion drafts share navigation cancellation. Unknown planned quantity may enter actual 1–20 or explicitly finish without quantity.
- [ ] Extend PostgreSQL tests and full verification, review combined change, update PR.
