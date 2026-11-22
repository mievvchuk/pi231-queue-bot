# Figma Make interface

Source: [ПІ-231 Черги для студентів](https://www.figma.com/make/OTqIq707sOJBGBTfjEhIjO/). The user supplied this design for implementation; the original code was obtained through Figma Make's **Download code** action. The prototype was rendered locally for comparison because its hosted preview could not load.

`static/design.css` adapts the exported CSS tokens, components and responsive breakpoints. `static/design.js` translates the exported React layouts to the existing native-DOM application. `static/design-adapter.css` binds the existing functional forms, administrative screens and API-backed content to those components. No React runtime, frontend build step or Figma preview service is required in production.

Manrope 400/500/600/700/800 is served locally with its OFL license. Bootstrap Icons 1.13.2 uses the original local icon font and its MIT license. Telegram avatars remain dynamic. Source specimen names, dates, queue positions, fabricated chart values, dummy tickets, weather and demo-only actions are replaced with live data or meaningful empty states. The administrative chart shows current queue participation, since the prototype's hourly activity values were samples. Existing administrative forms retain their real actions and confirmations.

## Appearance

The appearance dialog offers light, dark and system themes, six independent accent palettes, a live specimen and reduced motion. Settings use `pi231-appearance` in local storage; an existing `pi231-theme` choice is migrated. System changes are observed. Settings update without rebuilding the active form. The sidebar and mobile navigation stay connected during page refreshes; a role/mode change rebuilds the relevant navigation once.

## Selecting a queue position

Detailed queue responses include `join_slots` (`number`, `state`: `free` / `busy` / `yours`) and `append_position`. These express the existing domain rules, not a newly imposed queue capacity: existing vacancies can be taken at any position; new selectable positions are in the first ten; users can also append to the tail. Consultation sessions for subjects with a lab catalog include the laboratory and time steps.

The wizard keeps selected labs and minutes when a conflicting join is rejected. It reloads availability, clears an unavailable selection and lets the user choose again. The existing `join_queue` transaction remains authoritative. A success dialog appears only after the server confirms the position. Lab edits, partial completion, bans, queue notifications, Telegram buttons and schedule data retain their existing services.

Bootstrap keeps `swaps` as incoming requests and adds `outgoing_swaps` for the current sender. The same validation of active entries/revisions and expiry applies to both views. Only the recipient can accept or decline. Approved reusable exchanges retain their existing behavior. Timers are advisory; expiration is enforced by the server.

## Verification

Run `node scripts/check_figma_design.cjs [path-to-playwright] [optional-output-directory]`. This uses local assets, synthetic Telegram identities and mocked API responses, never production data. It checks 320/390/768/1024/1440/1920 px, local font/icon files, appearance persistence, a rejected occupied slot followed by a successful join, five schedule slots, student/admin navigation, administrative forms and errors/overflow. Python HTTP tests cover real queue vacancies, conflicting joins, the tail of a full queue and participant-only outgoing exchange visibility. PostgreSQL integration tests require a separate `TEST_DATABASE_URL`.
