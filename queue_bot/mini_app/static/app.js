"use strict";
(() => {
  const tg = window.Telegram?.WebApp,
    initData = tg?.initData || "",
    root = document.getElementById("app");
  const S = {
    tab: "queues",
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
  const paths = {
    queues: "M4 4h16v4H4z M4 12h16 M4 17h10 M4 21h7",
    schedule: "M4 5h16v16H4z M8 3v4 M16 3v4 M4 11h16 M8 15h2 M14 15h2 M8 18h2",
    profile: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8 M4 21v-2a8 6 0 0 1 16 0v2",
    admin: "M12 3 3 7v6c0 4 9 9 9 9s9-5 9-9V7z M8 12l3 3 5-6",
    arrow: "M5 12h14 M14 7l5 5-5 5",
    refresh:
      "M20 7v5h-5 M4 17v-5h5 M6 7a7 7 0 0 1 12-1l2 6 M4 12l2 6a7 7 0 0 0 12-1",
    back: "M19 12H5 M10 7l-5 5 5 5",
    close: "M6 6l12 12 M18 6 6 18",
  };
  function icon(name) {
    if (window.PI231Icon) return window.PI231Icon(name);
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
          Authorization: "tma " + initData,
          ...(data === undefined ? {} : { "Content-Type": "application/json" }),
        },
        ...(data === undefined ? {} : { body: JSON.stringify(data) }),
        cache: "no-store",
        credentials: "omit",
      },
    );
    const result = await response.json().catch(() => ({
      error: "Сервер тимчасово недоступний. Спробуйте ще раз.",
    }));
    if (!response.ok) {
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
      headers: { Authorization: "tma " + initData },
      credentials: "omit",
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
        headers: { Authorization: "tma " + initData },
        credentials: "omit",
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
              headers: { Authorization: "tma " + initData },
              credentials: "omit",
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
  function theme() {
    document.documentElement.dataset.theme =
      tg?.colorScheme ||
      (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
  }
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
    const tools = el("div", { class: "top-tools" });
    if (S.data?.me.is_admin)
      tools.append(
        btn(
          isAdmin() ? "До студента" : "До адміна",
          () => {
            if (S.busy || S.loading) return;
            S.mode = isAdmin() ? "student" : "admin";
            S.queue = null;
            S.feature = null;
            S.tab = isAdmin() ? "admin" : "queues";
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
  function bottomnav() {
    const tabs = [
      isAdmin() ? ["admin", "Керування"] : ["queues", "Черги"],
      ["schedule", "Розклад"],
      ["polls", "Опитування"],
      ["profile", "Профіль"],
      ["help", "Допомога"],
    ];
    const activeTab =
      S.feature === "polls" || S.feature === "help" ? S.feature : S.tab;
    return el(
      "nav",
      { class: "bottom-nav", "aria-label": "Основні розділи" },
      tabs.map(([key, label]) => {
        const node = btn(
          [icon(key), el("span", {}, label)],
          () => navigate(key),
          "nav-item " + (activeTab === key ? "active" : ""),
        );
        if (activeTab === key) node.setAttribute("aria-current", "page");
        return node;
      }),
    );
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
  function queuesPage() {
    const qs = S.data.queues,
      own = mine();
    const date = new Date(S.data.today + "T12:00:00Z");
    return el(
      "main",
      {},
      el(
        "section",
        { class: "welcome" },
        el(
          "p",
          { class: "eyebrow" },
          date.toLocaleDateString("uk-UA", {
            weekday: "long",
            day: "numeric",
            month: "long",
            timeZone: "Europe/Kyiv",
          }),
        ),
        el(
          "h1",
          {},
          "Привіт, " + S.data.me.name.split(" ").slice(0, 2).join(" "),
        ),
        el("p", {}, "Усе важливе для здачі лабораторних — поруч."),
      ),
      btn(
        [icon("plus"), "Відкрити чергу"],
        () => openFeature("create"),
        "button secondary create-queue",
      ),
      sectionTitle("Мої записи", "Активних: " + own.length),
      own.length
        ? el("div", { class: "queue-grid" }, own.map(queueCard))
        : empty(
            "Поки немає записів",
            "Обери предмет нижче й займи своє місце.",
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
      controls = el("div", { class: "action-stack" });
    if (own) {
      controls.append(
        btn(
          q.labs.length
            ? "Здав" + (own.labs?.length > 1 ? " лабораторні" : "")
            : "Завершити запис",
          () => {
            if (q.labs.length && own.labs?.length > 1) completionForm(q, own);
            else mutate("/queues/" + q.id + "/complete", reference(own));
          },
          "button primary",
        ),
      );
      if (q.labs.length)
        controls.append(
          btn("Лабораторні та час", () => labForm(q, own), "button secondary"),
        );
      if (!consultation(q))
        controls.append(
          btn(
            "Не здав — у кінець",
            () =>
              confirmSheet(
                "Перейти в кінець черги?",
                "Вибрані лабораторні збережуться.",
                () => mutate("/queues/" + q.id + "/not-passed", reference(own)),
              ),
            "button secondary",
          ),
        );
      controls.append(
        el(
          "div",
          { class: "button-row" },
          btn(
            own.swap_wish === "higher" ? "Зняти «Хочу вище»" : "Хочу вище ↑",
            () =>
              mutate("/queues/" + q.id + "/wish", {
                ...reference(own),
                wish: own.swap_wish === "higher" ? null : "higher",
              }),
            "button secondary",
          ),
          btn(
            own.swap_wish === "lower" ? "Зняти «Хочу нижче»" : "Хочу нижче ↓",
            () =>
              mutate("/queues/" + q.id + "/wish", {
                ...reference(own),
                wish: own.swap_wish === "lower" ? null : "lower",
              }),
            "button secondary",
          ),
        ),
      );
      controls.append(
        btn(
          "Вийти з черги",
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
      controls.append(btn("Стати в чергу", () => labForm(q), "button primary"));
    else
      controls.append(
        el(
          "p",
          { class: "notice" },
          "Запис закрито. Уже записані студенти можуть завершувати здачу.",
        ),
      );
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
          String(row.position).padStart(2, "0"),
        ),
        el(
          "div",
          { class: "entry-info" },
          el(
            "strong",
            {},
            row.name,
            row.is_me ? el("span", { class: "you" }, "ти") : null,
          ),
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
    return el(
      "main",
      {},
      back,
      el(
        "div",
        { class: "detail-title" },
        badge(consultation(q) ? "Консультація" : "Черга предмета"),
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
              small("ТВОЄ МІСЦЕ"),
              el("strong", {}, "#" + own.position),
            ),
            el(
              "div",
              {},
              el("b", {}, own.position === 1 ? "Твоя черга" : "Ти в черзі"),
              el(
                "p",
                {},
                own.position === 1
                  ? "Готуй роботи до здачі."
                  : "Стеж за оновленнями тут або в боті.",
              ),
            ),
          )
        : null,
      controls,
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
    document.getElementById("sheet")?.remove();
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
      el(
        "div",
        { class: "segments" },
        btn(
          "Сьогодні",
          () => loadSchedule(S.data.today),
          S.scheduleView === "day" && S.scheduleDate === S.data.today
            ? "selected"
            : "",
        ),
        btn(
          "Завтра",
          () => loadSchedule(tomorrow()),
          S.scheduleView === "day" && S.scheduleDate === tomorrow()
            ? "selected"
            : "",
        ),
        btn(
          "Тиждень",
          () => loadWeek(),
          S.scheduleView === "week" ? "selected" : "",
        ),
      ),
      data?.source_day
        ? el(
            "p",
            { class: "notice" },
            "Навчальний день за розкладом: " +
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
  function profilePage() {
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
      btn("Повернутися до бота", () => tg?.close(), "button secondary"),
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
        { class: "admin-grid" },
        sections.map(([key, title, description]) =>
          btn(
            [el("strong", {}, title), small(description), icon("arrow")],
            () => openFeature(key),
            "card admin-category",
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
  function render() {
    root.replaceChildren(topbar());
    if (!initData) {
      root.append(
        empty(
          "Відкрий з Telegram",
          "Зайди в бот ПІ-231 та натисни «Мініапка». Це потрібно для безпечного входу.",
        ),
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
    root.append(
      S.feature
        ? features.page(S.feature)
        : S.queue
          ? queuePage()
          : S.tab === "schedule"
            ? schedulePage()
            : S.tab === "profile"
              ? profilePage()
              : S.tab === "admin" && isAdmin()
                ? adminPage()
                : queuesPage(),
      bottomnav(),
    );
    root.classList.toggle("is-loading", S.loading && !S.scheduleLoading);
    if (S.loading)
      root.append(
        el("div", {
          class: "progress",
          role: "status",
          "aria-label": "Оновлюю дані",
        }),
      );
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
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && !S.busy) closeSheet();
  });
  theme();
  tg?.onEvent?.("themeChanged", theme);
  tg?.BackButton?.onClick(() => {
    if (!S.busy) {
      S.queue = null;
      S.feature = null;
      navigate(isAdmin() ? "admin" : "queues");
    }
  });
  tg?.ready();
  tg?.expand();
  render();
  if (initData)
    load().then(async () => {
      const pid = new URLSearchParams(location.search).get("poll");
      if (pid && /^[1-9][0-9]*$/.test(pid) && S.data?.me.is_registered) {
        await openFeature("polls");
        features.openPoll(Number(pid));
      }
    });
})();
