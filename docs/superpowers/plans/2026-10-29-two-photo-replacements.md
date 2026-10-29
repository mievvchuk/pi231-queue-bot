# Two-photo replacement tables

**Goal:** Accept one photo or two photos sent as a Telegram album and recognize them together. The header/date/general note may be on one image while the group's row is on the other.

**Architecture:** Collect media_group_id updates scoped by chat and sender with a short quiet period; one handler owns the batch. Send both image blocks in one existing provider request, preserving local header resolution and one editable preview. Keep the existing single-image function as a compatibility wrapper. Existing Render AI credentials are already authorized for reuse; no new key or provider.

**UX:** Send two photos together as one album. Independent single-photo messages remain independent. At most two images; larger albums receive one error and no partial recognition. Group messages and non-admins remain excluded. No update broadcasts or schema changes.

- [x] Failing tests: album grouping/order/dedup/isolation, image batch bounds, one provider call and one handler preview.
- [x] Implement collector, multi-image recognition and handler integration; update admin instructions.
- [x] Run regression suite and review; commit/push is the final publication step.

**Verification:** 275 tests passed in 47.64s with disposable PostgreSQL 16. compileall, actionlint and whitespace checks passed. New coverage includes a delayed second part, cancellation cleanup, late-part retry, max-two rejection, admin-only access, one combined provider payload, one preview, and the supplied example (third pair Філософія plus fourth pair ЛМВ from the header reference). API responses are mocked; no live Telegram/vision calls, production DB access or Render deploy occurred. Fresh static review found no blockers. An unusually delayed album update after the 1.2-second quiet period requires resending both images as a new album.
