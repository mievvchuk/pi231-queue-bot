"use strict";
/* Real-service screens. All server text is inserted through the text-safe el helper. */
window.createPI231Features = function (H) {
  const {
    S,
    el,
    btn,
    small,
    badge,
    api,
    toast,
    sheet,
    closeSheet,
    navigate,
    render,
  } = H;
  const titles = {
    create: "Відкрити чергу",
    proposals: "Пропозиції",
    bugs: "Повідомити про помилку",
    polls: "Опитування",
    help: "Допомога",
    "admin-history": "Історія черг",
    "admin-group": "Група",
    "admin-subjects": "Предмети й викладачі",
    "admin-replacements": "Заміни",
    "admin-saturdays": "Робочі суботи",
    "admin-polls": "Керування опитуваннями",
    "admin-reports": "Звіти",
    owner: "Панель власника",
    "admin-admins": "Адміністратори",
    "admin-bugs": "Звернення студентів",
    "admin-proposals": "Розгляд пропозицій",
  };
  const ownerKeys = new Set([
    "owner",
    "admin-admins",
    "admin-bugs",
    "admin-proposals",
  ]);
  let state = { key: null, loading: false, data: null, error: null, token: 0 },
    pollRequest = 0;
  const adminPath = (key) =>
    "/admin/manage/" + (key === "owner" ? "owner" : key.slice(6));
  const rows = (items) => (Array.isArray(items) ? items : []);
  const card = (...children) =>
    el("section", { class: "card request-card" }, ...children);
  const list = (items, mapper, empty = "Поки немає записів.") =>
    items.length
      ? el("div", { class: "list feature-list" }, items.map(mapper))
      : el("p", { class: "notice" }, empty);
  const field = (label, input) => el("label", { class: "field" }, label, input);
  const input = (attrs = {}) => el("input", { required: true, ...attrs });
  const text = (attrs = {}) =>
    el("textarea", { required: true, rows: 4, maxlength: 4000, ...attrs });
  const select = (options, value) => {
    const node = el(
      "select",
      { required: true },
      options.map(([v, label]) => el("option", { value: v }, label)),
    );
    if (value !== undefined) node.value = value;
    return node;
  };
  const checkbox = (label, checked = false) => {
    const node = el("input", { type: "checkbox", checked });
    return { node, view: field(label, node) };
  };
  const statusText = (value) =>
    ({
      pending: "На розгляді",
      approved: "Схвалено",
      rejected: "Відхилено",
      open: "Відкрито",
      closed: "Закрито",
      resolved: "Вирішено",
      active: "Активне",
    })[value] ||
    value ||
    "";
  const owner = () => Boolean(S.data?.me?.is_owner);
  const allowed = (key) =>
    ownerKeys.has(key)
      ? owner() && H.isAdmin()
      : key.startsWith("admin-")
        ? H.isAdmin()
        : true;
  const current = (token, epoch, key) =>
    token === state.token &&
    epoch === S.epoch &&
    key === S.feature &&
    allowed(key);
  function accessError(err, ctx) {
    if (
      ![401, 403].includes(err.status) ||
      !current(ctx.token, ctx.epoch, ctx.key)
    )
      return false;
    closeSheet();
    S.fatal = err.message;
    if (H.accessError) H.accessError(err);
    else render();
    return true;
  }
  function updateBusy() {
    if (H.updateBusy) H.updateBusy(S.busy);
    else if (typeof document !== "undefined") {
      const refresh = document.querySelector('button[aria-label="Оновити"]');
      if (refresh) refresh.disabled = Boolean(S.busy || S.loading);
    }
  }
  async function externalAction(action) {
    if (S.busy) return;
    const ctx = { key: S.feature, token: state.token, epoch: S.epoch };
    S.busy = true;
    updateBusy();
    try {
      await action();
    } catch (err) {
      if (!accessError(err, ctx) && current(ctx.token, ctx.epoch, ctx.key))
        toast(err.message, true);
    } finally {
      S.busy = false;
      updateBusy();
    }
  }
  function form(children, label, handler) {
    const error = el("p", {
      class: "notice error",
      role: "alert",
      hidden: true,
    });
    const submit = el(
      "button",
      { type: "submit", class: "button primary" },
      label,
    );
    const node = el(
      "form",
      { class: "action-stack feature-form" },
      children,
      error,
      submit,
    );
    node.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (S.busy || !node.reportValidity()) return;
      const key = S.feature,
        token = state.token,
        epoch = S.epoch;
      S.busy = true;
      updateBusy();
      const controls = [
        ...node.querySelectorAll("button,input,select,textarea"),
      ];
      const oldDisabled = controls.map((control) => control.disabled);
      controls.forEach((control) => {
        control.disabled = true;
      });
      error.hidden = true;
      submit.textContent = "Виконуємо…";
      try {
        await handler({ key, token, epoch });
      } catch (err) {
        if (accessError(err, { token, epoch, key })) return;
        if (current(token, epoch, key) && node.isConnected) {
          error.textContent = err.message || "Не вдалося виконати дію.";
          error.hidden = false;
        }
      } finally {
        S.busy = false;
        updateBusy();
        controls.forEach((control, index) => {
          control.disabled = oldDisabled[index];
        });
        submit.textContent = label;
      }
    });
    return node;
  }
  async function send(path, body, ctx, refresh = true) {
    const result = await api(path, body);
    if (current(ctx.token, ctx.epoch, ctx.key)) {
      closeSheet();
      toast(result.message || "Збережено");
      if (refresh) await open(ctx.key, true);
    }
    return result;
  }
  function confirm(title, description, path, body) {
    const panel = sheet(title, description);
    panel.append(form([], "Підтвердити", (ctx) => send(path, body, ctx)));
  }
  function edit(title, children, label, handler) {
    sheet(title, "Перевір дані перед підтвердженням.").append(
      form(children, label, handler),
    );
  }
  async function open(key, force = false, query = "") {
    if ((!force && S.busy) || !titles[key]) return;
    S.feature = key;
    S.queue = null;
    const epoch = ++S.epoch,
      token = state.token + 1;
    state = { key, loading: true, data: null, error: null, token, query };
    S.loading = false;
    render();
    if (!allowed(key)) {
      state.loading = false;
      state.error = "Цей розділ недоступний у поточному режимі.";
      render();
      return;
    }
    try {
      let data;
      if (key === "help") data = {};
      else if (key === "create") data = await api("/catalog");
      else if (key === "proposals") {
        const [proposals, catalog] = await Promise.all([
          api("/proposals"),
          api("/catalog"),
        ]);
        data = { ...proposals, ...catalog };
      } else
        data = await api(
          key.startsWith("admin-") || key === "owner"
            ? adminPath(key) + query
            : "/" + key,
        );
      if (current(token, epoch, key)) {
        state.data = data;
        S.fatal = null;
      }
    } catch (err) {
      if (accessError(err, { token, epoch, key })) return;
      if (current(token, epoch, key)) state.error = err.message;
    } finally {
      if (current(token, epoch, key)) {
        state.loading = false;
        render();
      }
    }
  }
  function createPage(data) {
    const subjects = rows(data.subjects),
      subject = select([
        ["", "Обери предмет"],
        ...subjects.map((s) => [s.id, s.name]),
      ]);
    const teacher = select([["", "Спершу обери предмет"]]);
    subject.addEventListener("change", () => {
      const selected = subjects.find((s) => String(s.id) === subject.value);
      teacher.replaceChildren(
        el("option", { value: "" }, "Обери викладача"),
        ...rows(selected?.teachers).map((t) =>
          el("option", { value: t.id }, t.name),
        ),
      );
    });
    const type = select([
      ["normal", "Звичайна пара"],
      ["consultation", "Консультація"],
    ]);
    return [
      card(
        form(
          [
            field("Тип черги", type),
            field("Предмет", subject),
            field("Викладач", teacher),
          ],
          "Відкрити чергу",
          async (ctx) => {
            const result = await send(
              "/queues",
              {
                subject_id: Number(subject.value),
                teacher_id: Number(teacher.value),
                type: type.value,
              },
              ctx,
              !H.openQueue,
            );
            if (
              H.openQueue &&
              result.session_id &&
              current(ctx.token, ctx.epoch, ctx.key)
            ) {
              S.feature = null;
              S.busy = false;
              await H.openQueue(result.session_id);
            }
          },
        ),
      ),
      btn(
        "Немає предмета чи викладача?",
        () => open("proposals"),
        "button secondary",
      ),
    ];
  }
  function proposalsPage(data, admin = false) {
    const path = admin ? adminPath("admin-proposals") : "/proposals";
    const content = [];
    if (!admin) {
      const kind = select([
        ["subject", "Новий предмет"],
        ["teacher", "Викладач до предмета"],
      ]);
      const subjectName = input({
        maxlength: 200,
        placeholder: "Назва предмета",
      });
      const teacherName = input({
        maxlength: 200,
        placeholder: "ПІБ викладача",
      });
      const subject = select([
        ["", "Обери предмет"],
        ...rows(data.subjects).map((s) => [s.id, s.name]),
      ]);
      const nameField = field("Новий предмет", subjectName),
        subjectField = field("До предмета", subject);
      const update = () => {
        const isTeacher = kind.value === "teacher";
        nameField.hidden = isTeacher;
        subjectName.required = !isTeacher;
        subjectField.hidden = !isTeacher;
        subject.required = isTeacher;
      };
      kind.addEventListener("change", update);
      update();
      content.push(
        card(
          form(
            [
              field("Що пропонуєш?", kind),
              nameField,
              subjectField,
              field("Викладач", teacherName),
            ],
            "Надіслати пропозицію",
            (ctx) =>
              send(
                path,
                {
                  kind: kind.value,
                  teacher_name: teacherName.value.trim(),
                  ...(kind.value === "teacher"
                    ? { subject_id: Number(subject.value) }
                    : { subject_name: subjectName.value.trim() }),
                },
                ctx,
              ),
          ),
        ),
      );
    }
    content.push(
      list(rows(data.items), (item) =>
        card(
          el("h3", {}, item.subject_name || item.subject || "Викладач"),
          el("p", {}, item.teacher_name),
          badge(statusText(item.status)),
          admin && item.status === "pending"
            ? el(
                "div",
                { class: "button-row" },
                btn(
                  "Схвалити",
                  () =>
                    confirm(
                      "Схвалити пропозицію?",
                      "Предмет або викладач буде доданий до каталогу.",
                      path,
                      { action: "approve", id: item.id },
                    ),
                  "button primary",
                ),
                btn(
                  "Відхилити",
                  () =>
                    confirm(
                      "Відхилити пропозицію?",
                      "Статус зміниться для автора.",
                      path,
                      { action: "reject", id: item.id },
                    ),
                  "button secondary",
                ),
              )
            : null,
        ),
      ),
    );
    return content;
  }
  function bugsPage(data, admin = false) {
    const path = admin ? adminPath("admin-bugs") : "/bugs",
      content = [];
    if (!admin) {
      const description = text({
        required: false,
        placeholder: "Що сталося? Які дії призвели до помилки?",
      });
      const screenshot = input({
        type: "file",
        required: false,
        accept: "image/jpeg,image/png,image/webp",
      });
      content.push(
        card(
          form(
            [
              field("Опис помилки", description),
              field("Скріншот до 1 МБ (необов’язково)", screenshot),
            ],
            "Надіслати звернення",
            async (ctx) => {
              const file = screenshot.files?.[0];
              if (!description.value.trim() && !file)
                throw new Error("Додай опис або скріншот.");
              if (file && file.size > 1024 * 1024)
                throw new Error("Скріншот має бути до 1 МБ.");
              const image = file ? await readImage(file) : null;
              if (!current(ctx.token, ctx.epoch, ctx.key)) return;
              return send(
                path,
                { text: description.value.trim(), ...(image ? { image } : {}) },
                ctx,
              );
            },
          ),
        ),
      );
    }
    content.push(
      list(rows(data.items), (item) =>
        card(
          el("p", {}, item.text),
          item.has_photo || item.photo_file_id
            ? el(
                "div",
                {},
                small("До звернення додано скріншот"),
                H.openImage
                  ? btn(
                      "Переглянути скріншот",
                      () =>
                        externalAction(() =>
                          H.openImage(
                            "/bugs/" + encodeURIComponent(item.id) + "/photo",
                          ),
                        ),
                      "button secondary",
                    )
                  : H.download
                    ? btn(
                        "Завантажити скріншот",
                        () =>
                          externalAction(() =>
                            H.download(
                              "/bugs/" + encodeURIComponent(item.id) + "/photo",
                              "bug-" + item.id + ".jpg",
                            ),
                          ),
                        "button secondary",
                      )
                    : null,
              )
            : null,
          badge(statusText(item.status)),
          item.admin_reply
            ? el("p", { class: "notice" }, "Відповідь: " + item.admin_reply)
            : null,
          admin
            ? el(
                "div",
                { class: "button-row" },
                btn(
                  "Відповісти",
                  () => {
                    const reply = text();
                    edit(
                      "Відповідь на звернення",
                      [field("Текст відповіді", reply)],
                      "Надіслати",
                      (ctx) =>
                        send(
                          path,
                          {
                            action: "reply",
                            report_id: item.id,
                            text: reply.value.trim(),
                          },
                          ctx,
                        ),
                    );
                  },
                  "button secondary",
                ),
                item.status !== "resolved"
                  ? btn(
                      "Вирішено",
                      () =>
                        confirm("Закрити звернення?", item.text, path, {
                          action: "resolve",
                          report_id: item.id,
                        }),
                      "button secondary",
                    )
                  : null,
              )
            : null,
        ),
      ),
    );
    return content;
  }
  function historyPage(data) {
    const path = adminPath("admin-history");
    return [
      list(rows(data.sessions), (item) =>
        card(
          el("h3", {}, item.subject),
          small(
            [item.teacher, item.date, statusText(item.status)]
              .filter(Boolean)
              .join(" · "),
          ),
          btn(
            "Учасники сесії",
            () =>
              open(
                "admin-history",
                false,
                "?sid=" + encodeURIComponent(item.id),
              ),
            "button secondary",
          ),
          ["open", "closed"].includes(item.status)
            ? btn(
                "Завершити сесію",
                () => finishSession(item),
                "button secondary",
              )
            : null,
        ),
      ),
      rows(data.entries).length
        ? card(
            el("h2", {}, "Учасники обраної сесії"),
            list(data.entries, (entry) =>
              el(
                "p",
                {},
                [
                  entry.position,
                  entry.name || entry.full_name,
                  statusText(entry.status),
                ]
                  .filter((v) => v !== undefined)
                  .join(" · "),
              ),
            ),
          )
        : null,
    ];
  }
  async function finishSession(session) {
    const ctx = { key: S.feature, token: state.token, epoch: S.epoch };
    try {
      const detail = await api(
        adminPath("admin-history") + "?sid=" + encodeURIComponent(session.id),
      );
      if (!current(ctx.token, ctx.epoch, ctx.key)) return;
      const create = checkbox("Створити нову чергу");
      const entries = rows(detail.entries)
        .filter((entry) => entry.status === "active")
        .map((entry, index) => {
          const keep = checkbox(entry.full_name || entry.name || "Учасник");
          const position = input({
            type: "number",
            min: 1,
            max: 1000,
            step: 1,
            value: index + 1,
          });
          return {
            entry,
            keep,
            position,
            view: card(keep.view, field("Місце в новій черзі", position)),
          };
        });
      edit(
        "Завершення сесії",
        [
          create.view,
          el(
            "p",
            { class: "notice" },
            "Для перенесення обери нову чергу, учасників і їхні місця.",
          ),
          entries.map((entry) => entry.view),
        ],
        "Завершити",
        (submitCtx) => {
          const selected = entries.filter((entry) => entry.keep.node.checked);
          if (selected.length && !create.node.checked)
            throw new Error("Для перенесення потрібно створити нову чергу.");
          const positions = selected.map((entry) =>
            Number(entry.position.value),
          );
          if (new Set(positions).size !== positions.length)
            throw new Error("Місця учасників не повинні повторюватися.");
          return send(
            adminPath("admin-history"),
            {
              action: "finish",
              session_id: session.id,
              create_new: create.node.checked,
              carry: Object.fromEntries(
                selected.map((entry) => [
                  entry.entry.id,
                  Number(entry.position.value),
                ]),
              ),
            },
            submitCtx,
          );
        },
      );
    } catch (err) {
      if (!accessError(err, ctx) && current(ctx.token, ctx.epoch, ctx.key))
        toast(err.message, true);
    }
  }
  function groupPage(data) {
    const path = adminPath("admin-group"),
      fullName = input({ maxlength: 200 });
    const users = rows(data.users || data.items),
      roster = el("div", { class: "list" });
    const userCard = (user) =>
      card(
        el("h3", {}, user.full_name || user.name),
        small("ID: " + user.telegram_id),
        badge(user.is_blocked ? "Обмежено" : "Активний"),
        user.protected
          ? badge("Адміністратор")
          : el(
              "div",
              { class: "button-row" },
              btn(
                "Змінити ПІБ",
                () => {
                  const name = input({
                    value: user.full_name || user.name,
                    maxlength: 200,
                  });
                  edit(
                    "Змінити ПІБ",
                    [field("Повне ім’я", name)],
                    "Зберегти",
                    (ctx) =>
                      send(
                        path,
                        {
                          action: "rename",
                          user_id: user.id,
                          name: name.value.trim(),
                        },
                        ctx,
                      ),
                  );
                },
                "button secondary",
              ),
              btn(
                user.is_blocked ? "Розбанувати" : "Обмежити доступ",
                () => {
                  if (user.is_blocked)
                    confirm(
                      "Розбанувати студента?",
                      user.full_name || user.name,
                      path,
                      { action: "unban", user_id: user.id },
                    );
                  else {
                    const days = input({
                      type: "number",
                      min: 1,
                      max: 36500,
                      step: 1,
                      value: 1,
                    });
                    const permanent = checkbox("Безстрокове блокування");
                    edit(
                      "Обмеження доступу",
                      [
                        el(
                          "p",
                          { class: "notice" },
                          "Студента буде прибрано з активних черг.",
                        ),
                        field("Кількість днів", days),
                        permanent.view,
                      ],
                      "Підтвердити",
                      (ctx) =>
                        send(
                          path,
                          {
                            action: permanent.node.checked ? "block" : "ban",
                            user_id: user.id,
                            days: Number(days.value),
                          },
                          ctx,
                        ),
                    );
                  }
                },
                "button secondary",
              ),
              btn(
                "Видалити",
                () =>
                  confirm(
                    "Видалити студента?",
                    user.full_name || user.name,
                    path,
                    { action: "delete", user_id: user.id },
                  ),
                "text-button danger",
              ),
            ),
      );
    const show = (query) =>
      roster.replaceChildren(
        ...users
          .filter((u) =>
            (u.full_name || u.name || "")
              .toLocaleLowerCase("uk-UA")
              .includes(query),
          )
          .map(userCard),
      );
    show("");
    const search = input({
      type: "search",
      required: false,
      placeholder: "Пошук за ПІБ",
      oninput: (event) =>
        show(event.target.value.trim().toLocaleLowerCase("uk-UA")),
    });
    return [
      card(
        form(
          [field("Додати студента без Telegram", fullName)],
          "Додати",
          (ctx) =>
            send(
              path,
              { action: "add", full_name: fullName.value.trim() },
              ctx,
            ),
        ),
      ),
      field("Студенти групи", search),
      users.length
        ? roster
        : el("p", { class: "notice" }, "У групі поки немає студентів."),
    ];
  }
  function subjectsPage(data) {
    const path = adminPath("admin-subjects"),
      name = input({ maxlength: 200 }),
      teacher = input({ maxlength: 200 });
    return [
      card(
        form(
          [field("Назва нового предмета", name), field("Викладач", teacher)],
          "Додати предмет",
          (ctx) =>
            send(
              path,
              {
                action: "add",
                name: name.value.trim(),
                teacher_name: teacher.value.trim(),
              },
              ctx,
            ),
        ),
      ),
      list(rows(data.subjects), (subject) =>
        card(
          el("h3", {}, subject.name),
          rows(subject.teachers).map((t) =>
            el(
              "div",
              { class: "button-row" },
              small(t.name),
              owner()
                ? btn(
                    "Прибрати викладача",
                    () =>
                      confirm("Прибрати викладача?", t.name, path, {
                        action: "remove_teacher",
                        subject_id: subject.id,
                        teacher_id: t.id,
                      }),
                    "text-button danger",
                  )
                : null,
            ),
          ),
          btn(
            "Додати викладача",
            () => {
              const teacherName = input({ maxlength: 200 });
              edit(
                "Викладач · " + subject.name,
                [field("ПІБ викладача", teacherName)],
                "Додати",
                (ctx) =>
                  send(
                    path,
                    {
                      action: "add_teacher",
                      subject_id: subject.id,
                      teacher_name: teacherName.value.trim(),
                    },
                    ctx,
                  ),
              );
            },
            "button secondary",
          ),
          owner()
            ? el(
                "div",
                { class: "button-row" },
                btn(
                  "Перейменувати",
                  () => {
                    const subjectName = input({
                      value: subject.name,
                      maxlength: 200,
                    });
                    edit(
                      "Перейменувати предмет",
                      [field("Нова назва", subjectName)],
                      "Зберегти",
                      (ctx) =>
                        send(
                          path,
                          {
                            action: "rename",
                            subject_id: subject.id,
                            name: subjectName.value.trim(),
                          },
                          ctx,
                        ),
                    );
                  },
                  "button secondary",
                ),
                btn(
                  "Видалити предмет",
                  () =>
                    confirm("Видалити предмет?", subject.name, path, {
                      action: "delete",
                      subject_id: subject.id,
                    }),
                  "text-button danger",
                ),
              )
            : null,
        ),
      ),
    ];
  }
  function saturdaysPage(data) {
    const path = adminPath("admin-saturdays"),
      date = input({ type: "date" });
    const dayNames = {
      monday: "Понеділок",
      tuesday: "Вівторок",
      wednesday: "Середа",
      thursday: "Четвер",
      friday: "П’ятниця",
    };
    const day = select(Object.entries(dayNames));
    const week = select([
      ["numerator", "Чисельник"],
      ["denominator", "Знаменник"],
    ]);
    return [
      card(
        form(
          [
            field("Робоча субота", date),
            field("Розклад за днем", day),
            field("Тип тижня", week),
          ],
          "Зберегти",
          (ctx) =>
            send(
              path,
              {
                action: "save",
                date: date.value,
                source_day: day.value,
                week_type: week.value,
              },
              ctx,
            ),
        ),
      ),
      list(rows(data.items), (item) =>
        card(
          el("h3", {}, item.date),
          small(
            (dayNames[item.source_day] || item.source_day) +
              " · " +
              (item.week_type === "denominator" ? "Знаменник" : "Чисельник"),
          ),
          el(
            "div",
            { class: "button-row" },
            btn(
              "Повідомити групу",
              () =>
                confirm(
                  "Надіслати повідомлення про суботу?",
                  item.date + " · " + item.source_day,
                  path,
                  { action: "notify", date: item.date },
                ),
              "button secondary",
            ),
            btn(
              "Видалити",
              () =>
                confirm("Видалити робочу суботу?", item.date, path, {
                  action: "delete",
                  date: item.date,
                }),
              "text-button danger",
            ),
          ),
        ),
      ),
    ];
  }
  async function readImage(file) {
    if (!/^image\/(jpeg|png|webp)$/.test(file.type))
      throw new Error("Підтримуються JPEG, PNG і WebP.");
    if (file.size > 3_000_000) throw new Error("Кожне фото має бути до 3 МБ.");
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error("Не вдалося прочитати фото."));
      reader.readAsDataURL(file);
    });
  }
  function replacementDraft(draft) {
    const path = adminPath("admin-replacements"),
      items = rows(draft.replacements || draft.items);
    const panel = sheet(
      "Перевір розпізнані заміни",
      "Розпізнавання ще нічого не зберегло. Виправ дані й підтвердь збереження та повідомлення.",
    );
    if (!items.length) {
      panel.append(
        el(
          "p",
          { class: "notice" },
          "Заміни не розпізнано. Спробуй чіткіше фото.",
        ),
      );
      return;
    }
    const fields = items.map((item) => {
      const date = input({
        type: "date",
        value: item.date || draft.date || "",
      });
      const lesson = input({
        type: "number",
        min: 1,
        max: 10,
        value: item.lesson_number || item.lesson || "",
      });
      const subject = input({
        value: item.new_subject || item.subject || "",
        maxlength: 300,
      });
      const oldSubject = input({
        value: item.old_subject || "",
        required: false,
        maxlength: 300,
      });
      const teacher = input({
        value: item.teacher || "",
        required: false,
        maxlength: 200,
      });
      const room = input({
        value: item.room || "",
        required: false,
        maxlength: 100,
      });
      const keep = checkbox("Зберегти цю заміну", true);
      return {
        item,
        date,
        lesson,
        subject,
        oldSubject,
        teacher,
        room,
        keep,
        view: card(
          keep.view,
          field("Дата", date),
          field("Номер пари", lesson),
          field("Замість предмета", oldSubject),
          field("Новий предмет", subject),
          field("Викладач", teacher),
          field("Аудиторія", room),
          item.source_note ? small(item.source_note) : null,
        ),
      };
    });
    panel.append(
      form(
        fields.map((f) => f.view),
        "Зберегти й повідомити",
        (ctx) => {
          const selected = fields.filter((f) => f.keep.node.checked);
          if (!selected.length) throw new Error("Обери хоча б одну заміну.");
          return send(
            path,
            {
              action: "save",
              items: selected.map((f) => ({
                ...f.item,
                date: f.date.value,
                lesson_number: Number(f.lesson.value),
                new_subject: f.subject.value.trim(),
                old_subject: f.oldSubject.value.trim(),
                teacher: f.teacher.value.trim(),
                room: f.room.value.trim(),
              })),
            },
            ctx,
          );
        },
      ),
    );
  }
  function replacementsPage(data) {
    const path = adminPath("admin-replacements"),
      files = input({
        type: "file",
        multiple: true,
        accept: "image/jpeg,image/png,image/webp",
      });
    const scheduleFile = input({
      type: "file",
      accept: "application/json,.json",
    });
    return [
      card(
        el(
          "p",
          { class: "notice" },
          "Завантаж 1–2 фото. Після розпізнавання перевір кожну заміну перед збереженням.",
        ),
        form([field("Фото замін", files)], "Розпізнати", async (ctx) => {
          const selected = [...files.files];
          if (selected.length < 1 || selected.length > 2)
            throw new Error("Обери одне або два фото.");
          const images = await Promise.all(selected.map(readImage));
          if (!current(ctx.token, ctx.epoch, ctx.key)) return;
          const result = await api(path, { action: "recognize", images });
          if (current(ctx.token, ctx.epoch, ctx.key))
            replacementDraft(result.draft || {});
        }),
      ),
      el("h2", {}, "Активні заміни"),
      list(rows(data.active), replacementCard),
      rows(data.active).length
        ? btn(
            "Очистити активні заміни",
            () =>
              confirm(
                "Очистити активні заміни?",
                "Вони більше не впливатимуть на розклад.",
                path,
                { action: "clear" },
              ),
            "text-button danger",
          )
        : null,
      el("h2", {}, "Архів"),
      list(rows(data.archived), replacementCard),
      card(
        el("h2", {}, "Файл розкладу"),
        H.download
          ? btn(
              "Завантажити розклад JSON",
              () =>
                externalAction(() =>
                  H.download(path + "?format=schedule", "pi231-schedule.json"),
                ),
              "button secondary",
            )
          : null,
        form(
          [field("Новий розклад JSON до 64 КБ", scheduleFile)],
          "Перевірити розклад",
          async (ctx) => {
            const file = scheduleFile.files?.[0];
            if (!file) throw new Error("Обери JSON-файл розкладу.");
            if (file.size > 64 * 1024)
              throw new Error("Розклад має бути до 64 КБ.");
            let parsed;
            try {
              parsed = JSON.parse(await file.text());
            } catch {
              throw new Error(
                "Не вдалося прочитати JSON. Перевір формат файлу.",
              );
            }
            if (!parsed || typeof parsed !== "object" || Array.isArray(parsed))
              throw new Error("Розклад має містити об’єкт із днями тижня.");
            if (!current(ctx.token, ctx.epoch, ctx.key)) return;
            const preview = sheet(
              "Замінити розклад?",
              "Перевір вміст. Після підтвердження цей розклад замінить поточний файл; заміни пар зберігаються окремо.",
            );
            preview.append(
              el(
                "pre",
                {
                  class: "schedule-json-preview",
                  style:
                    "max-height:320px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere",
                },
                JSON.stringify(parsed, null, 2),
              ),
              form([], "Підтвердити заміну розкладу", (submitCtx) =>
                send(
                  path,
                  { action: "import_schedule", schedule: parsed },
                  submitCtx,
                ),
              ),
            );
          },
        ),
      ),
    ];
  }
  function replacementCard(item) {
    return card(
      el("h3", {}, item.subject || item.new_subject || "Заміна"),
      small(
        [
          item.date,
          item.lesson_number && "Пара " + item.lesson_number,
          item.teacher,
          item.room,
        ]
          .filter(Boolean)
          .join(" · "),
      ),
    );
  }
  async function pollDetail(id) {
    const request = ++pollRequest;
    const ctx = { token: state.token, epoch: S.epoch, key: S.feature };
    try {
      const data = await api("/polls/" + encodeURIComponent(id));
      if (!current(ctx.token, ctx.epoch, ctx.key) || request !== pollRequest)
        return;
      const poll = data.poll,
        selected = new Set(rows(data.my_options)),
        panel = sheet(
          poll.question,
          poll.is_anonymous ? "Анонімне опитування" : "Відкрите опитування",
        );
      if (data.vote_channel_note)
        panel.append(el("p", { class: "notice" }, data.vote_channel_note));
      const options = rows(poll.options).map((option, index) => {
        const control = el("input", {
          type: poll.allows_multiple ? "checkbox" : "radio",
          name: "poll-vote",
          value: index,
          checked: selected.has(index),
          disabled: !data.can_vote,
        });
        const result = rows(data.results?.options).find(
          (item) => item.index === index,
        );
        const count = result?.count;
        const percent = Number.isFinite(result?.percent)
          ? Math.max(0, Math.min(100, result.percent))
          : 0;
        const voters = !poll.is_anonymous
          ? rows(result?.voters)
              .map((voter) =>
                typeof voter === "string"
                  ? voter
                  : voter.name || voter.full_name,
              )
              .filter(Boolean)
          : [];
        return {
          control,
          view: el(
            "div",
            { class: "poll-option-result" },
            field(
              option +
                (Number.isFinite(count)
                  ? " · " +
                    count +
                    (Number.isFinite(result.percent)
                      ? " (" + percent + "%)"
                      : "")
                  : ""),
              control,
            ),
            Number.isFinite(count) && Number.isFinite(result?.percent)
              ? el("meter", {
                  min: 0,
                  max: 100,
                  value: percent,
                  "aria-label": option + ": " + percent + "%",
                  style: "width:100%;height:12px",
                })
              : null,
            voters.length ? small(voters.join(", ")) : null,
          ),
        };
      });
      if (data.can_vote)
        panel.append(
          form(
            options.map((o) => o.view),
            "Проголосувати",
            async (voteCtx) => {
              const chosen = options
                .map((o, index) => (o.control.checked ? index : null))
                .filter((v) => v !== null);
              if (!chosen.length) throw new Error("Обери варіант відповіді.");
              await send(
                "/polls/" + encodeURIComponent(id) + "/vote",
                { options: chosen },
                voteCtx,
              );
            },
          ),
        );
      else
        panel.append(
          ...options.map((o) => o.view),
          el(
            "p",
            { class: "notice" },
            data.can_vote_reason ||
              data.reason ||
              "Голосування зараз недоступне.",
          ),
        );
      if (data.can_vote && selected.size)
        panel.append(
          form([], "Відкликати голос", (voteCtx) =>
            send(
              "/polls/" + encodeURIComponent(id) + "/vote",
              { options: [] },
              voteCtx,
            ),
          ),
        );
    } catch (err) {
      if (!accessError(err, ctx) && current(ctx.token, ctx.epoch, ctx.key))
        toast(err.message, true);
    }
  }
  function pollsPage(data, admin = false) {
    const path = adminPath("admin-polls"),
      content = [];
    if (admin) {
      const question = input({ maxlength: 300 }),
        options = text({
          placeholder: "Кожен варіант з нового рядка",
          maxlength: 3000,
        });
      const anonymous = checkbox("Анонімне", true),
        multiple = checkbox("Можна обирати кілька варіантів");
      const target = select([
        ["private", "Особисті повідомлення"],
        ["group", "Чат групи"],
      ]);
      const minutes = input({
        type: "number",
        min: 1,
        step: 1,
        required: false,
        placeholder: "Без обмеження",
      });
      content.push(
        card(
          el(
            "p",
            { class: "notice" },
            "Голосування відбудеться в мініапці. У Telegram буде надіслано запрошення.",
          ),
          form(
            [
              field("Запитання", question),
              field("Варіанти відповіді", options),
              anonymous.view,
              multiple.view,
              field("Куди надіслати запрошення", target),
              field("Тривалість, хвилин", minutes),
            ],
            "Створити й надіслати",
            (ctx) => {
              const values = options.value
                .split(/\r?\n/)
                .map((s) => s.trim())
                .filter(Boolean);
              if (values.length < 2 || values.length > 10)
                throw new Error("Додай від 2 до 10 варіантів.");
              return send(
                path,
                {
                  action: "create",
                  question: question.value.trim(),
                  options: values,
                  is_anonymous: anonymous.node.checked,
                  allows_multiple: multiple.node.checked,
                  target: target.value,
                  minutes: minutes.value ? Number(minutes.value) : null,
                },
                ctx,
              );
            },
          ),
        ),
      );
      const chat = input({
        type: "text",
        inputmode: "numeric",
        pattern: "-?[0-9]+",
        placeholder: "Telegram ID чату",
      });
      content.push(
        card(
          form(
            [field("Чат групи для опитувань", chat)],
            "Зберегти чат",
            (ctx) =>
              send(path, { action: "group", chat_id: Number(chat.value) }, ctx),
          ),
        ),
      );
    }
    content.push(
      list(rows(data.items || data.polls), (poll) =>
        card(
          el("h3", {}, poll.question),
          badge(statusText(poll.status)),
          small(poll.is_anonymous ? "Анонімне" : "Відкрите"),
          poll.closes_at
            ? small(
                "До " +
                  new Date(poll.closes_at).toLocaleString("uk-UA", {
                    timeZone: "Europe/Kyiv",
                  }),
              )
            : null,
          btn(
            "Відкрити опитування",
            () => pollDetail(poll.id),
            "button secondary",
          ),
          admin && !["closed", "finished"].includes(poll.status)
            ? btn(
                "Закрити голосування",
                () =>
                  confirm("Закрити опитування?", poll.question, path, {
                    action: "close",
                    poll_id: poll.id,
                  }),
                "text-button danger",
              )
            : null,
        ),
      ),
    );
    return content;
  }
  function reportsPage(data) {
    const subjects = select([
      ["", "Вся група"],
      ...rows(data.subjects).map((s) => [s.id, s.name]),
    ]);
    subjects.value =
      new URLSearchParams(state.query || "").get("subject_id") || "";
    subjects.addEventListener("change", () =>
      open(
        "admin-reports",
        false,
        subjects.value
          ? "?subject_id=" + encodeURIComponent(subjects.value)
          : "",
      ),
    );
    const statistics = data.statistics || {},
      content = [field("Предмет звіту", subjects)];
    if (data.subject_report) content.push(subjectReport(data.subject_report));
    else {
      const view = el("div", { class: "report-view" });
      const tabs = el("div", {
        class: "segments",
        role: "tablist",
        "aria-label": "Подання звіту",
      });
      const views = {
        summary: () =>
          el(
            "div",
            { class: "feature-metrics" },
            numberCard("Зареєстрованих студентів", statistics.registered_users),
            numberCard("Студентів без Telegram", statistics.placeholder_users),
            numberCard("Активних сесій", statistics.active_sessions),
            numberCard("Архівних сесій", statistics.archived_sessions),
            numberCard("Успішність", statistics.pass_rate, "%"),
            donut("Типи черг", [
              ["Звичайні пари", statistics.lessons_count],
              ["Консультації", statistics.consults_count],
              ["Курсові", statistics.cw_count],
            ]),
            donut("Стани записів", [
              ["Здано", statistics.total_passed],
              ["Не здано / перенесено", statistics.total_removed],
              ["Вихід із черги", statistics.total_left],
              ["Активні", statistics.total_active],
            ]),
          ),
        subjects: () =>
          chart(
            "Здано за предметами",
            rows(statistics.subjects).map((item) => [item.name, item.passed]),
            true,
          ),
        students: () =>
          chart(
            "Здано студентами",
            rows(statistics.top_students).map((item) => [
              item.name,
              item.passed,
            ]),
            false,
          ),
      };
      const show = (key) => {
        view.replaceChildren(views[key]());
        for (const node of tabs.children) {
          const active =
            node.dataset?.reportView === key ||
            node.getAttribute?.("data-report-view") === key;
          node.setAttribute("aria-selected", String(active));
          node.classList.toggle("selected", active);
        }
      };
      for (const [key, label] of [
        ["summary", "Огляд"],
        ["subjects", "Предмети"],
        ["students", "Студенти"],
      ]) {
        const tab = btn(label, () => show(key), "button secondary");
        tab.setAttribute("role", "tab");
        tab.setAttribute("data-report-view", key);
        tabs.append(tab);
      }
      show("summary");
      content.push(tabs, view);
    }
    if (H.download)
      content.push(
        btn(
          subjects.value ? "CSV обраного предмета" : "CSV усієї групи",
          () =>
            externalAction(() =>
              H.download(
                adminPath("admin-reports") +
                  "?format=csv" +
                  (subjects.value
                    ? "&subject_id=" + encodeURIComponent(subjects.value)
                    : ""),
                subjects.value
                  ? "pi231-subject-" + subjects.value + ".csv"
                  : "pi231-report.csv",
              ),
            ),
          "button secondary",
        ),
      );
    return content;
  }
  const count = (value) =>
    typeof value === "number" && Number.isFinite(value) && value >= 0
      ? value
      : 0;
  function numberCard(label, value, suffix = "") {
    return card(
      small(label),
      el("strong", { class: "report-number" }, count(value) + suffix),
    );
  }
  function donut(title, values) {
    const colors = ["#3F68B8", "#51AD93", "#D6A34D", "#9979C7"],
      total = values.reduce((sum, [, value]) => sum + count(value), 0);
    if (!total)
      return card(
        el("h2", {}, title),
        el("p", { class: "notice" }, "Немає даних для діаграми."),
      );
    let offset = 0;
    const stops = values.map(([, value], index) => {
      const start = offset;
      offset += (count(value) / total) * 100;
      return colors[index % colors.length] + " " + start + "% " + offset + "%";
    });
    return card(
      el("h2", {}, title),
      el(
        "div",
        {
          class: "report-donut",
          role: "img",
          "aria-label":
            title +
            ": " +
            values.map(([name, value]) => name + " " + count(value)).join(", "),
          style:
            "width:160px;height:160px;border-radius:50%;background:conic-gradient(" +
            stops.join(",") +
            ");display:grid;place-items:center;margin:16px auto",
        },
        el(
          "span",
          {
            style:
              "display:grid;place-items:center;width:104px;height:104px;border-radius:50%;background:var(--surface,#171A1F);font-size:24px;font-weight:700",
          },
          total,
        ),
      ),
      values.map(([label, value], index) =>
        el(
          "p",
          {},
          el(
            "span",
            {
              "aria-hidden": "true",
              style: "color:" + colors[index % colors.length],
            },
            "● ",
          ),
          label +
            ": " +
            count(value) +
            " · " +
            Math.round((count(value) / total) * 100) +
            "%",
        ),
      ),
    );
  }
  function chart(title, values, vertical) {
    const max = Math.max(1, ...values.map(([, value]) => count(value)));
    if (!values.length || !values.some(([, value]) => count(value)))
      return card(
        el("h2", {}, title),
        el("p", { class: "notice" }, "Поки немає зданих робіт."),
      );
    return card(
      el("h2", {}, title),
      el(
        "div",
        {
          class: vertical ? "report-columns" : "report-bars",
          style: vertical
            ? "display:flex;align-items:flex-end;gap:16px;overflow-x:auto;padding:12px 0"
            : "display:grid;gap:12px",
        },
        values.map(([label, value]) => {
          const amount = count(value),
            percentage = (amount / max) * 100;
          return vertical
            ? el(
                "div",
                {
                  style:
                    "flex:1;min-width:88px;max-width:180px;text-align:center",
                },
                el("strong", {}, amount),
                el(
                  "div",
                  {
                    style:
                      "height:160px;display:flex;align-items:flex-end;justify-content:center",
                    role: "img",
                    "aria-label": label + ": " + amount,
                  },
                  el("div", {
                    style:
                      "width:44px;min-height:2px;border-radius:8px 8px 0 0;background:#3F68B8;height:" +
                      percentage +
                      "%",
                  }),
                ),
                small(label),
              )
            : el(
                "div",
                {},
                el(
                  "div",
                  { class: "button-row" },
                  el("span", {}, label),
                  el("strong", {}, amount),
                ),
                el(
                  "div",
                  {
                    role: "img",
                    "aria-label": label + ": " + amount,
                    style:
                      "height:12px;background:rgba(127,127,127,.15);border-radius:8px;overflow:hidden",
                  },
                  el("div", {
                    style:
                      "height:100%;background:#3F68B8;width:" +
                      percentage +
                      "%",
                  }),
                ),
              );
        }),
      ),
    );
  }
  function subjectReport(report) {
    const columns = [
      "Студент",
      "Здано",
      "Не здано",
      "Поточне місце",
      "Дати здачі",
    ];
    return card(
      el("h2", {}, "Звіт за предметом"),
      el(
        "div",
        { class: "feature-metrics" },
        numberCard("Сесій", report.sessions_count),
        numberCard("Усього здано", report.total_passed),
      ),
      rows(report.students).length
        ? el(
            "div",
            { style: "overflow-x:auto" },
            el(
              "table",
              { class: "report-table" },
              el("caption", {}, "Успішність студентів"),
              el(
                "thead",
                {},
                el(
                  "tr",
                  {},
                  columns.map((label) => el("th", { scope: "col" }, label)),
                ),
              ),
              el(
                "tbody",
                {},
                rows(report.students).map((student) =>
                  el(
                    "tr",
                    {},
                    [
                      student.name,
                      count(student.passed_count),
                      count(student.not_passed_count),
                      student.current_position ?? "—",
                      rows(student.dates).join(", ") || "—",
                    ].map((value) => el("td", {}, value)),
                  ),
                ),
              ),
            ),
          )
        : el("p", { class: "notice" }, "Для цього предмета ще немає записів."),
    );
  }
  function ownerPage(data) {
    const path = adminPath("owner"),
      announcement = text({ maxlength: 4000 });
    const announcementForm = form(
      [field("Текст оголошення", announcement)],
      "Перевірити й надіслати",
      async () => {
        const message = announcement.value.trim();
        confirm("Надіслати оголошення всім?", message, path, {
          action: "announce",
          text: message,
        });
      },
    );
    const active = checkbox("Технічні роботи увімкнено", data.maintenance),
      end = input({ type: "time", value: data.end_time || "20:15" });
    return [
      card(el("h2", {}, "Оголошення"), announcementForm),
      card(
        el("h2", {}, "Технічні роботи"),
        form(
          [active.view, field("Орієнтовне завершення", end)],
          "Підтвердити зміну",
          async () =>
            confirm(
              "Змінити режим технічних робіт?",
              active.node.checked
                ? "Доступ студентів буде тимчасово обмежено."
                : "Доступ студентів буде відновлено.",
              path,
              {
                action: "maintenance",
                active: active.node.checked,
                end_time: end.value.trim() || null,
              },
            ),
        ),
      ),
      ...[
        ["admin-admins", "Адміністратори"],
        ["admin-bugs", "Звернення"],
        ["admin-proposals", "Пропозиції"],
      ].map(([key, title]) => btn(title, () => open(key), "button secondary")),
    ];
  }
  function adminsPage(data) {
    const path = adminPath("admin-admins"),
      id = input({
        type: "text",
        inputmode: "numeric",
        pattern: "[1-9][0-9]*",
      });
    return [
      card(
        form(
          [field("Telegram ID нового адміністратора", id)],
          "Додати адміністратора",
          (ctx) =>
            send(path, { action: "add", telegram_id: Number(id.value) }, ctx),
        ),
      ),
      list(rows(data.items || data.admins), (admin) => {
        const telegramId =
          typeof admin === "object" ? admin.telegram_id : admin;
        return card(
          el("strong", {}, "Telegram ID: " + telegramId),
          btn(
            "Зняти права",
            () =>
              confirm("Зняти права адміністратора?", String(telegramId), path, {
                action: "remove",
                telegram_id: telegramId,
              }),
            "text-button danger",
          ),
        );
      }),
    ];
  }
  function helpPage() {
    return [
      card(
        el("h2", {}, "Як стати в чергу"),
        el(
          "p",
          {},
          "Відкрий предмет у «Чергах» і натисни «Стати в чергу». Для лабораторних обери роботи та приблизний час. Для консультації вибирати лабораторні не потрібно.",
        ),
      ),
      card(
        el("h2", {}, "Здача й обміни"),
        el(
          "p",
          {},
          "Натисни «Здав» після здачі. Якщо вибрано кілька робіт, познач фактично здані: з рештою залишишся на своєму місці. Для обміну натисни на іншого учасника. На відповідь є 10 хвилин; погоджений обмін можна повторити.",
        ),
      ),
      card(
        el("h2", {}, "Розклад і час"),
        el(
          "p",
          {},
          "Заміни й робочі суботи враховуються в розкладі. Приблизний час у черзі допомагає планувати здачу та не гарантує завершення до кінця пари.",
        ),
      ),
      btn(
        "Запропонувати предмет або викладача",
        () => open("proposals"),
        "button secondary",
      ),
      btn("Повідомити про помилку", () => open("bugs"), "button secondary"),
    ];
  }
  function page(key) {
    const main = el(
      "main",
      { class: "feature-page" },
      btn(
        "Назад",
        () => {
          if (S.busy) return;
          state.token++;
          S.feature = null;
          navigate(
            key.startsWith("admin-") || key === "owner" ? "admin" : "queues",
          );
        },
        "back-button",
      ),
      el("div", { class: "page-title" }, el("h1", {}, titles[key] || "Розділ")),
    );
    if (!allowed(key)) {
      main.append(
        el(
          "p",
          { class: "notice", role: "alert" },
          "Недостатньо прав доступу.",
        ),
      );
      return main;
    }
    if (state.key !== key || state.loading) {
      main.append(
        el(
          "div",
          { class: "loading", role: "status" },
          el("span", { class: "spinner" }),
          "Завантаження…",
        ),
      );
      return main;
    }
    if (state.error) {
      main.append(
        el("p", { class: "notice error", role: "alert" }, state.error),
        btn("Спробувати знову", () => open(key), "button primary"),
      );
      return main;
    }
    const data = state.data || {};
    const screens = {
      create: createPage,
      proposals: proposalsPage,
      bugs: bugsPage,
      help: helpPage,
      polls: pollsPage,
      "admin-history": historyPage,
      "admin-group": groupPage,
      "admin-subjects": subjectsPage,
      "admin-replacements": replacementsPage,
      "admin-saturdays": saturdaysPage,
      "admin-reports": reportsPage,
      "admin-polls": (d) => pollsPage(d, true),
      owner: ownerPage,
      "admin-admins": adminsPage,
      "admin-bugs": (d) => bugsPage(d, true),
      "admin-proposals": (d) => proposalsPage(d, true),
    };
    main.append(
      ...(screens[key]?.(data) || []).filter(
        (node) => node !== null && node !== undefined && node !== false,
      ),
    );
    return main;
  }
  return { open, page, openPoll: pollDetail };
};
