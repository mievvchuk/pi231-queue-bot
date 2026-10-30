# Claude prototype adaptation — implementation plan

**Goal:** Adapt all student/admin/owner screens from the supplied HTML to the real bot, preserving Telegram keyboards and rules; use local Bootstrap Icons for subjects.
**Spec:** User's complete prompt and supplied `ПІ-231 · Telegram Mini App.html`; decoded visual reference in `../claude-reference/screen-0-source.html`. Existing domain services are authoritative; prototype scripts/dummy data are reference only.
**Architecture:** Native JS frontend + aiohttp HTTP adapters over existing services. Separate feature UI module and administrative adapter. No secret/client role authority or fake production success.

## Constraints
- Keep branch feature/lab-count-estimates and existing checkout. No deployment or production DB access. No automated updates broadcasts. Commit/push verified changes per AGENTS.md.
- Same lab rules, consultation bypass, one/unknown lab immediate completion, repeated accepted swaps, ten-minute unanswered swaps, timed bans removing active entries, IDs admin-only.
- All mutations server-authenticated, validate current role after body read, synchronous service writes before network awaits. Owner checks server-side. Uploads bounded, no arbitrary file writes or path traversal. Return only public projections and JSON-serializable data.
- Adapt approved charcoal #0F1114 / #171A1F / blue #3F68B8, Onest and compact layout. Do not copy demo names/dates/bells/data or x-dc framework.

## Tasks
- [x] Add student registration/catalog/create queue/wish/not-passed/proposals/bug reports adapter and tests.
- [x] Add admin/owner/poll adapters with authorization and regression tests.
- [x] Build feature screens and integrate navigation into existing app; implement full flows through real API.
- [x] Adapt reference styles/fonts, Bootstrap Icons and responsive behavior with local assets/license.
- [x] Verify full tests, browser flows (fake users/offline Bot/AI), security review. Integration: commit/push and update the existing PR per AGENTS.md.

## Shared frontend contract
Existing app API retained. New UI script `/app/features.js`, loaded before app.js, defines `window.createPI231Features({S,el,btn,small,badge,icon,api,toast,sheet,closeSheet,mutate,load,navigate,isAdmin})` returning `{page(key),open(key)}`. Feature page keys polls/help/bugs/create/proposals/admin-history/admin-subjects/admin-replacements/admin-saturdays/admin-polls/admin-reports/owner/admin-admins/admin-bugs/admin-proposals. `open(key)` sets S.feature key; main render calls page. Module owns async load/render state; uses S.epoch/current feature to avoid stale responses and helper render supplied by parent. Add `render` to helper object. `mutate` existing posts+closes sheet+reloads bootstrap; features may use own submit API/refresh for feature-specific flows. No innerHTML. Build reusable forms and confirmations.

## HTTP contract: student (parent)
- GET /catalog => {subjects:[{id,name,teachers:[{id,name}]}]}
- GET /bootstrap includes me.is_registered, me.is_owner, name nullable (unregistered empty queues/swaps).
- POST /register {full_name} => {message}; allowed for signed unregistered identity only; restrictions apply.
- POST /queues {subject_id,teacher_id,type:'normal'|'consultation'} => {message,session_id}
- POST /queues/{sid}/not-passed + {entry_id,revision}; POST /queues/{sid}/wish + {entry_id,revision,wish:'higher'|'lower'|null}
- GET /proposals => {items:[{id,kind,subject_name,teacher_name,status}]}; POST {kind:'subject'|'teacher',subject_id?,subject_name?,teacher_name}
- GET /bugs => {items:[{id,text,status,admin_reply,created_at}]}; POST {text}.
- Existing schedule/date available; week UI may fetch seven dates.

## HTTP contract: admin backend agent
Use API guard and helpers from api.py (imports inside handlers avoid cycles); expose setup(app), routes GET/POST /admin/manage/{section}. All require_admin plus owner check where stated. Return result {message,...}. Subject/teacher objects same catalog. IDs opaque server action refs admin-only.
- history GET {sessions:[{id,subject,teacher,status,date,type}],entries:[]} optional ?sid; POST action finish {session_id,create_new,carry?}.
- group GET optional or existing /admin/users; POST action add {full_name}, rename/delete/block/unban/ban {user_id,name?/days?}. Existing admin users endpoints remain.
- subjects GET {subjects}; POST action add {name,teacher_name}, add_teacher {subject_id,teacher_name}, rename {subject_id,name}, remove_teacher {subject_id,teacher_id}, delete {subject_id}; last three owner-only.
- replacements GET {active,archived}; POST action recognize {images:[base64]} => {draft:{date,replacements,...}}, save {items:[public replacement fields]}, clear. Recognition only drafts, no saves; validate before commit; preserve source header resolution. Final save sends existing replacement notification only as explicit UI action. Server-side recheck auth/role/ban/maintenance after recognition awaits before anything save-related.
- saturdays GET {items:[{date,source_day,week_type}]}; POST action save/delete/notify {date,source_day,week_type}. Existing service keys adapt to contract.
- reports GET optional ?subject_id, ?format=csv => {statistics: get_global_statistics object, subject_report?:domain object, subjects}; CSV response downloadable via authorized fetch (UI Blob). No privileged IDs in student data.
- owner GET {maintenance:boolean,end_time,admins:[{telegram_id}],group_chat_id}; POST action announce {text}, maintenance {active,end_time}. Owner-only. Explicit confirmation before network sends.
- admins GET {items}; POST action add/remove {telegram_id}; owner-only.
- bugs GET {items:all report public+admin fields}; POST action reply/resolve {report_id,text?}; owner-only.
- proposals GET {items}; POST action approve/reject {id}; owner-only; use subjects services and same semantics as bot.
- polls GET/POST admin section creation and list; POST action create {question,options:[str],is_anonymous,allows_multiple,target:'private'|'group',minutes?:positive|null}, close {poll_id}, group {chat_id}. Public GET /polls {items}; GET /polls/{pid} {poll: object, results: object, my_options:[zero-based],can_vote}; POST /polls/{pid}/vote {options:[zero-based]}. Public poll object includes id,question,options:[str],is_anonymous,allows_multiple,status,closes_at,target. Never expose anonymous voter names/IDs; live Telegram votes and Mini App share domain records. Do not invent counts or bypass deadline. Audit anonymous group inability to deduplicate native vs web votes; report concrete integration decision before implementing unsafe totals.

