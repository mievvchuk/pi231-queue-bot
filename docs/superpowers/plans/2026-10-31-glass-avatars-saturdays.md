# Working Saturdays, Telegram avatars and Liquid Glass (2026-10-31)

Approved request: archive a working Saturday at 13:30 Europe/Kyiv on its actual date; stop offering it as upcoming; retain historical information. Show the bot's Telegram profile photo instead of the PI monogram, the signed-in student's Telegram photo in the header/profile, initials when unavailable. Apply an Apple-inspired glass design throughout without changing queue/poll/lab rules or removing chat buttons.

Implementation choices: derive archival state from stored ISO date and Kyiv cutoff (no destructive data movement or new columns); separate active/archive admin lists and advance the default Saturday picker after the cutoff. Exact-date schedule lookup remains valid for history. Avatar endpoints accept only bot/me, use signed server identity, proxy thumbnail bytes with bounded cache/downloads and recheck permissions after awaits. No token, arbitrary Telegram ID or remote URL in the browser. CSS uses system UI fonts, Apple-style blue/neutral palettes, translucent navigation/panels, soft shadows, pill controls and short motion. Reduced motion/transparency, contrast and no-blur fallbacks remain readable.

Validation: cutoff boundaries, restart/history/next-week isolation; avatar identity/cache/no-photo/late-ban behavior; frontend fallback and avatar wiring; browser dark/light pages and glass with readable content; regression checks before commit/push. PR #5 was merged during this work; use feature/mini-app-liquid-glass from its identical-tree develop merge, with a new PR. The five-pair timetable stays included.

References: https://core.telegram.org/bots/api#getuserprofilephotos ; https://developer.apple.com/design/human-interface-guidelines/materials ; https://developer.apple.com/videos/play/wwdc2025/219/


## Verification and review resolutions
- Final Python regression: 362 passed in 54.53s, explicitly excluding the 9 PostgreSQL tests because the local Docker Linux engine is unavailable. No schema changes were introduced. All 28 Node tests pass; compileall and diff whitespace checks pass.
- Independent read-only review found two Important issues, both resolved. Five new failing regression cases reproduced stale Telegram/Mini App controls changing/deleting archived dates; handler guards now reject all such writes. Reduced-transparency root variables now match dark/light selector specificity, so opaque fallback wins the CSS cascade.
- Browser verified bot/me image decoding from authenticated blob responses, separate active/archive dates, 390px layout, no horizontal overflow at 320px, and dark/light surfaces. Test avatars were synthetic placeholders, not real student photos. Screenshots: task outputs/mini-app-glass-dark.jpg and mini-app-glass-light.jpg.
- Avatar failures/no photo preserve initials; permission checks reject late bans. Thumbnail size/type/download bounds, small-photo bot fallback and cache identity are covered offline. Delayed avatar updates patch only photo faces and preserve form nodes.
- The browser viewport override was reset, QA tabs closed and temporary fake-Telegram/SQLite server stopped. Production DB, live Telegram messages, Render settings and update broadcasts were not touched.
