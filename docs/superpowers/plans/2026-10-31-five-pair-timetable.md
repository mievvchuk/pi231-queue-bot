# Five-pair timetable follow-up (2026-10-31)

User supplied schedule (3).json and clarified that Tuesday has no fifth lesson. Commit that exact schedule: Monday and Thursday have fifth-pair Programming in Internet; Monday pair 1 is empty. Preserve parity, teachers and rooms from the file.

Use one server bell schedule: 08:30-09:50, 10:00-11:20, 11:50-13:10, 13:20-14:40, 14:50-16:10. Actual lessons remain distinct from display slots so an empty cell never creates a class or replacement. The API supplies five numbered, timed slots with grouped lessons; day/week views render the same slots, including free days. Preserve any additional actual pair rather than silently hiding it. Telegram's real lessons use these same bell times. Existing schedule imports and replacement semantics remain intact; no automatic rewriting of configured runtime paths or production database.

Validation: regression tests for committed parity/fifth pairs, fixed times, gaps, new fifth-pair replacements, API empty days, five day cells and 35 week cells. Check browser Monday and Tuesday, Python/Node regressions, compileall and diff; commit/push a separate bugfix branch and PR to develop.

## Verification
- 350 Python tests passed in the complete run. Nine PostgreSQL fixtures could not connect: Docker Desktop's Linux engine was unavailable; no production database was used. Those integration checks remain a CI requirement rather than a claimed local pass.
- All 25 Node tests passed, including five day cells and 35 week cells, with no invented lessons. Focused schedule/recognition/API tests: 63 passed.
- compileall and git diff --check passed. User JSON and committed JSON match semantically, including both week parities and subgroup teacher/room details.
- Local browser verified Monday's empty pair 1 and real pair 5, Tuesday's empty pair 5, and all 35 weekly slots with the requested bells. Screenshot: task outputs/schedule-five-slots.jpg. QA server used temporary SQLite and fake Telegram and was stopped afterward.
