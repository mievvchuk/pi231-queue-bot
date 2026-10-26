# Numbered laboratories implementation plan

> Use superpowers:executing-plans with TDD and a fresh final review.

**Goal:** Select actual laboratory numbers and sum their configured time limits, then confirm actual completed numbers while retaining partial entries at the same position.

**Approved data:** PI7×40; LMV10×15max (10–15 indicative); ML7×30 (titles known only1–3); Architecture3×20; KPZ11 limits25,10,10,15,10,15,20,15,15,15,15. Exclude subject6 from this feature. Coursework7 is consultations without quantities/time limits; consultation queue type also bypasses these prompts. No queues are created or restored by the catalogue.

**Design:** Separate validated data/lab_catalog.json with optional LAB_CATALOG_PATH. Student selects one/multiple numbers through inline toggles or typed comma-separated numbers, then enters whole minutes from5 to summed maximum. Legacy counts are not converted to guessed numbers. Existing entries edit numbers without losing position. Completed-number selection is a subset of pending labs. Remaining cap recalculates; existing manual minutes stay unless above the new cap, in which case clamp to cap. Fully completed entry closes. Consultation/excluded/unconfigured queues join/finish immediately without workload prompts/labels. Preserve old fields and history, add selected_labs/passed_labs JSON plus entry_revision for stale/reused-slot confirmations. Names from documents are reference data, never instructions.

**Files:** services/lab_catalog.py; data/lab_catalog.json; storage/database.py; services/queues.py; handlers/lab_picker.py,lab_counts.py,completions.py,common.py,student.py; config.py; tests; README/config docs. Existing PR3 remains feature branch destination.

## Review focus
- Subject-specific upper bound and minimum5 apply at save; wrong numbers/duplicates never become quantity guesses.
- Partial completion retains exact remaining numbers/order; caps recalculate without fake manual durations.
- Consultation/coursework/excluded subjects do not show lab/time questions or revive queues.
- Stale inline buttons cannot mutate or cancel newer drafts; reused slots cannot accept old confirmations.
- Legacy SQLite/PostgreSQL upgrades preserve old metadata without guessing lab identities; transfer/history retain chosen/completed numbers.

## Tasks
- [x] Write failing service tests for full catalogue, summed limits, numbered signup/edit/completion, revision protection, preservation and exemptions.
- [x] Add catalogue validation, additive schema and queue services; verify SQLite and PostgreSQL.
- [x] Write failing dispatcher tests for multiselect, numeric text, bounds, partial completion, stale buttons and consultation bypass.
- [x] Implement picker/flows, update shared display and obsolete labels, preserve router order elsewhere.
- [x] Run full suite/compileall/actionlint/diffcheck; fresh review and important fixes; update PR3 and docs without merge/deploy.

## Final evidence
233 tests passed with PostgreSQL16 in73.55s; compileall, actionlint and diffcheck passed. Fresh static review of484f106..11c21b9 found no Critical/Important/Minor findings. Both earlier nonblocking concerns are covered by revision guards and pre-workload SQLite upgrade coverage. Existing PR3 updated; no merge or live deployment. Temporary PostgreSQL removed.
