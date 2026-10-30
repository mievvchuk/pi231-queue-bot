# Schedule header notes

**Goal:** Recognize global notes above a replacement table and apply the referenced day, week and source pair to the stated target pair. Example: fourth pairs follow Thursday's first pair, denominator.

**Architecture:** Reuse the existing configured Groq/OpenAI vision request and Render credentials (explicitly chosen by the user). Extract structured scheduleOverrides alongside group rows. Resolve actual subjects/teachers/rooms from local schedule.json, never ask the model to invent them. Translate to normal replacements for the existing administrator preview/edit/confirm flow. No provider change, live API test, new keys or update broadcasts.

**Rules:** A header can apply even when the group has no table row. A concrete group-specific row overrides the general mapping; “По розкладу” keeps the mapping. Restrict rows to the requested group. Reject ambiguous notes, unsupported references and missing source lessons with a helpful error rather than silently ignoring them. Keep target pair number and target date, using the source's week type only for the referenced lesson. Other pairs remain untouched.

- [x] Add failing tests using mocked vision JSON for header-only, row precedence, unknown references, group filtering and saved schedule.
- [x] Extend extraction prompt and resolve structured mappings against local schedule.
- [x] Show the recognized note in the existing editable preview.
- [x] Run regression tests and review; commit/push is the final publication step.

**Verification:** 261 tests passed with disposable PostgreSQL 16. Recognition tests use mocked provider responses; no real photo request or paid API call was made. Static review identified date/field validation and source-room inheritance; these were fixed and covered. Provider/key/env settings remain unchanged. The user's example resolves to Людино-машинна взаємодія using the repository's current timetable.
