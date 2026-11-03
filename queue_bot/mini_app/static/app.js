"use strict";
(() => {
  const tg = window.Telegram?.WebApp,
    initData = tg?.initData || "",
    root = document.getElementById("app");
  const S = {
    tab: "home",
    queueFilter: 'all',
    feature: null,
    mode: "student",
    data: null,
    queue: null,
    schedule: null,
    scheduleView: "day",
    weekSchedule: null,
    scheduleDate: null,
    scheduleError: null,
    scheduleLoading: false,
    users: [],
    busy: false,
    loading: false,
    epoch: 0,
    avatars: { bot: null, me: null },
    avatarLoading: false,
    avatarCheckedAt: 0,
  };
  let design = null;
  const browserSession = { ready: !!initData, authenticated: false, csrf: null, enabled: false };
  function loginFailureMessage(reason) {
    const messages = {
      client_config: 'Telegram відхилив налаштування входу. Потрібно перевірити Client ID і Client Secret із BotFather у Render.',
      client_mismatch: 'Client ID сервісу не збігається з підтвердженням Telegram. Повідом адміністратора.',
      login_expired: 'Спроба входу завершилась або сервіс перезапустився. Натисни «Увійти через Telegram» ще раз.',
      browser_changed: 'Повернись у те саме вікно браузера або PWA, з якого починав вхід, і спробуй ще раз.',
      code_expired: 'Одноразовий код входу вже недійсний. Почни новий вхід кнопкою нижче.',
      cancelled: 'Підтвердження входу скасовано. Можеш спробувати ще раз.',
      signing_algorithm: 'У BotFather для Telegram Login потрібно вибрати RS256. Повідом адміністратора.',
      signing_key: 'Не вдалося отримати відповідний ключ перевірки Telegram. Спробуй пізніше.',
      telegram_unavailable: 'Не вдалося зв’язатися з Telegram для завершення входу. Спробуй за хвилину.',
      server_restarting: 'Сервіс перезапускається. Спробуй ввійти за хвилину.',
      server_busy: 'Зараз забагато активних входів. Спробуй пізніше.',
      access_denied: 'Доступ до застосунку обмежений. Звернись до адміністратора.',
      profile_missing: 'Telegram не повернув потрібний ідентифікатор профілю. Повідом адміністратора.',
      nonce_missing: 'У відповіді Telegram немає захисного підтвердження. Повідом адміністратора.',
      nonce_mismatch: 'Захисне підтвердження входу не збігається. Почни новий вхід.',
      token_expired: 'Підтвердження Telegram прострочилося. Почни новий вхід.',
      token_signature: 'Не вдалося перевірити підпис Telegram. Повідом адміністратора.',
      token_claims: 'Не вдалося перевірити дані підтвердження Telegram. Повідом адміністратора.',
      token_response: 'Telegram повернув неочікувану відповідь. Повідом адміністратора.',
      provider_response: 'Telegram не повернув код підтвердження. Спробуй почати вхід заново.',
      server_error: 'Сервер не зміг завершити вхід. Повідом адміністратора.',
    };
    return Object.hasOwn(messages, reason) ? messages[reason] + ' Код для підтримки: ' + reason + '.'
      : 'Вхід не завершено. Спробуй ще раз.';
  }
  function authHeaders() {
    return initData ? { Authorization: 'tma ' + initData }
      : { 'X-CSRF-Token': browserSession.csrf || '' };
  }
  const paths = {
    up: 'M12 20V4 M5 11l7-7 7 7',
    down: 'M12 4v16 M5 13l7 7 7-7',
    home: 'M3 11 12 3l9 8 M5 10v11h5v-7h4v7h5V10',
    more: 'M5 12h.01 M12 12h.01 M19 12h.01',
    queues: "M4 4h16v4H4z M4 12h16 M4 17h10 M4 21h7",
    schedule: "M4 5h16v16H4z M8 3v4 M16 3v4 M4 11h16 M8 15h2 M14 15h2 M8 18h2",
    profile: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 21v-2a8 6 0 0 1 16 0v2",
    admin: "M12 3 3 7v6c0 4 9 9 9 9s9-5 9-9V7z M8 12l3 3 5-6",
    arrow: "M5 12h14 M14 7l5 5-5 5",
    refresh:
      "M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-1l2 6 M4 12l2 6a7 7 0 0 0 12-1",
    back: "M19 12H5 M10 7l-5 5 5 5",
    close: "M6 6l12 12 M18 6 6 18",
    sun: 'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v2 M12 20v2 M2 12h2 M20 12h2 M5 5l1.5 1.5 M17.5 17.5 19 19 M5 19l1.5-1.5 M17.5 6.5 19 5',
    moon: 'M20 15A9 9 0 0 1 9 4a9 9 0 1 0 11 11Z',
  };
  function icon(name) {
    if (window.PI231Icon && !['home','more','up','down','sun','moon'].includes(name)) return window.PI231Icon(name);
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    for (const [k, v] of Object.entries({
      viewBox: "0 0 24 24",
      fill: "none",
      stroke: "currentColor",
      "stroke-width": "1.7",
      "stroke-linecap": "round",
      "stroke-linejoin": "round",
      "aria-hidden": "true",
    }))
      svg.setAttribute(k, v);
    const p = document.createElementNS(svg.namespaceURI, "path");
    p.setAttribute("d", paths[name] || paths.arrow);
    svg.append(p);
    return svg;
  }
  function el(tag, attrs = {}, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs)) {
      if (key === "class") node.className = value;
      else if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (value !== undefined && value !== null && value !== false)
        node.setAttribute(key, value === true ? "" : String(value));
    }
    for (const child of children.flat(Infinity))
      if (child !== null && child !== undefined && child !== false)
        node.append(
          child instanceof Node
            ? child
            : document.createTextNode(String(child)),
        );
    return node;
  }
  const btn = (text, fn, cls = "button") =>
    el("button", { type: "button", class: cls, onclick: fn }, text);
  const liquidCleanups = [];
  function attachLiquidLens(container, { buttons, selectedIndex = 0, onSelect, persistent = false }) {
    if (!container || !buttons?.length) return null;
    const lens = el("span", { class: "liquid-lens", "aria-hidden": true });
    let selected = selectedIndex;
    let pointerId = null;
    let origin = null;
    let hovered = selected;
    let dragged = false;
    let suppressClickUntil = 0;
    let geometry = null;
    let moveFrame = null;
    let latestX = 0;
    let ready = false;
    const cleanup = persistent ? [] : liquidCleanups;
    container.append(lens);
    container.classList.add("liquid-control");

    const measure = () => {
      geometry = {
        frame: container.getBoundingClientRect(),
        buttons: buttons.map((node) => node.getBoundingClientRect()),
      };
    };
    const rects = () => {
      if (!geometry) measure();
      return geometry.buttons;
    };
    const setLens = (index, x) => {
      const rect = rects()[index];
      const frame = geometry.frame;
      if (!frame?.width || !rect?.width) return;
      const width = rect.width;
      const center = Math.max(frame.left + width / 2,
        Math.min(frame.right - width / 2, x ?? rect.left + width / 2));
      lens.style.width = width + "px";
      lens.style.transform =
        "translate3d(" + (center - frame.left - width / 2) + "px, 0, 0)";
      if (!ready) {
        ready = true;
        window.requestAnimationFrame?.(() => lens.classList.add('lens-ready'));
      }
    };
    const nearest = (x) => {
      const rs = rects();
      return rs.reduce(
        (best, rect, index) =>
          Math.abs(rect.left + rect.width / 2 - x) <
          Math.abs(rs[best].left + rs[best].width / 2 - x)
            ? index
            : best,
        0,
      );
    };
    const render = (index, x) => {
      lens.hidden = index < 0;
      buttons.forEach((node, i) => {
        node.classList.toggle("selected", i === index);
        if (i === index) node.setAttribute("aria-current", "page");
        else node.removeAttribute("aria-current");
      });
      setLens(index, x);
    };
    const commit = (index) => {
      selected = index;
      render(index);
      onSelect(index);
    };
    const cancel = () => {
      if (moveFrame !== null) window.cancelAnimationFrame?.(moveFrame);
      moveFrame = null;
      pointerId = null;
      origin = null;
      dragged = false;
      container.classList.remove("liquid-dragging");
      render(selected);
    };
    container.addEventListener("pointerdown", (event) => {
      if (container.classList.contains("bottom-nav") && window.innerWidth >= 1024) return;
      if (event.button !== undefined && event.button !== 0) return;
      measure();
      pointerId = event.pointerId;
      origin = { x: event.clientX, y: event.clientY };
      hovered = nearest(event.clientX);
      dragged = false;
      // A tap stays at the selected item. Only an intentional horizontal drag
      // captures the pointer and moves the glass; vertical scrolling is untouched.
    });
    container.addEventListener("pointermove", (event) => {
      if (event.pointerId !== pointerId || !origin) return;
      const dx = event.clientX - origin.x;
      const dy = event.clientY - origin.y;
      if (Math.abs(dy) > 12 && Math.abs(dy) > Math.abs(dx)) return cancel();
      if (!dragged && Math.abs(dx) <= 8) return;
      if (!dragged) {
        dragged = true;
        container.classList.add('liquid-dragging');
        container.setPointerCapture?.(pointerId);
      }
      hovered = nearest(event.clientX);
      latestX = event.clientX;
      if (moveFrame === null) {
        moveFrame = window.requestAnimationFrame?.(() => {
          moveFrame = null;
          setLens(hovered, latestX);
        }) ?? null;
      }
    });
    container.addEventListener("pointerup", (event) => {
      if (event.pointerId !== pointerId) return;
      if (!dragged) return cancel(); // Native click handles taps and keyboard activation.
      const next = nearest(event.clientX);
      if (moveFrame !== null) window.cancelAnimationFrame?.(moveFrame);
      moveFrame = null;
      pointerId = null;
      origin = null;
      dragged = false;
      container.classList.remove('liquid-dragging');
      suppressClickUntil = Date.now() + 300;
      commit(next);
      tg?.HapticFeedback?.selectionChanged?.();
    });
    container.addEventListener("pointercancel", cancel);
    cleanup.push(cancel);
    container.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return;
      event.preventDefault();
      commit(selected < 0 ? (event.key === 'ArrowRight' ? 0 : buttons.length-1)
        : (selected + (event.key === "ArrowRight" ? 1 : buttons.length - 1)) % buttons.length);
      buttons[selected].focus();
    });
    // Measure after insertion and whenever the control changes size. Disconnect
    // observers when a rerender removes the old control.
    if (window.ResizeObserver) {
      const observer = new window.ResizeObserver(() => { measure(); render(selected); });
      observer.observe(container);
      cleanup.push(() => observer.disconnect());
    }
    window.requestAnimationFrame?.(() => {
      if (container.isConnected) { measure(); render(selected); }
    });
    render(selected);
    return {
      activate: (index) => {
        if (Date.now() >= suppressClickUntil) commit(index);
      },
      commit,
      cancel,
      select: (index) => { selected = index; render(index); },
    };
  }
  function installPressOptics(root) {
    // Only interactive surfaces receive feedback, never a form/card parent.
    root.querySelectorAll?.('button:not(.nav-item), .lesson[role="button"]')
      .forEach((node) => {
        const clear = () => node.classList.remove('liquid-pressing');
        node.addEventListener('pointerdown', (event) => {
          if (node.disabled || (event.button !== undefined && event.button !== 0)) return;
          node.classList.add('liquid-pressing');
        });
        for (const event of ['pointerup', 'pointercancel', 'pointerleave', 'blur'])
          node.addEventListener(event, clear);
        liquidCleanups.push(clear);
      });
  }
  const small = (text) => el("span", { class: "muted" }, text),
    badge = (text, cls = "") => el("span", { class: "badge " + cls }, text);
  const mine = () => S.data.queues.filter((q) => q.my_position !== null),
    reference = (row) => ({ entry_id: row.id, revision: row.entry_revision });
  const isAdmin = () => S.mode === "admin" && S.data?.me.is_admin;
  let features;
  let toastTimer;
  function toast(text, error = false) {
    document.getElementById("toast")?.remove();
    clearTimeout(toastTimer);
    const node = el(
      "div",
      {
        id: "toast",
        class: "toast " + (error ? "error" : ""),
        role: error ? "alert" : "status",
      },
      text,
    );
    document.body.append(node);
    toastTimer = setTimeout(() => node.remove(), 6000);
    if (error) tg?.HapticFeedback?.notificationOccurred("error");
  }
  async function api(path, data) {
    const response = await fetch(
      "/api/miniapp" +
        path +
        (path.includes("?") ? "&" : "?") +
        "mode=" +
        S.mode,
      {
        method: data === undefined ? "GET" : "POST",
        headers: {
          ...authHeaders(),
          ...(data === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
        cache: "no-store",
        credentials: "same-origin",
      },
    );
    const result = await response.json().catch(() => ({
      error: "Сервер тимчасово недоступний. Спробуйте ще раз.",
    }));
    if (!response.ok) {
      if (response.status === 401 && !initData) {
        browserSession.authenticated = false;
        browserSession.csrf = null;
        S.data = null;
      }
      const error = new Error(result.error || "Не вдалося виконати запит.");
      error.status = response.status;
      error.code = result.code;
      throw error;
    }
    return result;
  }
  function updateBusy() {
    const refresh = document.querySelector('button[aria-label="Оновити"]');
    if (refresh) refresh.disabled = S.busy || S.loading;
  }
  async function openImage(path) {
    const epoch = S.epoch,
      mode = S.mode;
    const response = await fetch("/api/miniapp" + path + "?mode=" + S.mode, {
      headers: authHeaders(),
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw Object.assign(
        new Error(data.error || "Не вдалося відкрити зображення."),
        { status: response.status },
      );
    }
    const blob = await response.blob();
    if (epoch !== S.epoch || mode !== S.mode) return;
    const url = URL.createObjectURL(blob);
    const panel = sheet("Скріншот звернення", "");
    panel.append(
      el("img", {
        src: url,
        alt: "Скріншот, доданий до звернення",
        class: "report-image",
        onload: () => URL.revokeObjectURL(url),
        onerror: () => URL.revokeObjectURL(url),
      }),
    );
  }
  async function download(path, filename) {
    const response = await fetch(
      "/api/miniapp" +
        path +
        (path.includes("?") ? "&" : "?") +
        "mode=" +
        S.mode,
      {
        headers: authHeaders(),
        credentials: "same-origin",
        cache: "no-store",
      },
    );
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw Object.assign(
        new Error(data.error || "Не вдалося завантажити файл."),
        { status: response.status },
      );
    }
    const url = URL.createObjectURL(await response.blob());
    const link = el("a", { href: url, download: filename });
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  function avatarImage(kind) {
    return el("img", {
      src: S.avatars[kind],
      alt: "",
      class: "avatar-photo",
      decoding: "async",
      onerror: (event) => {
        event.currentTarget.remove();
        S.avatars[kind] = null;
      },
    });
  }
  function avatarFace(kind, letters) {
    return el(
      "span",
      { class: "avatar-face", "data-avatar-kind": kind },
      el("span", { class: "avatar-initials" }, letters),
      S.avatars[kind] ? avatarImage(kind) : null,
    );
  }
  async function loadAvatars() {
    if (
      !S.data?.me.is_registered ||
      S.avatarLoading ||
      typeof URL === "undefined" ||
      typeof URL.createObjectURL !== "function" ||
      Date.now() - S.avatarCheckedAt < 300000
    )
      return;
    S.avatarLoading = true;
    S.avatarCheckedAt = Date.now();
    try {
      await Promise.allSettled(
        ["bot", "me"].map(async (kind) => {
          const response = await fetch(
            "/api/miniapp/avatar/" + kind + "?mode=" + S.mode,
            {
              headers: authHeaders(),
              credentials: "same-origin",
              cache: "no-store",
            },
          );
          if (response.status !== 200) return;
          const blob = await response.blob();
          if (!blob.type.startsWith("image/") || S.fatal) return;
          const previous = S.avatars[kind];
          S.avatars[kind] = URL.createObjectURL(blob);
          // Patch only avatar faces; never rerender an in-progress student/admin form.
          document
            .querySelectorAll('[data-avatar-kind="' + kind + '"]')
            .forEach((face) => {
              face.querySelector("img")?.remove();
              face.append(avatarImage(kind));
            });
          if (previous) URL.revokeObjectURL(previous);
        }),
      );
    } finally {
      S.avatarLoading = false;
    }
  }
  function openFeature(key) {
    if (S.busy || !features) return;
    if (key === "polls" || key === "help") S.tab = key;
    return features.open(key);
  }
  const presentation = {theme:'system',accent:'lime',reduced:false};
  try {
    const stored = JSON.parse(window.localStorage?.getItem('pi231-appearance') || '{}');
    if(['light','dark','system'].includes(stored.theme)) presentation.theme=stored.theme;
    else { const old=window.localStorage?.getItem('pi231-theme'); if(['light','dark'].includes(old))presentation.theme=old; }
    if(['lime','blue','violet','teal','pink','orange'].includes(stored.accent))presentation.accent=stored.accent;
    presentation.reduced=stored.reduced===true;
  } catch (_) {}
  function appearanceSettings(){return {...presentation};}
  function setAppearance(change){
    if(['light','dark','system'].includes(change.theme))presentation.theme=change.theme;
    if(['lime','blue','violet','teal','pink','orange'].includes(change.accent))presentation.accent=change.accent;
    if(typeof change.reduced==='boolean')presentation.reduced=change.reduced;
    try {window.localStorage?.setItem('pi231-appearance',JSON.stringify(presentation));}catch(_){}
    theme();
  }
  function theme() {
    const value=presentation.theme==='system'?(tg?.colorScheme || (window.matchMedia?.('(prefers-color-scheme: dark)')?.matches?'dark':'light')):presentation.theme;
    const html=document.documentElement;
    html.dataset.theme=value; html.dataset.accent=presentation.accent;
    html.style.colorScheme=value; html.classList.toggle('reduce-motion',presentation.reduced);
    const color=value==='dark'?'#111410':'#f4f5f0';
    document.querySelector?.('meta[name="theme-color"]')?.setAttribute('content',color);
    try {tg?.setHeaderColor?.(color);tg?.setBackgroundColor?.(color);}catch(_){}
    return value;
  }
  function toggleTheme(){setAppearance({theme:document.documentElement.dataset.theme==='dark'?'light':'dark'});}
  async function load() {
    const epoch = ++S.epoch;
    S.loading = true;
    S.scheduleLoading = false;
    render();
    try {
      const data = await api("/bootstrap");
      if (epoch !== S.epoch) return;
      S.data = data;
      if (!data.me.is_admin) {
        S.mode = "student";
        if (S.tab === "admin") S.tab = "queues";
      }
      const sid = S.queue?.id;
      if (sid) {
        const detail = await api("/queues/" + sid);
        if (epoch !== S.epoch) return;
        S.queue = detail;
      }
      if (S.tab === "admin" && !S.queue) {
        const result = await api("/admin/users");
        if (epoch !== S.epoch) return;
        S.users = result.users;
      }
      S.fatal = null;
    } catch (error) {
      if (epoch === S.epoch) {
        S.fatal = error.message;
        S.queue = null;
        if (error.status === 403 && S.mode === "admin") S.mode = "student";
      }
    } finally {
      if (epoch === S.epoch) {
        S.loading = false;
        render();
        if (!S.fatal) void loadAvatars();
      }
    }
  }
  async function openQueue(id) {
    if (S.busy || S.loading) return;
    const epoch = ++S.epoch;
    S.loading = true;
    render();
    try {
      const data = await api("/queues/" + id);
      if (epoch === S.epoch) {
        S.queue = data;
        window.scrollTo(0, 0);
      }
    } catch (error) {
      toast(error.message, true);
    } finally {
      if (epoch === S.epoch) {
        S.loading = false;
        render();
      }
    }
  }
  async function mutate(path, data) {
    if (S.busy) return;
    S.busy = true;
    document
      .querySelectorAll("button,input,select")
      .forEach((n) => (n.disabled = true));
    try {
      const result = await api(path, data);
      closeSheet();
      toast(result.message || "Збережено");
      tg?.HapticFeedback?.notificationOccurred("success");
      await load();
    } catch (error) {
      toast(error.message, true);
      if (error.status === 401 || error.status === 403) {
        closeSheet();
        await load();
      }
    } finally {
      S.busy = false;
      document
        .querySelectorAll("button,input,select")
        .forEach((n) => (n.disabled = false));
    }
  }
  function navigate(tab) {
    if (tab === "polls" || tab === "help") {
      openFeature(tab);
      return;
    }
    if (S.busy || (S.loading && !S.scheduleLoading)) return;
    S.tab = tab;
    S.feature = null;
    S.queue = null;
    S.fatal = null;
    window.scrollTo(0, 0);
    if (tab === "schedule") loadSchedule();
    else load();
  }
  function topbar() {
    if(design) return design.topbar();
    const tools = el("div", { class: "top-tools" });
    const themeToggle = btn(icon(document.documentElement?.dataset.theme === 'light' ? 'moon' : 'sun'), toggleTheme, 'icon-button theme-toggle');
    themeToggle.setAttribute('aria-label', document.documentElement?.dataset.theme === 'light' ? 'Увімкнути темну тему' : 'Увімкнути світлу тему');
    themeToggle.setAttribute('aria-pressed', String(document.documentElement?.dataset.theme === 'light'));
    tools.append(themeToggle);
    if (S.data?.me.is_admin)
      tools.append(
        btn(
          isAdmin() ? "До студента" : "До адміна",
          () => {
            if (S.busy || S.loading) return;
            S.mode = isAdmin() ? "student" : "admin";
            S.queue = null;
            S.feature = null;
            S.tab = isAdmin() ? "admin" : "home";
            load();
          },
          "mode " + (isAdmin() ? "admin" : ""),
        ),
      );
    const refresh = btn(
      icon("refresh"),
      () =>
        S.feature
          ? features.open(S.feature)
          : S.tab === "schedule"
            ? refreshSchedule()
            : load(),
      "icon-button",
    );
    refresh.setAttribute("aria-label", "Оновити");
    refresh.disabled = S.loading || S.busy;
    tools.append(refresh);
    if (S.data?.me.name) {
      const avatar = btn(
        avatarFace(
          "me",
          S.data.me.name
            .split(" ")
            .filter(Boolean)
            .map((n) => n[0])
            .slice(0, 2)
            .join(""),
        ),
        () => navigate("profile"),
        "avatar header-avatar",
      );
      avatar.setAttribute("aria-label", "Відкрити профіль");
      tools.append(avatar);
    }
    return el(
      "header",
      { class: "topbar" },
      el(
        "div",
        { class: "brand" },
        el("span", { class: "brand-logo" }, avatarFace("bot", "ПІ")),
        el(
          "span",
          {},
          "ПІ-231",
          el("small", {}, S.data ? "бот на зв’язку" : "твій простір"),
        ),
      ),
      tools,
    );
  }
  let navCache = null;
  function bottomnav() {
    if(design){const node=design.navigation();if(navCache?.node!==node)navCache?.node.remove?.();navCache={node};return node;}
    const tabs = [
      ["home", "Головна"],
      ["queues", "Черги"],
      ["schedule", "Розклад"],
      ["more", "Ще"],
    ];
    const activeTab = S.queue ? 'queues' : (S.feature || ['profile', 'admin', 'polls', 'help'].includes(S.tab)) ? 'more' : S.tab;
    if (navCache) {
      navCache.buttons.forEach((node, i) => node.classList.toggle('active', tabs[i][0] === activeTab));
      navCache.control.select(Math.max(0, tabs.findIndex(([key]) => key === activeTab)));
      return navCache.node;
    }
    const buttons = tabs.map(([key, label]) => {
      const node = btn(
        [icon(key), el("span", {}, label)],
        () => control?.activate(tabs.findIndex(([tab]) => tab === key)),
        "nav-item " + (activeTab === key ? "active" : ""),
      );
      if (activeTab === key) node.setAttribute("aria-current", "page");
      return node;
    });
    let control;
    const nav = el(
      "nav",
      { class: "bottom-nav", "aria-label": "Основні розділи" },
      buttons,
    );
    control = attachLiquidLens(nav, {
      buttons,
      selectedIndex: Math.max(0, tabs.findIndex(([key]) => key === activeTab)),
      persistent: true,
      onSelect: (index) => navigate(tabs[index][0]),
    });
    navCache = {node: nav, buttons, control};
    return nav;
  }
  function empty(title, text) {
    return el(
      "div",
      { class: "empty" },
      el("div", { class: "empty-icon" }, icon("queues")),
      el("h3", {}, title),
      el("p", {}, text),
    );
  }
  function sectionTitle(title, meta) {
    return el(
      "div",
      { class: "section-heading" },
      el("h2", {}, title),
      small(meta),
    );
  }
  function queueCard(q) {
    return btn(
      [
        el(
          "div",
          { class: "position-tile" },
          el("strong", {}, q.my_position),
          el("span", {}, "у черзі"),
        ),
        el(
          "div",
          { class: "queue-copy" },
          el(
            "span",
            { class: "status " + (q.status === "open" ? "open" : "") },
            q.status === "open"
              ? q.my_position === 1
                ? "Твоя черга"
                : "Запис відкрито"
              : "Запис закрито",
          ),
          el("h3", {}, q.subject),
          el(
            "p",
            { class: "teacher" },
            consultation(q)
              ? "Консультація"
              : labText({
                  labs: q.my_labs,
                  planned_minutes: q.my_planned_minutes,
                }) || q.teacher,
          ),
          small(q.count + " у черзі"),
        ),
        icon("arrow"),
      ],
      () => openQueue(q.id),
      "queue-card record-card",
    );
  }
  const consultation = (q) =>
    q.type === "consultation" || q.subject.includes("Курсова");
  function subjectCard(q) {
    const joined = q.my_position !== null;
    return btn(
      [
        el(
          "span",
          { class: "subject-icon tone-" + (q.id % 3) },
          window.PI231SubjectIcon
            ? window.PI231SubjectIcon(q.subject)
            : icon(consultation(q) ? "profile" : "queues"),
        ),
        el(
          "div",
          { class: "subject-copy" },
          el("h3", {}, q.subject),
          el("p", {}, q.teacher),
          small(
            (consultation(q) ? "Консультація · " : "") + q.count + " у черзі",
          ),
        ),
        el(
          "span",
          { class: "join-label " + (joined ? "joined" : "") },
          isAdmin()
            ? "Керувати"
            : joined
              ? "Додано ✓"
              : q.status === "open"
                ? "+ Стати"
                : "Закрито",
        ),
      ],
      () => openQueue(q.id),
      "subject-row",
    );
  }
  function homePage() {
    if(design) return design.homePage();
    const own = mine(), primary = own[0], lesson = S.data.next_lesson;
    const rooms = lesson?.lessons.flatMap(item => item.teachers_raw?.map(t => t.room).filter(Boolean) || [item.room].filter(Boolean)) || [];
    const subjects = [...new Set(lesson?.lessons.map(item => item.subject) || [])];
    const lessonDate = lesson ? new Date(lesson.date + 'T12:00:00Z').toLocaleDateString('uk-UA', {day:'numeric',month:'long',timeZone:'Europe/Kyiv'}) : '';
    return el('main', {class:'home-page'},
      el('section', {class:'welcome'},
        el('p',{class:'eyebrow'},new Date(S.data.today+'T12:00:00Z').toLocaleDateString('uk-UA',{weekday:'long',day:'numeric',month:'long',timeZone:'Europe/Kyiv'})),
        el('h1',{},'Привіт, '+ S.data.me.name.split(' ').slice(0,2).join(' ')),
        el('p',{},'Твій навчальний день — під контролем.'),
      ),
      btn([
        el('span',{class:'eyebrow hero-eyebrow'},icon('schedule'),lesson?.ongoing?'ЗАРАЗ ТРИВАЄ':'НАЙБЛИЖЧА ПАРА'),
        el('strong',{}, lesson ? subjects.join(' / ') : 'Переглянути розклад'),
        small(lesson ? [lesson.date !== S.data.today ? lessonDate : null, lesson.startTime+'–'+lesson.endTime, rooms.length ? 'ауд. '+[...new Set(rooms)].join(' / ') : null].filter(Boolean).join(' · ') : 'Пари, заміни та твій навчальний тиждень'),
      ],()=>{S.scheduleDate=lesson?.date || S.data.today; navigate('schedule');},'card upcoming-card'),
      primary ? btn([
        el('div',{class:'hero-top'},el('span',{class:'eyebrow hero-eyebrow'},icon('queues'),'ТВІЙ ЗАПИС'),el('span',{class:'status '+primary.status},primary.status==='open'?'Запис відкрито':'Запис закрито')),
        el('h2',{},primary.subject),
        el('div',{class:'hero-position'},el('strong',{},primary.my_position),el('div',{},small('місце в черзі'),el('p',{},consultation(primary)?'Консультація':labText({labs:primary.my_labs,planned_minutes:primary.my_planned_minutes}) || primary.teacher))),
        el('span',{class:'hero-open'},icon('arrow'),'Відкрити чергу'),
      ],()=>openQueue(primary.id),'card home-queue-hero') : el('section',{class:'card home-empty'},el('h2',{},'Займи своє місце'),small('Обери предмет і запишись, коли готовий до здачі.'),btn('Знайти чергу',()=>navigate('queues'),'button primary')),
      own.length>1 ? sectionTitle('Інші записи',String(own.length-1)) : null,
      own.length>1 ? el('div',{class:'compact-records'},own.slice(1).map(q=>btn([
        el('span',{class:'subject-icon'},window.PI231SubjectIcon?window.PI231SubjectIcon(q.subject):icon('queues')),
        el('div',{class:'subject-copy'},el('strong',{},q.subject),small(consultation(q)?'Консультація':labText({labs:q.my_labs,planned_minutes:q.my_planned_minutes}) || q.teacher)),
        el('strong',{class:'compact-position'},'#'+q.my_position),icon('arrow'),
      ],()=>openQueue(q.id),'compact-record'))) : null,
    );
  }
  function morePage() {
    const links=[['profile','profile','Профіль і мої обміни','Акаунт, записи та запити на обмін'],['polls','polls','Опитування','Голосування та результати'],['help','help','Допомога','Як працюють черги'],['bugs','bugs','Звернення','Повідомити про проблему']];
    if(S.data.me.is_admin) links.unshift(['admin','admin','Керування групою','Черги, студенти, заміни та звіти']);
    return el('main',{},el('div',{class:'page-title'},el('h1',{},'Ще'),small('Усе інше — в одному місці.')),
      el('div',{class:'more-list'},links.map(([key,glyph,title,description])=>btn([icon(glyph),el('span',{},el('strong',{},title),small(description)),icon('arrow')],()=>{
        if(key==='admin'){ S.mode='admin'; navigate('admin'); }
        else if(key==='profile') navigate('profile'); else openFeature(key);
      },'more-row'))),
      installCard(),
    );
  }
  function installCard() {
    return el('section',{class:'card install-card'},el('h2',{},'Окремий застосунок'),
      small(initData ? 'Встанови PWA із Safari або Chrome: вона відкриватиметься окремим вікном.' : 'Встанови на телефон або ПК та відкривай без пошуку вкладки.'),
      btn(initData?'Відкрити вебверсію / PWA':'Встановити застосунок',async()=>toast(await window.PI231PWA?.install() || 'Відкрий меню браузера для встановлення.'),'button secondary'),
      el('p',{class:'muted'},'Telegram потрібен лише для підтвердження входу.'),
    );
  }
  function queuesPage() {
    if(design) return design.queuesPage();
    const qs = S.queueFilter === 'mine' ? mine() : S.data.queues,
      own = mine();
    return el(
      "main",
      {},
      el(
        "div",
        { class: "page-title" },
        el('h1',{},'Черги'),
        small('Обери предмет і своє місце.'),
      ),
      el('div',{class:'segments queue-filters'},
        el('button',{type:'button',class:S.queueFilter==='all'?'selected':'','aria-pressed':String(S.queueFilter==='all'),onclick:()=>{S.queueFilter='all';render();}},icon('queues'),'Усі'),
        el('button',{type:'button',class:S.queueFilter==='mine'?'selected':'','aria-pressed':String(S.queueFilter==='mine'),onclick:()=>{S.queueFilter='mine';render();}},icon('profile'),'Мої · '+own.length),
      ),
      btn(
        [icon("plus"), "Відкрити чергу"],
        () => openFeature("create"),
        "button secondary create-queue",
      ),
      sectionTitle("Доступні предмети", "Предметів: " + qs.length),
      qs.length
        ? el("div", { class: "subject-list" }, qs.map(subjectCard))
        : empty("Черги ще не відкриті", "Потрібні предмети з’являться тут."),
      el(
        "p",
        { class: "footnote" },
        "Ті самі черги, що й у боті. Обирай, як зручніше сьогодні.",
      ),
    );
  }
  function labText(row) {
    const numbers = row.labs?.length
      ? "ЛР " + row.labs.map((n) => "№" + n).join(", ")
      : row.lab_count
        ? row.lab_count + " лаб."
        : "";
    return [
      numbers,
      row.planned_minutes ? "≈ " + row.planned_minutes + " хв" : "",
    ]
      .filter(Boolean)
      .join(" · ");
  }
  function queuePage() {
    const q = S.queue,
      own = q.entries.find((e) => e.is_me),
      back = btn(
        [icon("back"), "До предметів"],
        () => {
          S.queue = null;
          load();
        },
        "back-button",
      ),
      controls = el("div", { class: "queue-tools" }),
      otherActions = el('div',{class:'action-stack'}),
      dock = el('div',{class:'queue-dock'});
    if (own) {
      if (!q.lesson || q.lesson.own?.state === 'serving') dock.append(
        btn(
          [icon('check'), q.labs.length
            ? "Здав" + (own.labs?.length > 1 ? " лабораторні" : "")
            : "Завершити запис"],
          () => {
            if (q.labs.length && own.labs?.length > 1) completionForm(q, own);
            else mutate("/queues/" + q.id + "/complete", reference(own));
          },
          "button primary",
        ),
      );
      if (q.labs.length)
        controls.append(
          btn([icon('code-slash'),"Лабораторні та час"], () => labForm(q, own), "button secondary"),
        );
      if (!consultation(q) && (!q.lesson || q.lesson.own?.state === 'serving'))
        otherActions.append(
          btn(
            [icon('refresh'),"Не здав — у кінець"],
            () =>
              confirmSheet(
                "Перейти в кінець черги?",
                "Вибрані лабораторні збережуться.",
                () => mutate("/queues/" + q.id + "/not-passed", reference(own)),
              ),
            "button secondary",
          ),
        );
      otherActions.append(
        el(
          "div",
          { class: "button-row" },
          btn(
            [icon('up'),own.swap_wish === "higher" ? "Зняти «Хочу вище»" : "Хочу вище ↑"],
            () =>
              mutate("/queues/" + q.id + "/wish", {
                ...reference(own),
                wish: own.swap_wish === "higher" ? null : "higher",
              }),
            "button secondary",
          ),
          btn(
            [icon('down'),own.swap_wish === "lower" ? "Зняти «Хочу нижче»" : "Хочу нижче ↓"],
            () =>
              mutate("/queues/" + q.id + "/wish", {
                ...reference(own),
                wish: own.swap_wish === "lower" ? null : "lower",
              }),
            "button secondary",
          ),
        ),
      );
      controls.append(btn([icon('swap'),'Обмін місцями'],()=>{
        const panel=sheet('Обмін місцями','Натисни на учасника у списку, щоб запропонувати обмін. На відповідь є 10 хвилин.');
        panel.append(btn('Обрати учасника',()=>{closeSheet();document.querySelector('.entry-list')?.scrollIntoView({behavior:'smooth',block:'start'});},'button primary'));
      },'button secondary'));
      dock.append(
        btn(
          [icon('close'),"Вийти з черги"],
          () =>
            confirmSheet(
              "Вийти з черги?",
              "Поточне місце буде звільнено.",
              () => mutate("/queues/" + q.id + "/leave", reference(own)),
            ),
          "text-button danger",
        ),
      );
    } else if (q.status === "open")
      dock.append(btn([icon('plus'),"Стати в чергу"], () => labForm(q), "button primary"));
    else
      controls.append(
        el(
          "p",
          { class: "notice" },
          "Запис закрито. Уже записані студенти можуть завершувати здачу.",
        ),
      );
    if (q.lesson) {
      const ownState = q.lesson.own;
      const summary = el('section', {class:'card action-stack'},
        el('strong',{},'Здача на парі активна'),
        small('На підтвердження присутності та виклику — 10 хвилин.'),
        small('До '+new Date(q.lesson.ends_at*1000).toLocaleTimeString('uk-UA',{hour:'2-digit',minute:'2-digit'})),
        q.lesson.current_name ? small('Викликано / зараз здає: '+q.lesson.current_name) : small('Очікуємо готових студентів.'),
        ownState ? small(q.lesson.states[String(own.id)] || '') : null,
        q.lesson.people_ahead != null ? small('Людей попереду: '+q.lesson.people_ahead) : null);
      if (ownState) {
        const answer = (response, minutes) => mutate('/queues/'+q.id+'/attendance', {token:ownState.token,response,minutes});
        if (ownState.state === 'called') summary.append(
          btn('Готовий здавати',()=>answer('ready'),'button primary'),
          btn('Пропустити мене',()=>answer('skip'),'button secondary'));
        else if (ownState.state !== 'serving') summary.append(
          btn('Я вже на місці',()=>answer('present'),'button primary'),
          btn('Запізнююсь',()=>{
            const panel=sheet('Запізнююсь','Через скільки хвилин будете? Місце зберігається, інші поки можуть здавати.');
            const minutes=el('input',{type:'number',min:1,max:240,step:1,value:10,required:true,'aria-label':'Хвилин до приходу'});
            const form=el('form',{},minutes,el('button',{type:'submit',class:'button primary'},'Підтвердити запізнення'));
            form.addEventListener('submit',event=>{event.preventDefault();answer('late',Number(minutes.value));});panel.append(form);
          },'button secondary'),
          btn('На цій парі не буду',()=>answer('absent'),'button secondary'));
      }
      controls.append(summary);
    }
    if (own && q.labs.length && (!q.lesson || q.lesson.own?.state === 'serving'))
      otherActions.append(btn('Завершити підхід з рештою робіт',()=>confirmSheet(
        'Завершити підхід?', 'Решта робіт збережеться, а ви перейдете в кінець черги.',
        ()=>mutate('/queues/'+q.id+'/finish-turn',reference(own))),'button secondary'));
    if (isAdmin()) {
      if (q.lesson) controls.append(btn('Зупинити здачу на парі',()=>mutate('/admin/queues/'+q.id,{action:'stop-lesson'}),'button secondary'));
      else controls.append(btn('Почати здачу на парі',()=>{
        const panel=sheet('Почати здачу','Студенти отримають запит присутності. На обидві відповіді — 10 хвилин. Оберіть тривалість пари; після неї виклики зупиняться.');
        [40,80,90].forEach(minutes=>panel.append(btn(minutes+' хв',()=>mutate('/admin/queues/'+q.id,{action:'start-lesson',minutes}),'button secondary')));
      },'button primary'));
    }
    if (isAdmin())
      controls.append(
        btn(
          q.status === "open" ? "Закрити запис" : "Відкрити запис",
          () =>
            mutate("/admin/queues/" + q.id, {
              action: q.status === "open" ? "close" : "open",
            }),
          "button secondary",
        ),
      );
    if (isAdmin())
      controls.append(
        btn("Додати студентів", () => addStudentsForm(q), "button secondary"),
        btn(
          "Історія та завершення черги",
          () => features.open("admin-history", false, "?sid=" + q.id),
          "button secondary",
        ),
      );
    const entries = q.entries.map((row) => {
      const action = isAdmin()
        ? () => adminEntryForm(q, row)
        : own && !row.is_me && !row.vacant
          ? () =>
              confirmSheet(
                "Запропонувати обмін?",
                row.name + ". На відповідь є 10 хвилин.",
                () =>
                  mutate("/queues/" + q.id + "/swap", {
                    ...reference(own),
                    target_entry_id: row.id,
                    target_revision: row.entry_revision,
                  }),
              )
          : null;
      return el(
        action ? "button" : "div",
        {
          class: "entry " + (row.is_me ? "self" : ""),
          ...(action ? { type: "button", onclick: action } : {}),
        },
        el(
          "span",
          { class: "position" },
          row.position,
        ),
        el('span',{class:'entry-avatar','aria-hidden':true},row.vacant ? '+' : row.name.split(' ').filter(Boolean).map(n=>n[0]).slice(0,2).join('')),
        el(
          "div",
          { class: "entry-info" },
          el(
            "strong",
            {},
            row.name,
            row.is_me ? el("span", { class: "you" }, "ти") : null,
          ),
          q.lesson?.states[String(row.id)] ? small(q.lesson.states[String(row.id)]) : null,
          row.swap_wish
            ? badge(row.swap_wish === "higher" ? "Хоче вище ↑" : "Хоче нижче ↓")
            : null,
          q.labs.length
            ? el("small", {}, labText(row) || "Лабораторні не вказані")
            : null,
          isAdmin() && row.telegram_id > 0
            ? el("small", { class: "id-label" }, "ID: " + row.telegram_id)
            : null,
        ),
        action ? icon(isAdmin() ? "admin" : "arrow") : null,
      );
    });
    if(design) return design.queuePage(q,own,entries,controls,dock,otherActions);
    return el(
      "main",
      {class:'queue-detail'+(isAdmin()?' admin-detail':'')},
      back,
      el(
        "div",
        { class: "detail-title" },
        el('span',{class:'status '+q.status},q.status==='open'?'Запис відкрито':'Запис закрито'),
        el("h1", {}, q.subject),
        el("p", { class: "muted" }, q.teacher),
      ),
      own
        ? el(
            "div",
            { class: "own-banner" },
            el(
              "div",
              {},
              el("strong", {}, "#" + own.position),
            ),
            el(
              "div",
              {},
              el("b", {}, own.position === 1 ? "Твоя черга" : "Твоє місце"),
              el(
                "p",
                {},
                consultation(q) ? 'Консультація' : labText(own) || 'Лабораторні не вказані',
              ),
            ),
          )
        : null,
      el(
        "div",
        { class: "section-heading" },
        el("h2", {}, "Учасники"),
        badge(q.count),
      ),
      el("section", { class: "entry-list" }, entries),
      !entries.length
        ? empty(
            "Ти можеш бути першим",
            "Запишися, коли будеш готовий до здачі.",
          )
        : null,
      controls,
      own ? el('div',{class:'queue-secondary-actions'},otherActions) : null,
      dock,
      own && !isAdmin() && q.count > 1
        ? el(
            "p",
            { class: "footnote" },
            "Натисни на іншого учасника, щоб запропонувати обмін місцями.",
          )
        : null,
    );
  }
  function closeSheet() {
    const panel=document.getElementById("sheet");
    panel?.querySelectorAll?.('video')?.forEach(video=>video.pause());
    panel?.remove();
  }
  function sheet(title, description) {
    closeSheet();
    const overlay = el("div", { id: "sheet", class: "sheet-overlay" }),
      panel = el(
        "section",
        {
          class: "sheet",
          role: "dialog",
          "aria-modal": "true",
          "aria-label": title,
        },
        el("div", { class: "sheet-handle" }),
      ),
      close = btn(
        icon("close"),
        () => {
          if (!S.busy) closeSheet();
        },
        "icon-button",
      );
    close.setAttribute("aria-label", "Закрити");
    panel.append(
      el("div", { class: "section-heading" }, el("h2", {}, title), close),
      el("p", { class: "muted" }, description),
    );
    overlay.append(panel);
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay && !S.busy) closeSheet();
    });
    document.body.append(overlay);
    requestAnimationFrame(() => panel.querySelector("input,button")?.focus());
    return panel;
  }
  function confirmSheet(title, description, fn) {
    const panel = sheet(title, description);
    panel.append(
      btn("Підтвердити", fn, "button primary"),
      btn("Скасувати", closeSheet, "button secondary"),
    );
  }
  function choices(labs, selected = []) {
    return el(
      "div",
      { class: "lab-grid" },
      labs.map((lab) =>
        el(
          "label",
          { class: "lab-choice" },
          el("input", {
            type: "checkbox",
            value: lab.number,
            checked: selected.includes(lab.number),
          }),
          el(
            "span",
            {},
            el("b", {}, "№" + lab.number),
            el("small", {}, lab.title || "Лабораторна"),
            el("em", {}, "до " + lab.max_minutes + " хв"),
          ),
        ),
      ),
    );
  }
  function selectedIn(node) {
    return [...node.querySelectorAll("input[type=checkbox]:checked")].map((n) =>
      Number(n.value),
    );
  }
  function labForm(q, own) {
    if(design && !own) return design.joinWizard(q);
    const panel = sheet(
        own ? "Твої лабораторні" : "Запис на здачу",
        q.labs.length
          ? "Обери роботи й зазнач приблизний час. Місце займеться після підтвердження."
          : "Це запис без лабораторних та часових лімітів.",
      ),
      form = el("form");
    let grid, minutes, cap;
    if (q.labs.length) {
      grid = choices(
        q.labs.filter((lab) => !own?.passed_labs?.includes(lab.number)),
        own?.labs || [],
      );
      form.append(grid);
      minutes = el("input", {
        type: "number",
        min: 5,
        step: 1,
        required: true,
        inputmode: "numeric",
        value: own?.planned_minutes || 10,
        "aria-label": "Час здачі у хвилинах",
      });
      cap = el("p", { class: "notice" });
      const update = () => {
        const selected = selectedIn(grid),
          maximum = q.labs
            .filter((l) => selected.includes(l.number))
            .reduce((sum, l) => sum + l.max_minutes, 0);
        cap.textContent = selected.length
          ? "Можна вказати від 5 до " + maximum + " хв."
          : "Обери щонайменше одну лабораторну.";
        minutes.max = maximum;
      };
      grid.addEventListener("change", update);
      update();
      form.append(
        el("label", { class: "field" }, "Твій час здачі, хв", minutes),
        cap,
      );
    }
    let place;
    if (!own && q.can_pick_position) {
      place = el("input", {
        type: "number",
        min: 1,
        step: 1,
        max: 1000,
        inputmode: "numeric",
        placeholder: "У кінець черги",
      });
      form.append(
        el("label", { class: "field" }, "Місце (необов’язково)", place),
      );
    }
    form.append(
      el(
        "button",
        { type: "submit", class: "button primary" },
        own ? "Зберегти зміни" : "Підтвердити запис",
      ),
    );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      const labs = grid ? selectedIn(grid) : [];
      if (q.labs.length && !labs.length) {
        toast("Обери хоча б одну лабораторну.", true);
        return;
      }
      mutate("/queues/" + q.id + (own ? "/edit" : "/join"), {
        ...(own ? reference(own) : {}),
        ...(q.labs.length ? { labs, minutes: Number(minutes.value) } : {}),
        ...(!own && place?.value ? { position: Number(place.value) } : {}),
      });
    });
    panel.append(form);
  }
  function completionForm(q, own) {
    const panel = sheet(
        "Що вдалося здати?",
        "Познач фактично здані роботи. З рештою ти залишишся на своєму місці.",
      ),
      grid = choices(q.labs.filter((l) => own.labs.includes(l.number)));
    panel.append(
      grid,
      btn(
        "Здав усі вибрані раніше",
        () =>
          mutate("/queues/" + q.id + "/complete", {
            ...reference(own),
            labs: own.labs,
          }),
        "button secondary",
      ),
      btn(
        "Підтвердити здачу",
        () => {
          const labs = selectedIn(grid);
          if (!labs.length) {
            toast("Познач здані лабораторні.", true);
            return;
          }
          mutate("/queues/" + q.id + "/complete", { ...reference(own), labs });
        },
        "button primary",
      ),
    );
  }
  async function loadSchedule(value) {
    if (S.busy) return;
    const epoch = ++S.epoch;
    S.scheduleView = "day";
    S.scheduleDate = value || S.scheduleDate || S.data.today;
    S.scheduleLoading = true;
    S.scheduleError = null;
    S.loading = true;
    render();
    try {
      const data = await api(
        "/schedule?date=" + encodeURIComponent(S.scheduleDate),
      );
      if (epoch === S.epoch) {
        S.schedule = data;
        S.fatal = null;
      }
    } catch (error) {
      if (epoch === S.epoch) {
        if (error.status === 401 || error.status === 403)
          S.fatal = error.message;
        else S.scheduleError = error.message;
      }
    } finally {
      if (epoch === S.epoch) {
        S.scheduleLoading = false;
        S.loading = false;
        render();
      }
    }
  }
  function refreshSchedule() {
    return S.scheduleView === "week"
      ? loadWeek(S.scheduleDate)
      : loadSchedule();
  }
  async function loadWeek(value) {
    if (S.busy) return;
    const epoch = ++S.epoch;
    const monday = new Date(
      (value || S.scheduleDate || S.data.today) + "T12:00:00Z",
    );
    monday.setUTCDate(monday.getUTCDate() - ((monday.getUTCDay() + 6) % 7));
    S.scheduleView = "week";
    S.scheduleDate = value || S.scheduleDate || S.data.today;
    S.scheduleLoading = true;
    S.loading = true;
    S.scheduleError = null;
    render();
    try {
      const days = await Promise.all(
        Array.from({ length: 7 }, (_, index) => {
          const date = new Date(monday);
          date.setUTCDate(date.getUTCDate() + index);
          return api("/schedule?date=" + date.toISOString().slice(0, 10));
        }),
      );
      if (epoch === S.epoch) {
        S.weekSchedule = days;
        S.fatal = null;
      }
    } catch (error) {
      if (epoch === S.epoch) {
        if (error.status === 401 || error.status === 403)
          S.fatal = error.message;
        else S.scheduleError = error.message;
      }
    } finally {
      if (epoch === S.epoch) {
        S.scheduleLoading = false;
        S.loading = false;
        render();
      }
    }
  }
  function lessonCard(slot) {
    if(design) return design.lessonCard(slot);
    const lessons = slot.lessons || [slot];
    return el(
      "article",
      { class: "lesson" + (lessons.length ? "" : " lesson-empty") },
      el(
        "div",
        { class: "lesson-number" },
        slot.number,
        el("small", {}, "пара"),
      ),
      el(
        "div",
        { class: "lesson-body" },
        slot.startTime && slot.endTime
          ? el(
              "small",
              { class: "lesson-time" },
              slot.startTime + "–" + slot.endTime,
            )
          : null,
        lessons.length
          ? lessons.map((lesson) =>
              el(
                "div",
                { class: "lesson-content" },
                lesson.is_replacement ? badge("Заміна", "warning") : null,
                el("h3", {}, lesson.subject),
                lesson.teachers_raw?.length
                  ? lesson.teachers_raw.map((t) =>
                      el(
                        "p",
                        { class: "muted" },
                        t.name + (t.room ? " · ауд. " + t.room : ""),
                      ),
                    )
                  : el(
                      "p",
                      { class: "muted" },
                      [lesson.teacher, lesson.room && "ауд. " + lesson.room]
                        .filter(Boolean)
                        .join(" · "),
                    ),
              ),
            )
          : el("p", { class: "muted empty-lesson-label" }, "Заняття немає"),
      ),
    );
  }
  function dayTimeline(day) {
    // Slots come from the server's bell schedule; actual lessons stay separate.
    return el(
      "div",
      { class: "timeline" },
      (day.slots || day.lessons).map(lessonCard),
    );
  }
  function weekView() {
    return el(
      "div",
      { class: "week-schedule" },
      (S.weekSchedule || []).map((day) =>
        el(
          "section",
          {},
          sectionTitle(
            day.day,
            new Date(day.date + "T12:00:00Z").toLocaleDateString("uk-UA", {
              day: "numeric",
              month: "long",
              timeZone: "Europe/Kyiv",
            }),
          ),
          day.source_day
            ? el(
                "p",
                { class: "notice" },
                "За розкладом: " +
                  day.source_day +
                  (day.working_day_archived ? " · Завершено" : ""),
              )
            : null,
          !day.lessons.length
            ? el("p", { class: "notice" }, "Занять немає.")
            : null,
          dayTimeline(day),
        ),
      ),
    );
  }
  function scheduleState() {
    if (S.scheduleLoading)
      return el(
        "div",
        {
          class: "schedule-loading",
          role: "status",
          "aria-label": "Завантаження розкладу",
        },
        [1, 2, 3, 4, 5].map(() =>
          el(
            "div",
            { class: "lesson-skeleton" },
            el("i"),
            el("span", {}, el("b"), el("b"), el("b")),
          ),
        ),
      );
    return el(
      "div",
      { class: "schedule-state", role: "alert" },
      empty("Не вдалося завантажити розклад", S.scheduleError),
      btn("Спробувати знову", () => loadSchedule(), "button primary"),
    );
  }
  function tomorrow() {
    const next = new Date(S.data.today + "T12:00:00Z");
    next.setUTCDate(next.getUTCDate() + 1);
    return next.toISOString().slice(0, 10);
  }
  function schedulePage() {
    const data =
        S.scheduleView === "day" &&
        S.schedule?.date === S.scheduleDate &&
        !S.scheduleLoading &&
        !S.scheduleError
          ? S.schedule
          : null,
      input = el("input", {
        type: "date",
        value: S.scheduleDate || S.data.today,
        onchange: (event) => loadSchedule(event.target.value),
        "aria-label": "Дата розкладу",
      });
    const segmentChoices = [
      {
        label: "Сьогодні",
        selected: S.scheduleView === "day" && S.scheduleDate === S.data.today,
        select: () => loadSchedule(S.data.today),
      },
      {
        label: "Завтра",
        selected: S.scheduleView === "day" && S.scheduleDate === tomorrow(),
        select: () => loadSchedule(tomorrow()),
      },
      {
        label: "Тиждень",
        selected: S.scheduleView === "week",
        select: () => loadWeek(),
      },
    ];
    let segmentControl;
    const segmentButtons = segmentChoices.map((choice, index) =>
      btn(
        choice.label,
        () => segmentControl?.activate(index),
        choice.selected ? "selected" : "",
      ),
    );
    const segmentContainer = el("div", { class: "segments" }, segmentButtons);
    segmentControl = attachLiquidLens(segmentContainer, {
      buttons: segmentButtons,
      selectedIndex: segmentChoices.findIndex((choice) => choice.selected),
      onSelect: (index) => segmentChoices[index].select(),
    });
    return el(
      "main",
      {},
      el(
        "div",
        { class: "page-title" },
        small("ТВІЙ НАВЧАЛЬНИЙ ДЕНЬ"),
        el("h1", {}, "Розклад"),
      ),
      el("label", { class: "date-control" }, icon("schedule"), input),
      segmentContainer,
      data?.source_day
        ? el(
            "p",
            { class: "notice" },
            (data.working_day_archived ? "Архівна робоча субота за розкладом: " : "Навчальний день за розкладом: ") +
              data.source_day +
              (data.working_day_archived ? " · Завершено" : ""),
          )
        : null,
      data
        ? el(
            "div",
            { class: "section-heading" },
            el("h2", {}, data.day),
            badge(data.week_type === "denominator" ? "Знаменник" : "Чисельник"),
          )
        : null,
      S.scheduleLoading || S.scheduleError
        ? scheduleState()
        : S.scheduleView === "week"
          ? weekView()
          : data
            ? [
                !data.lessons.length
                  ? el("p", { class: "notice" }, "На обрану дату занять немає.")
                  : null,
                dayTimeline(data),
              ]
            : empty("Сьогодні можна видихнути", "На обрану дату занять немає."),
    );
  }
  function inbox() {
    const requests = S.data.swaps;
    if (!requests.length) return null;
    return el(
      "section",
      {},
      el(
        "div",
        { class: "section-heading" },
        el("h2", {}, "Обміни"),
        badge(requests.length),
      ),
      requests.map((inv) =>
        el(
          "article",
          { class: "request-card" },
          el("strong", {}, inv.sender_name),
          el(
            "p",
            { class: "muted" },
            inv.status === "accepted"
              ? "Погоджений обмін. Можна повторити."
              : "Пропонує обмін місцями. Відповідь до " +
                  new Date(inv.expires_at).toLocaleTimeString("uk-UA", {
                    hour: "2-digit",
                    minute: "2-digit",
                    timeZone: "Europe/Kyiv",
                  }),
          ),
          el(
            "div",
            { class: "button-row" },
            btn(
              inv.status === "accepted" ? "Повторити обмін" : "Погодитися",
              () => mutate("/swaps/" + inv.token, { accept: true }),
              "button primary",
            ),
            inv.status === "pending"
              ? btn(
                  "Відхилити",
                  () => mutate("/swaps/" + inv.token, { accept: false }),
                  "button secondary",
                )
              : null,
          ),
        ),
      ),
    );
  }
  function swapsPage(){
    if(design)return design.swapsPage();
    return el("main",{class:"page swaps-page"},design?design.heading("Черги","Обміни місцями"):el("h1",{},"Обміни місцями"),inbox());
  }
  async function logout(){
    try {const response=await fetch("/auth/logout",{method:"POST",credentials:"same-origin",headers:authHeaders()});if(!response.ok)throw new Error("Не вдалося вийти. Оновіть сторінку.");location.reload();}catch(error){toast(error.message,true);}
  }
  function profilePage() {
    if(design) return design.profilePage();
    const own = mine(),
      name = S.data.me.name;
    return el(
      "main",
      {},
      el(
        "section",
        { class: "profile-heading" },
        el(
          "div",
          { class: "avatar" },
          avatarFace(
            "me",
            name
              .split(" ")
              .map((n) => n[0])
              .join("")
              .slice(0, 2),
          ),
        ),
        el("h1", {}, name),
        small(isAdmin() ? "Режим адміністратора · ПІ-231" : "Студент · ПІ-231"),
      ),
      inbox(),
      el(
        "div",
        { class: "section-heading" },
        el("h2", {}, "Мої записи"),
        badge(own.length),
      ),
      el("div", { class: "queue-grid" }, own.map(queueCard)),
      !own.length
        ? empty(
            "Твій час вільний",
            "Знайди потрібний предмет у вкладці «Черги».",
          )
        : null,
      el(
        "div",
        { class: "notice" },
        "Звичні кнопки залишилися в чаті. Твої дані однакові в боті та мініапці.",
      ),
      el(
        "div",
        { class: "action-stack" },
        btn(
          "Мої пропозиції предметів і викладачів",
          () => openFeature("proposals"),
          "button secondary",
        ),
        btn(
          "Звернення та відповіді",
          () => openFeature("bugs"),
          "button secondary",
        ),
        btn("Допомога", () => openFeature("help"), "button secondary"),
      ),
      btn(initData ? "Повернутися до бота" : "До черг", () => initData ? tg?.close() : navigate('queues'), "button secondary"),
      installCard(),
      !initData ? btn("Вийти з акаунта", async () => {
        try {
          const response = await fetch('/auth/logout', { method: 'POST', credentials: 'same-origin', headers: authHeaders() });
          if (!response.ok) throw new Error('Не вдалося вийти. Оновіть сторінку.');
          location.reload();
        } catch (error) { toast(error.message, true); }
      }, "button secondary") : null,
    );
  }
  function banForm(userId, name) {
    const panel = sheet(
        "Тимчасовий бан",
        name + ". Студента буде прибрано з активних черг.",
      ),
      days = el("input", {
        type: "number",
        min: 1,
        max: 36500,
        value: 1,
        required: true,
        step: 1,
        inputmode: "numeric",
      }),
      form = el(
        "form",
        {},
        el("label", { class: "field" }, "На скільки днів?", days),
        el(
          "button",
          { type: "submit", class: "button primary" },
          "Підтвердити бан",
        ),
      );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      mutate("/admin/users/" + userId, {
        action: "ban",
        days: Number(days.value),
      });
    });
    panel.append(form);
  }
  function adminEntryForm(q, row) {
    const panel = sheet(
        row.name,
        "Місце #" +
          row.position +
          (row.telegram_id > 0 ? " · ID: " + row.telegram_id : ""),
      ),
      position = el("input", {
        type: "number",
        min: 1,
        step: 1,
        value: row.position,
        required: true,
        inputmode: "numeric",
      }),
      move = el(
        "form",
        {},
        el("label", { class: "field" }, "Перемістити на місце", position),
        el(
          "button",
          { type: "submit", class: "button secondary" },
          "Перемістити",
        ),
      );
    move.addEventListener("submit", (event) => {
      event.preventDefault();
      mutate("/admin/entries/" + row.id, {
        action: "move",
        revision: row.entry_revision,
        position: Number(position.value),
      });
    });
    panel.append(move);
    if(q.lesson && !row.vacant) {
      const correct = response => mutate('/admin/entries/'+row.id,{action:'attendance',response,run_id:q.lesson.id,revision:row.entry_revision});
      panel.append(small(q.lesson.states[String(row.id)] || 'Оновіть чергу'),
        btn('Підтвердити присутність',()=>correct('present'),'button secondary'),
        btn('Пропустити на цій парі',()=>correct('skipped'),'button secondary'),
        btn('Завершити підхід з рештою робіт',()=>correct('finish'),'button secondary'));
    }
    if (!row.vacant)
      panel.append(
        btn(
          "Позначити «Здав»",
          () =>
            mutate("/admin/entries/" + row.id, {
              action: "complete",
              revision: row.entry_revision,
            }),
          "button primary",
        ),
        btn(
          "Тимчасовий бан",
          () => banForm(row.user_id, row.name),
          "button secondary",
        ),
      );
    panel.append(
      btn(
        "Видалити з черги",
        () =>
          confirmSheet("Видалити запис?", row.name, () =>
            mutate("/admin/entries/" + row.id, {
              action: "remove",
              revision: row.entry_revision,
            }),
          ),
        "text-button danger",
      ),
    );
  }
  function addStudentsForm(q) {
    const panel = sheet(
        "Додати студентів",
        "Кожне прізвище та ім’я — з нового рядка. Буде додано в кінець черги.",
      ),
      names = el("textarea", {
        rows: 5,
        maxlength: 4000,
        required: true,
        "aria-label": "Список студентів",
      }),
      form = el(
        "form",
        {},
        el("label", { class: "field" }, "Прізвище та ім’я", names),
        el(
          "button",
          { type: "submit", class: "button primary" },
          "Додати студентів",
        ),
      );
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      mutate("/admin/queues/" + q.id, {
        action: "add_students",
        names: names.value
          .split(/\r?\n/)
          .map((n) => n.trim())
          .filter(Boolean),
      });
    });
    panel.append(form);
  }
  function adminPage() {
    if(design) return design.adminPage();
    const sections = [
      [
        "admin-history",
        "Черги та історія",
        "Завершення, архів та перенесення учасників",
      ],
      ["admin-group", "Студенти групи", "Пошук, ПІБ, обмеження та бан"],
      [
        "admin-subjects",
        "Предмети й викладачі",
        "Каталог предметів та викладачів",
      ],
      [
        "admin-replacements",
        "Розклад і заміни",
        "Фото, розпізнавання, перевірка й архів",
      ],
      ["admin-saturdays", "Робочі суботи", "Дата, день розкладу та тип тижня"],
      ["admin-polls", "Опитування", "Створення, голосування та результати"],
      [
        "admin-reports",
        "Звіти та діаграми",
        "Статистика групи, предметів і PDF",
      ],
    ];
    if (S.data.me.is_owner)
      sections.push([
        "owner",
        "Налаштування власника",
        "Оголошення, техроботи, адміністратори та звернення",
      ]);
    return el(
      "main",
      {},
      el(
        "div",
        { class: "modebar" },
        "Режим адміністратора · ID доступні лише тут",
      ),
      el(
        "div",
        { class: "page-title" },
        el("h1", {}, "Керування"),
        small("Усі інструменти групи в одному місці."),
      ),
      el(
        "div",
        { class: "more-list admin-categories" },
        sections.map(([key, title, description]) =>
          btn(
            [icon(({'admin-history':'queues','admin-group':'users','admin-subjects':'mortarboard','admin-replacements':'schedule','admin-saturdays':'schedule','admin-polls':'polls','admin-reports':'polls',owner:'owner'})[key]),el('span',{},el("strong", {}, title), small(description)), icon("arrow")],
            () => openFeature(key),
            "more-row admin-nav-row",
          ),
        ),
      ),
      sectionTitle("Поточні черги", String(S.data.queues.length)),
      btn("Створити чергу", () => openFeature("create"), "button secondary"),
      el("div", { class: "subject-list" }, S.data.queues.map(subjectCard)),
    );
  }
  function registrationPage() {
    const name = el("input", {
        required: true,
        maxlength: 100,
        autocomplete: "name",
        placeholder: "Прізвище та ім’я",
        "aria-label": "Прізвище та ім’я",
      }),
      error = el("p", { class: "notice error", role: "alert", hidden: true }),
      submit = el(
        "button",
        { type: "submit", class: "button primary" },
        "Зареєструватися",
      ),
      form = el(
        "form",
        { class: "action-stack" },
        el("label", { class: "field" }, "Прізвище та ім’я", name),
        error,
        submit,
      );
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (S.busy) return;
      S.busy = true;
      submit.disabled = true;
      error.hidden = true;
      try {
        await api("/register", { full_name: name.value.trim() });
        await load();
      } catch (err) {
        error.textContent = err.message;
        error.hidden = false;
      } finally {
        S.busy = false;
        submit.disabled = false;
      }
    });
    return el(
      "main",
      {},
      el(
        "div",
        { class: "page-title" },
        badge("Перший вхід"),
        el("h1", {}, "Вітаємо в ПІ-231"),
        el(
          "p",
          { class: "muted" },
          "Вкажи справжні прізвище та ім’я. Якщо тебе вже додали до черги, запис буде під’єднано автоматично.",
        ),
      ),
      form,
    );
  }
  const studentProfiles=new Map();
  function enrichStudents(roster) {
    roster.disposeProfiles?.();
    let disposed=false, observer;
    const urls=[];
    roster.disposeProfiles=()=>{disposed=true;observer?.disconnect();urls.splice(0).forEach(url=>URL.revokeObjectURL(url));};
    liquidCleanups.push(roster.disposeProfiles);
    const waiting=[];let running=0;
    const alive=node=>!disposed&&isAdmin()&&node.isConnected;
    async function show(node) {
      const id=node.dataset.studentId,telegram=node.dataset.telegramId,key=id+':'+telegram;
      if(!alive(node)||Number(telegram)<=0)return;
      const label=node.querySelector('.student-username');
      try {
        let cached=studentProfiles.get(key);
        if(!cached||cached.until<Date.now()) {
          cached={value:await api('/admin/users/'+encodeURIComponent(id)+'/telegram'),until:Date.now()+300000};
          if(!alive(node))return;
          studentProfiles.set(key,cached);
          if(studentProfiles.size>64)studentProfiles.delete(studentProfiles.keys().next().value);
        }
        if(!alive(node))return;
        const profile=cached.value;
        label.textContent=profile.username?'@'+profile.username:profile.username_available?'Username не задано':'Профіль недоступний';
        if(profile.has_photo){
          const response=await fetch('/api/miniapp/admin/users/'+encodeURIComponent(id)+'/avatar?mode=admin',{headers:authHeaders(),credentials:'same-origin',cache:'no-store'});
          if(response.status!==200||!response.headers.get('content-type')?.startsWith('image/'))return;
          const blob=await response.blob();
          if(!alive(node))return;
          const url=URL.createObjectURL(blob);urls.push(url);
          const image=el('img',{src:url,alt:'',loading:'lazy'});
          image.addEventListener('error',()=>image.remove(),{once:true});
          node.querySelector('.student-avatar').append(image);
        }
      }catch(error){
        if(!disposed && [401,403].includes(error.status)){studentProfiles.clear();if(S.data)S.fatal=error.message;render();return;}
        if(alive(node))label.textContent='Профіль недоступний';
      }
    }
    function drain(){while(running<3&&waiting.length&&!disposed){const node=waiting.shift();running++;show(node).finally(()=>{running--;drain();});}}
    window.requestAnimationFrame(()=>{
      if(disposed)return;
      const nodes=[...roster.querySelectorAll('.student-identity')];
      if(window.IntersectionObserver){observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting){observer.unobserve(entry.target);waiting.push(entry.target);}drain();},{rootMargin:'150px'});nodes.forEach(node=>observer.observe(node));}
      else {waiting.push(...nodes);drain();}
    });
  }
  let previousView = null;
  function render() {
    liquidCleanups.splice(0).forEach((cleanup) => cleanup());
    if(!isAdmin())studentProfiles.clear();
    if (navCache) navCache.node.hidden = !S.data || (!initData && !browserSession.authenticated);
    // Keep navigation connected: detaching its lens cancels CSS interpolation.
    if (root.removeChild && navCache?.node.parentNode === root) {
      [...root.children].forEach(node => { if (node !== navCache.node) root.removeChild(node); });
      if (design ? Boolean(S.data && (initData || browserSession.authenticated)) : !S.queue) root.insertBefore(topbar(), navCache.node);
    } else root.replaceChildren(...((design ? !S.data || (!initData && !browserSession.authenticated) : S.queue) ? [] : [topbar()]));
    if (!initData && !browserSession.authenticated) {
      root.append(
        loginPage(),
      );
      return;
    }
    if (S.fatal) {
      root.append(
        el(
          "main",
          {},
          empty("Не вдалося завантажити", S.fatal),
          btn(
            "Спробувати знову",
            () =>
              S.feature
                ? features.open(S.feature)
                : S.tab === "schedule"
                  ? refreshSchedule()
                  : load(),
            "button primary",
          ),
        ),
      );
      return;
    }
    if (!S.data) {
      root.append(
        el(
          "div",
          { class: "loading", role: "status" },
          el("span", { class: "spinner" }),
          "Збираємо твій простір…",
        ),
      );
      return;
    }
    if (S.data.me.is_registered === false) {
      root.append(registrationPage());
      return;
    }
    const page = S.feature
        ? (design && S.feature==='help' ? design.helpPage() : features.page(S.feature))
        : S.queue
          ? queuePage()
          : S.tab === "schedule"
            ? schedulePage()
            : S.tab === "swaps"
            ? swapsPage()
            : S.tab === "profile"
              ? profilePage()
              : S.tab === "admin" && isAdmin()
                ? adminPage()
                : S.tab === 'more' ? morePage()
                  : S.tab === 'home' ? homePage() : queuesPage();
    design?.decorate(page);
    const nav = bottomnav();
    if (root.insertBefore && nav.parentNode === root) root.insertBefore(page, nav);
    else root.append(page, nav);
    const view = S.feature || (S.queue ? 'queue-' + S.queue.id : S.mode + '-' + S.tab);
    if (view !== previousView) root.querySelector?.('main')?.classList.add('page-enter');
    previousView = view;
    root.classList.toggle("is-loading", S.loading && !S.scheduleLoading);
    if (S.loading)
      root.append(
        el("div", {
          class: "progress",
          role: "status",
          "aria-label": "Оновлюю дані",
        }),
      );
    installPressOptics(root);
    if (tg?.BackButton) {
      S.queue || S.feature ? tg.BackButton.show() : tg.BackButton.hide();
    }
  }
  features = window.createPI231Features?.({
    S,
    el,
    btn,
    small,
    badge,
    icon,
    api,
    toast,
    sheet,
    closeSheet,
    mutate,
    load,
    navigate,
    isAdmin,
    render,
    download,
    openQueue,
    openImage,
    updateBusy,
    enrichStudents,
  });
  design = window.createPI231Design?.({S, el, btn, small, api, sheet, closeSheet, navigate, openFeature, openQueue,
    labForm, avatarFace, consultation, labText, isAdmin, render, toast, appearanceSettings, setAppearance,
    mutate, load, logout, completionForm, adminEntryForm, confirmSheet, reference, download, installCard, browserSession, loginFailureMessage, initData});
  window.matchMedia?.('(prefers-color-scheme: dark)')?.addEventListener?.('change',()=>{if(presentation.theme==='system')theme();});
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !S.busy) {closeSheet();design?.closeMenu();}
  });
  window.setInterval(() => {features?.tick?.();design?.tick?.();}, 1000);
  window.setInterval(() => {
    if(document.visibilityState === 'visible' && S.queue && !S.busy && !S.loading && !document.querySelector('.sheet')) void load();
  }, 15000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {features?.tick?.(); if(S.queue && !S.busy && !S.loading && !document.querySelector('.sheet')) void load();}
  });
  theme();
  tg?.onEvent?.("themeChanged", theme);
  tg?.BackButton?.onClick(() => {
    if (!S.busy) {
      S.queue = null;
      S.feature = null;
      navigate(isAdmin() ? "admin" : "home");
    }
  });
  tg?.ready();
  tg?.expand();
  render();
  function loginPage() {
    if(design) return design.loginPage();
    const panel = el('main', {class: 'login-page'},
      el('h1', {}, 'Твій простір ПІ-231'),
      el('p', {class: 'muted'}, 'Черги, розклад та опитування — з телефона чи комп’ютера.'),
    );
    if (!browserSession.ready) panel.append(el('p', {role: 'status'}, 'Перевіряю вхід…'));
    else if (browserSession.enabled) {
      if (new URLSearchParams(location.search).get('login') === 'failed')
        panel.append(el('p', {class: 'notice', role: 'alert'}, loginFailureMessage(new URLSearchParams(location.search).get('reason'))));
      panel.append(el('a', {href: '/auth/telegram/start', class: 'button primary'}, 'Увійти через Telegram'),
        el('p', {class: 'muted'}, 'Підтвердь свій акаунт у Telegram. Твої записи під’єднаються автоматично.'));
    } else panel.append(el('p', {class: 'notice'}, 'Вхід у браузері поки не налаштовано. Відкрий мініапку через меню бота в Telegram.'));
    panel.append(btn('Додати на головний екран', async () => toast(await window.PI231PWA?.install() || 'Скористайся меню браузера → Встановити застосунок.'), 'button secondary'));
    return panel;
  }
  async function startApp() {
    if (!initData) {
      try {
        const response = await fetch('/auth/session', { credentials: 'same-origin', cache: 'no-store' });
        if (!response.ok) throw new Error('Session unavailable');
        const session = await response.json();
        browserSession.authenticated = session.authenticated === true;
        browserSession.csrf = session.csrf;
        browserSession.enabled = session.login_enabled === true;
      } catch (_) {
        browserSession.enabled = true;
      }
      browserSession.ready = true;
      render();
    }
    if (initData || browserSession.authenticated) await load().then(async () => {
      const pid = new URLSearchParams(location.search).get("poll");
      if (pid && /^[1-9][0-9]*$/.test(pid) && S.data?.me.is_registered) {
        await openFeature("polls");
        features.openPoll(Number(pid));
      }
    });
  }
  void startApp();
})();
