# Liquid Optics v2.8 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перенести дизайн і взаємодії Liquid Optics v2.8 у чинну Mini App без зміни її бізнес-логіки.

**Architecture:** CSS-токени та material-стилі живуть у `glass.css`; reusable DOM-поведінка для лінз і press-physics живе у `app.js`. Наявний рендерер продовжує створювати справжні екрани та API-виклики, а лише додає data-атрибути/контейнери для візуальної поведінки.

**Tech Stack:** vanilla JavaScript, CSS custom properties, CSS backdrop-filter, Telegram WebApp API, Node test runner.

**Spec:** `docs/superpowers/specs/2026-10-31-liquid-optics-v28-design.md`

## Global Constraints

- Не змінювати API, права доступу, дані черг чи логіку бота.
- Не додавати залежностей, секретів або автоматичних розсилок.
- Зберегти доступність: keyboard, 44 px targets, reduced motion/transparency та fallback без blur.
- Після перевірки закомітити й запушити зміни в `origin`.

## Review Focus

- Drag навігації не має змінювати вкладку до `pointerup`; тест у Task 2.
- `pointercancel` і вертикальний scroll не мають лишати лінзу/картку в pressed-стані; тест у Task 2/3.
- Клавіатурна навігація не залежить від pointer-подій; тест у Task 2.
- Reduced motion/transparency мають прибрати лише ефекти, а не доступ до функцій; тест у Task 1.
- Вузький мобільний екран не має горизонтального overflow або перекритих кнопок; ручна перевірка в Task 4.

### Task 1: Liquid Optics foundations

**Files:**
- Modify: `queue_bot/mini_app/static/glass.css`
- Test: `tests/mini_app_frontend.test.cjs`

**Interfaces:**
- Produces CSS tokens and `.glass-*` material/press classes for current Mini App markup.

- [ ] **Step 1: Write a failing frontend test**
  Assert that the v2.8 stylesheet contains the pure-black dark canvas, no-motion fallback, transparency fallback and a lesson press class.
- [ ] **Step 2: Run the focused test to verify it fails**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 3: Implement the tokens and static materials in `glass.css`**
  Use stationary blur and transform-friendly moving elements; keep current card content legible.
- [ ] **Step 4: Run the focused test to verify it passes**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 5: Commit**
  `git add queue_bot/mini_app/static/glass.css tests/mini_app_frontend.test.cjs && git commit -m "feat: add liquid optics foundations"`

### Task 2: Shared liquid-lens controls

**Files:**
- Modify: `queue_bot/mini_app/static/app.js`
- Modify: `queue_bot/mini_app/static/glass.css`
- Test: `tests/mini_app_frontend.test.cjs`

**Interfaces:**
- Produces `attachLiquidLens(container, options)` that controls a single `.liquid-lens` child and commits selection once after pointer release.

- [ ] **Step 1: Write failing tests**
  Test a one-lens DOM contract, deferred pointer commit, nearest selection, cancel restore, keyboard selection and reduced-motion behavior.
- [ ] **Step 2: Run the focused tests to verify failure**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 3: Implement `attachLiquidLens(container, options)`**
  Cache button geometry, use pointer capture and requestAnimationFrame transform updates; call the existing navigation callbacks only upon selection commit.
- [ ] **Step 4: Apply it to bottom navigation and existing segmented controls**
  Preserve normal click semantics and current buttons/labels.
- [ ] **Step 5: Run focused tests to verify pass**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 6: Commit**
  `git add queue_bot/mini_app/static/app.js queue_bot/mini_app/static/glass.css tests/mini_app_frontend.test.cjs && git commit -m "feat: add liquid lens navigation controls"`

### Task 3: Local press optics

**Files:**
- Modify: `queue_bot/mini_app/static/app.js`
- Modify: `queue_bot/mini_app/static/glass.css`
- Test: `tests/mini_app_frontend.test.cjs`

**Interfaces:**
- Produces `installPressOptics(root)` applied after each render.

- [ ] **Step 1: Write failing tests**
  Assert press state is installed for interactive glass elements and is cleared on release/cancel without suppressing normal click.
- [ ] **Step 2: Run focused tests to verify failure**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 3: Implement `installPressOptics(root)`**
  Add bounded pointer-relative CSS variables and a no-motion path; vertical movement cancels 3D effect and does not block scroll.
- [ ] **Step 4: Mark lesson rows, cards and actionable controls**
  Apply existing markup/data attributes only; no API or queue-state changes.
- [ ] **Step 5: Run focused tests to verify pass**
  Run: `node --test tests/mini_app_frontend.test.cjs`
- [ ] **Step 6: Commit**
  `git add queue_bot/mini_app/static/app.js queue_bot/mini_app/static/glass.css tests/mini_app_frontend.test.cjs && git commit -m "feat: add liquid press optics"`

### Task 4: Full regression and mobile QA

**Files:**
- Modify: `docs/MINI_APP.md`
- Test: `tests/mini_app_frontend.test.cjs`, `tests/mini_app_features.test.cjs`, Python test suite

- [ ] **Step 1: Document the WebView visual fallbacks and gesture behavior**
- [ ] **Step 2: Run JavaScript regression suite**
  Run: `node --test tests/mini_app_frontend.test.cjs tests/mini_app_features.test.cjs`
- [ ] **Step 3: Run Python regression suite**
  Run: `$env:TEST_DATABASE_URL=''; .venv/Scripts/python -m pytest --ignore=tests/test_postgres.py -q --tb=short`
- [ ] **Step 4: Run compile/static checks and inspect 320 px light/dark rendering**
  Run: `.venv/Scripts/python -m compileall -q bot.py queue_bot` and `git diff --check`.
- [ ] **Step 5: Commit and push**
  `git add docs/MINI_APP.md && git commit -m "docs: document liquid optics controls" && git push origin HEAD`
