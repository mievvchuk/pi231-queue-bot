# Figma Mini App refresh — 2026-10-30

Reference approved by the user: Figma Make file `9aGw8Xa00I4IQzPGHMk3Dx`, exported `src/App.tsx` and `src/index.css`. Adapt its blue palette, compact header, own records before available subjects, cards and mobile navigation to the existing native frontend.

- [x] Replace the green hero with the compact welcome and two sections; keep real subjects, registration names, positions and roles.
- [x] Show the current student's selected lab numbers and minutes on their own cards, without adding identifiers to public payloads. Regression test observed failing before implementation, then passing.
- [x] Carry the blue styles through detail, lab/confirmation sheets, profile and admin controls; preserve every existing action and bot keyboard.
- [x] Keep schedule date controls and navigation available on transient errors; show skeletons while loading, reject stale responses, keep authentication failures global.
- [x] Verify in the browser at mobile/narrow/desktop sizes and light/dark themes; run backend checks and final review.

No production data, Telegram messages, AI calls, migrations, deployment or update broadcasts. Fake names, subjects, week numbers and start estimates from the Figma prototype are not copied. Native HTML/JS stays in place; no React runtime, external fonts or new frontend build required.


Verification: full pytest suite 301 passed / 9 PostgreSQL tests skipped (no TEST_DATABASE_URL); final Mini App API regression 30 passed. Five Node state tests pass, covering restored access, local errors, stale dates, leaving pending requests and admin cards. node --check, compileall, actionlint and git diff --check pass.

Chrome against the disposable local API verified one-lab completion without a dialog, partial completion retaining position and lab #2 in the home card, admin IDs / student hiding, consultation labels, schedule skeleton/error controls and navigation during a pending request. No horizontal overflow at 320/390/1000 px, light/dark themes inspected, JavaScript error log empty. Telegram transport is a QA-only adapter outside the repository. No production requests were sent.

Fresh-context final review found one Important (successful schedule retry retained fatal state) and one Minor (admin listing reused personal position cards). Both reproduced with failing Node tests, fixed, then all five frontend tests passed. No deferred review findings. Integration uses the existing feature/lab-count-estimates branch and PR #3; automatic commit/push follows repository AGENTS.md.