## Validation ledger
Plan just created. Reference archive decoded statically; no imported scripts executed. Tasks independent by file ownership: parent api.py/app.js/web.py/student tests; admin agent admin_api.py/poll adapter/tests; UI agent features.js/tests; visual agent theme.css/icons.js/local assets/licenses. Integration handled by parent.


## Final verification
- Full pytest regression with disposable PostgreSQL 16: 350 passed in 94.78s; no skipped tests. Expanded PostgreSQL HTTP integration (batch participants, history, reports, anonymous web vote, owner bug timestamps) passed separately after extending the existing test.
- 23 Node tests pass: feature handlers, stale request isolation, owner/privacy gates, restored access, busy state, week/date loading, anonymous/public results, vote withdrawal, schedule import/CSV wiring and text-safe empty actions. The last browser-found literal-null artifact was reproduced with a native-append-compatible fake DOM, then fixed.
- compileall, node --check, actionlint and git diff --check passed. CI now runs both frontend test files.
- Chrome on the disposable SQLite server verified dark mobile UI and Bootstrap subject SVGs, admin dashboard/diagrams, real registration without role escalation, poll creation and anonymous voting, two numbered labs with 5-minute minimum, partial completion retaining place and lab #2, wish changes, own text bug reports, week timetable, replacement import/export controls and owner controls.
- Browser file-chooser automation was blocked by the Chrome extension's file-URL permission. No extension permissions changed; image/report/recognition API paths were covered by offline regression tests. Telegram and recognition adapters were fake throughout. Real Telegram opening and deliveries remain deployment smoke checks.
- Screenshot: task outputs/mini-app-claude-mobile.jpg. Font and Bootstrap Icons licenses are included locally; the exported prototype runtime and dummy data are not shipped.

## Review resolutions
Fresh-context final review found no Critical permission/privacy issues. Important findings were reproduced and fixed: old anonymous-private close snapshots overwrote web votes; ordinary no-catalog subjects lacked retry; owner could not view report screenshots; oversized photo captions suppressed notifications. Minor busy-refresh, vote withdrawal, schedule/subject exports and fatal-retry issues were also covered and fixed. A web-managed legacy anonymous-private vote (including withdrawal) remains web-managed persistently; final native snapshots still update untouched ballots. User explicitly chose new Mini App polls and read-only old anonymous group results.

## Integration
Use existing feature/lab-count-estimates branch and PR #3. Production DB, Render deployment, live Telegram recipients and paid recognition were not accessed. Old chat keyboards remain in place. No updates broadcast entries or new schema migrations were added.

## Follow-up: session participants and PDF reports (2026-10-31)
- Admin history opens a focused session detail with active and historical participants, readable statuses, explicit empty state and return to session list; unknown session IDs are rejected. Retry preserves the selected query.
- Shared read-only ReportLab renderer provides global and subject PDF reports in Mini App and Telegram. Embedded licensed DejaVu Sans supports Ukrainian on Render; tables wrap, repeat headers and paginate with numbered footers. Global totals retain all students and per-subject data; labels explain that completed entries are not individual labs or grades.
- PDF rendering runs outside the aiohttp event loop using data snapshots; admin access is checked again after rendering. Legacy Telegram CSV buttons route to PDF; legacy CSV API remains compatible.
- Regression evidence: full run with disposable PostgreSQL 16 had 354 passed and one expected router-name snapshot mismatch after CSV handler rename. Updated only that snapshot and reran all 5 project-structure tests successfully. All 24 Node tests pass; PDF tests include Unicode extraction, 100-row pagination and Telegram document delivery. compileall, node --check and git diff --check passed.
- Browser verified populated/empty admin session detail, return to list and actual PDF button download. Download automation event timed out, but the resulting 29,358-byte PDF existed in Downloads and was verified with pypdf and Poppler; both pages rendered correctly. A separate 40-row subject PDF was inspected visually.
- No timetable data/code, production DB, migrations, Render settings or automatic broadcasts changed. QA used temporary data and fake Telegram; its server and PostgreSQL container were removed after checking.
- Integration follow-up: PR #3 was already merged into develop. Its tree matches the tested starting point; publish this fix on bugfix/admin-session-pdf with a new PR to develop.
