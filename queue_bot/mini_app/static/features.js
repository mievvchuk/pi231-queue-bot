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
    help: "Допомога",
    journal: "Мої оцінки",
    "admin-history": "Активні черги",
    "admin-group": "Група",
    "admin-subjects": "Предмети й викладачі",
    "admin-replacements": "Заміни",
    "admin-saturdays": "Робочі суботи",
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
  let state = { key: null, loading: false, data: null, error: null, token: 0 };
  let saturdayTicker = null;
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
      archived: "Завершено",
      completed: "Здав",
      transferred: "Перенесено в іншу чергу",
      removed: "Не здав / перенесено",
      left: "Вийшов із черги",
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
            : "/" + key + query,
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
  function historyTime(value) {
    if (!value) return 'Дата не вказана';
    let text=String(value).trim().replace(' ', 'T').replace(/(\.\d{3})\d+/, '$1');
    const dateOnly=/^\d{4}-\d{2}-\d{2}$/.test(text);
    if (dateOnly) text+='T12:00:00Z';
    else if (!/(Z|[+-]\d{2}:?\d{2})$/i.test(text)) text+='Z';
    const date=new Date(text);
    return Number.isNaN(date.getTime()) ? 'Дата не вказана' : new Intl.DateTimeFormat('uk-UA', {timeZone:'Europe/Kyiv',day:'2-digit',month:'2-digit',year:'numeric',...(dateOnly?{}:{hour:'2-digit',minute:'2-digit'})}).format(date);
  }
  function historyPage(data) {
    const caption=el('p',{class:'history-timezone'},'Дата й час за Києвом');
    const sessionMeta=session=>small([session?.teacher,historyTime(session?.date),session?.status==='archived'?'Архів':null,H.pairPlanText?.(session)].filter(Boolean).join(' · '));
    if (data.session_id != null) {
      const session=rows(data.sessions).find(item=>String(item.id)===String(data.session_id));
      const people=new Map();
      for(const entry of rows(data.entries)) {
        const key=entry.user_id ?? entry.full_name ?? entry.name ?? entry.id;
        if(!people.has(key))people.set(key,[]);
        people.get(key).push(entry);
      }
      const participants=[...people.values()].map(attempts=>{
        const latest=attempts[attempts.length-1],active=attempts.find(e=>e.status==='active');
        return el('section',{class:'card request-card history-person'},
          el('h3',{},latest.full_name||latest.name||'Студент'),
          el('div',{class:'history-person-summary'},badge(statusText(active?.status||latest.status)),active?.position?small('Місце №'+active.position):null,small('Записів: '+attempts.length)),
          small('Остання дія: '+historyTime(latest.completed_at||latest.created_at)),
          el('details',{class:'history-attempts'},el('summary',{},'Історія записів · '+attempts.length),
            ...attempts.map((entry,index)=>el('div',{class:'history-attempt'},el('strong',{},'Запис '+(index+1)+' · '+statusText(entry.status)),
              small('Додано: '+historyTime(entry.created_at)),entry.completed_at?small('Завершено: '+historyTime(entry.completed_at)):null,
              entry.labs_passed?small('Здано лабораторних: '+entry.labs_passed):null))));
      });
      return [btn('До списку сесій',()=>open('admin-history'),'button secondary'),caption,
        card(el('h2',{},session?.subject||'Обрана сесія'),sessionMeta(session),el('h3',{},'Учасники сесії · '+people.size),
          el('div',{class:'history-participants'},...(participants.length?participants:[el('p',{class:'empty'},'У цій сесії ще немає учасників.')])) )];
    }
    const groups=new Map();
    for(const session of rows(data.sessions)){
      const key=session.subject_id??session.subject;
      if(!groups.has(key))groups.set(key,{name:session.subject,sessions:[]});
      groups.get(key).sessions.push(session);
    }
    return [caption,el('div',{class:'history-subjects'},...[...groups.values()].sort((a,b)=>(a.name||'').localeCompare(b.name||'','uk')).map(group=>
      el('section',{class:'history-subject'},el('h2',{},group.name+' · '+group.sessions.length),
        ...group.sessions.sort((a,b)=>String(b.date||'').localeCompare(String(a.date||''))).map(item=>
          el('article',{class:'card request-card history-session'},sessionMeta(item),
            el('div',{class:'button-row'},btn('Учасники сесії',()=>open('admin-history',false,'?sid='+encodeURIComponent(item.id)),'button secondary'),
              null))))))];
  }
  function groupPage(data) {
    const path = adminPath("admin-group"),
      fullName = input({ maxlength: 200 });
    const users = rows(data.users || data.items),
      roster = el("div", { class: "student-roster" });
    const userCard = (user) => {
      const node = card(
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
      const children=[...node.children];
      const avatar=el('span',{class:'student-avatar','aria-hidden':'true'},el('span',{},(user.full_name||user.name||'Студент').split(' ').filter(Boolean).map(n=>n[0]).slice(0,2).join('')));
      const identity=el('div',{class:'student-identity','data-student-id':user.id,'data-telegram-id':user.telegram_id},avatar,
        el('div',{class:'student-copy'},children[0],el('span',{class:'student-username'},user.telegram_id>0?'Завантаження Telegram…':'Без Telegram'),
          small(user.telegram_id>0?'Telegram ID: '+user.telegram_id:'Додано вручну · Telegram ще не підключено'),
          el('div',{class:'student-flags'},children[2],user.protected?children[3]:null)));
      if(!user.protected)children[3]?.setAttribute('class','button-row student-actions');
      node.setAttribute('class','card request-card student-card');
      node.replaceChildren(identity,...(!user.protected?children.slice(3):[]));
      return node;
    };
    const show = (query) => {
      roster.replaceChildren(
        ...users
          .filter((u) =>
            (u.full_name || u.name || "")
              .toLocaleLowerCase("uk-UA")
              .includes(query),
          )
          .map(userCard),
      );
      H.enrichStudents?.(roster);
    };
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
    let active = [...rows(data.items)], archived = [...rows(data.archived)];
    const activeList = el('div', {class:'list feature-list'});
    const archiveList = el('div', {class:'list feature-list'});
    const archiveSummary = el('summary',{});
    const archivePanel = el('details',{class:'saturday-archive card'},archiveSummary,archiveList);
    const offset = data.server_time ? Date.parse(data.server_time)-Date.now() : 0;
    const description = item => (dayNames[item.source_day] || item.source_day) + ' · ' +
      (item.week_type==='denominator'?'Знаменник':item.week_type==='numerator'?'Чисельник':'Автоматично');
    function drawLists() {
      activeList.replaceChildren(...(active.length ? active.map(item => card(
        el('h3',{},item.date),small(description(item)),
        el('div',{class:'button-row'},
          btn('Повідомити групу',()=>confirm('Надіслати повідомлення про суботу?',item.date+' · '+item.source_day,path,{action:'notify',date:item.date}),'button secondary'),
          btn('Видалити',()=>confirm('Видалити робочу суботу?',item.date,path,{action:'delete',date:item.date}),'text-button danger'),
        ),
      )) : [el('p',{class:'notice saturday-empty'},'Немає активних робочих субот.')]));
      archived.sort((a,b)=>b.date.localeCompare(a.date));
      archiveList.replaceChildren(...archived.map(item=>card(el('h3',{},item.date),badge('Завершено'),small(description(item)))));
      archiveSummary.textContent = 'Архів робочих субот · '+archived.length;
      archivePanel.hidden = archived.length===0;
    }
    saturdayTicker = () => {
      const ended = active.filter(item=>item.archive_at && Date.parse(item.archive_at)<=Date.now()+offset);
      if (!ended.length) return;
      active=active.filter(item=>!ended.includes(item));
      archived.push(...ended);
      drawLists();
    };
    drawLists();
    saturdayTicker();
    return [
      card(
        small(
          "Після 13:30 у день суботи запис автоматично переходить до архіву.",
        ),
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
      el('h2',{},'Активні робочі суботи'),
      activeList,
      archivePanel,
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
    return [
      card(el("p", { class: "notice" }, "Заміни надходять автоматично з каналу. Підтвердження й редагування до та після збереження доступні у чаті з ботом.")),
      rows(data.confirmed).filter((item) => item.no_replacements).map((item) =>
        el("p", { class: "notice" }, item.message)),
      el("h2", {}, "Активні заміни"),
      list(rows(data.active), replacementCard),
      card(el("p", { class: "notice" }, "Базовий розклад можна змінити в чаті з ботом командою /edit_schedule.")),
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
        const name = typeof admin === "object" && (admin.full_name || admin.name);
        return card(
          el("strong", {}, name || "Профіль ще не зареєстрований"),
          small("Telegram ID: " + telegramId),
          btn(
            "Зняти права",
            () =>
              confirm("Зняти права адміністратора?", (name ? name + " · " : "") + telegramId, path, {
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
          "Натисни «Здав» після здачі. Якщо вибрано кілька робіт, познач фактично здані: поки продовжуєш підхід, місце зберігається. Після «Завершити підхід» решта робіт переходить у кінець черги. Для обміну натисни на іншого учасника. На відповідь є 10 хвилин; погоджений обмін можна повторити.",
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
  function personalJournalPage(data) {
    const allSubjects=rows(data.subjects), viewer=data.viewer_id;
    const coursework=allSubjects.filter(s=>/курсова|курсовий/i.test(s.name));
    const kpz=allSubjects.find(s=>/конструювання/i.test(s.name));
    const subjects=allSubjects.filter(s=>!kpz||!coursework.includes(s));
    const params=new URLSearchParams(state.query||'');
    let selected=subjects.some(s=>s.key===params.get('subject'))?params.get('subject'):'';
    let editing=null;
    const content=el('section',{class:'personal-journal','aria-label':'Мої оцінки'});
    const own=()=>rows(data.records).filter(r=>r.user_id===viewer);
    const recordsFor=key=>own().filter(r=>r.subject_key===key||(key===kpz?.key&&coursework.some(s=>s.key===r.subject_key)));
    const period=select(rows(data.periods).map(p=>[p,p.replace(':',' · семестр ')]),data.period);
    const query=key=>'?period='+encodeURIComponent(data.period)+(key?'&subject='+encodeURIComponent(key):'');
    const route=key=>{
      selected=key;editing=null;state.query=query(key);
      if(typeof location!=='undefined'&&typeof history!=='undefined') {
        const url=new URL(location.href);url.searchParams.set('page','journal');url.searchParams.set('period',data.period);
        key?url.searchParams.set('subject',key):url.searchParams.delete('subject');
        history.pushState({},'',url);
      }
      draw();
      if(typeof window.scrollTo==='function')window.scrollTo(0,0);
    };
    period.addEventListener('change',()=>{
      if(typeof location!=='undefined'&&typeof history!=='undefined'){
        const url=new URL(location.href);url.searchParams.set('page','journal');url.searchParams.set('period',period.value);
        selected?url.searchParams.set('subject',selected):url.searchParams.delete('subject');history.pushState({},'',url);
      }
      open('journal',true,'?period='+encodeURIComponent(period.value)+(selected?'&subject='+encodeURIComponent(selected):''));
    });
    const mark=value=>value==null?'Не внесено':String(value);
    const works=subject=>{
      const result=[];
      const sources=[subject,...(subject.key===kpz?.key?coursework:[])];
      for(const source of sources)for(const lab of rows(source.labs))for(let part=1;part<=lab.parts;part++)result.push({subject_key:source.key,kind:'lab',number:lab.number,part,title:(source===subject?(lab.title||'Лабораторна '+lab.number):source.name)+(lab.parts>1?' · оцінка '+part:''),optional:lab.optional_last&&part===lab.parts});
      for(const r of recordsFor(subject.key))if(!result.some(w=>w.subject_key===r.subject_key&&w.kind===r.kind&&w.number===r.number&&w.part===(r.part||1)))
        result.push({subject_key:r.subject_key,kind:r.kind,number:r.number,part:r.part||1,title:(r.kind==='lab'?'Лабораторна ':'Робота ')+r.number+' · попередній запис',legacy:true});
      return result;
    };
    const itemFor=(subject,work)=>recordsFor(subject.key).find(r=>r.subject_key===work.subject_key&&r.kind===work.kind&&r.number===work.number&&(r.part||1)===work.part);
    function workCard(subject,work,index) {
      const record=itemFor(subject,work), editor=editing===index;
      const cardView=el('article',{class:'personal-work'},
        el('div',{class:'personal-work-heading'},el('div',{},el('h3',{},work.title),work.optional?small('За потреби'):null),
          el('span',{class:record?'personal-grade':'personal-missing'},mark(record?.grade))));
      if(record?.note)cardView.append(el('p',{class:'muted'},record.note));
      if(editor) {
        const grade=input({type:'number',inputmode:'decimal',min:0,max:5,step:'0.1',value:record?.grade??'', 'aria-label':'Оцінка від 0 до 5'});
        const note=input({required:false,maxlength:500,value:record?.note||'',placeholder:'За бажанням'});
        const editorForm=form([field('Оцінка від 0 до 5',grade),field('Примітка',note)],'Зберегти',async ctx=>{
          const value=Number(String(grade.value).replace(',','.'));
          if(String(grade.value).trim()===''||!Number.isFinite(value)||value<0||value>5)throw new Error('Введи оцінку від 0 до 5.');
          await api('/journal',{action:'submit',period:data.period,user_id:viewer,subject_key:work.subject_key,kind:work.kind,number:work.number,part:work.part,grade:value,note:note.value.trim(),...(record?{record_id:record.id,revision:record.revision}:{})});
          const fresh=await api('/journal'+query(selected));
          if(!current(ctx.token,ctx.epoch,ctx.key))return;
          Object.assign(data,fresh);editing=null;draw();toast('Оцінку збережено');
        });
        editorForm.append(btn('Скасувати',()=>{if(!S.busy){editing=null;draw();}},'button secondary'));
        cardView.append(editorForm);
      } else if(record?.can_edit!==false&&(!work.legacy||record))cardView.append(btn(record?'Редагувати':'Внести оцінку',()=>{editing=index;draw();},'button secondary'));
      return cardView;
    }
    function draw() {
      const subject=subjects.find(s=>s.key===selected);
      const toolbar=el('div',{class:'personal-journal-toolbar'},subject?btn('← Усі предмети',()=>route(''),'button secondary'):small('Твої оцінки за предметами'),(rows(data.periods).length>1?field('Навчальний період',period):small(data.period.replace(':',' · семестр '))),H.isAdmin()?btn('Журнал групи',()=>open('journal',false,'?period='+encodeURIComponent(data.period)+'&scope=group'),'button secondary'):null);
      if(!subject) {
        const cards=subjects.map(s=>{
          const ownRecords=recordsFor(s.key), configured=works(s), filled=configured.filter(w=>itemFor(s,w)).length;
          const latest=[...ownRecords].sort((a,b)=>(Date.parse(b.updated_at||b.created_at)||0)-(Date.parse(a.updated_at||a.created_at)||0))[0];
          return el('button',{type:'button',class:'personal-subject-card',onclick:()=>route(s.key)},el('h2',{},s.name),
            small('Внесено оцінок: '+filled+' із '+configured.length),latest?el('p',{},'Остання оцінка: ',el('strong',{},String(latest.grade))):null,el('span',{class:'personal-subject-open'},'Відкрити →'));
        });
        content.replaceChildren(toolbar,el('div',{class:'personal-subject-grid'},...cards));
        if(!subjects.length)content.append(el('p',{class:'notice'},'Предметів поки немає.'));
        return;
      }
      const summary=data.averages?.[viewer]?.[subject.key];
      const averages=el('div',{class:'personal-averages'},...[1,2].map(n=>{
        const average=summary?.[String(n)];
        const due=rows(data.dates).find(d=>d.subject_key===subject.key&&d.number===n)?.due_date;
        return el('div',{},small('Атестація '+n),el('strong',{},average?.average!=null?Number(average.average).toFixed(2):'Не внесено'),due?small('Дата: '+due):null);
      }));
      const subjectNav=el('nav',{class:'personal-subject-nav','aria-label':'Мої предмети'},...subjects.map(s=>el('button',{type:'button','aria-current':s.key===selected?'page':null,onclick:()=>route(s.key)},s.name)));
      const workList=works(subject);
      content.replaceChildren(toolbar,el('div',{class:'personal-journal-layout'},subjectNav,el('div',{class:'personal-journal-detail'},
        el('h2',{},subject.name),averages,el('div',{class:'personal-work-list'},...workList.map((w,index)=>workCard(subject,w,index))),
        !workList.length?el('p',{class:'notice'},'Роботи для цього предмета ще не налаштовані.'):null)));
    }
    draw();return [content];
  }

  let journalSubject='';
  function journalPage(data) {
    if(!data.group)return personalJournalPage(data);
    const query = (review=data.group,period=data.period) => '?period='+encodeURIComponent(period)+(review?'&scope=group':'');
    const save = async (values, ctx) => {
      await send('/journal', values, ctx, false);
      if(current(ctx.token,ctx.epoch,ctx.key))await open('journal',true,query());
    };
    const period=select(rows(data.periods).map(p=>[p,p.replace(':',' · семестр ')]),data.period);
    period.addEventListener('change',()=>open('journal',false,query(data.group,period.value)));
    const subjects=rows(data.subjects);
    const requested=new URLSearchParams(state.query||'').get('subject');
    const initial=subjects.some(s=>s.key===requested)?requested:subjects.some(s=>s.key===journalSubject)?journalSubject:subjects.find(s=>/конструювання/i.test(s.name))?.key||subjects[0]?.key||'';
    const subjectFilter={value:initial};
    const pick=key=>{
      subjectFilter.value=journalSubject=key;
      const params=new URLSearchParams(state.query||'');params.set('subject',key);params.set('scope','group');state.query='?'+params.toString();
      if(typeof location!=='undefined'&&typeof history!=='undefined'){
        const url=new URL(location.href);url.searchParams.set('page','journal');url.searchParams.set('subject',key);url.searchParams.set('period',data.period);history.replaceState({},'',url);
      }
      draw();
    };
    const subjectSelect=select(subjects.map(s=>[s.key,s.name]),initial);
    subjectSelect.setAttribute('aria-label','Предмет журналу групи');
    subjectSelect.addEventListener('change',()=>pick(subjectSelect.value));
    const search=input({type:'search',required:false,placeholder:'Прізвище або ім’я','aria-label':'Пошук студента'});
    const tables=el('div',{class:'journal-tables'});
    const tone = value => value===undefined||value===''?'':Number(value)===5?' grade-five':Number(value)<3?' grade-low':'';
    const grade = value => el('span',{class:'journal-grade'+tone(value),'aria-label':'Оцінка '+value+' із 5'},String(value));
    // Grades are typed straight into the cell and saved on change, no dialog or approval.
    const saveInline=async(student,subject,column,record,box)=>{
      const raw=box.value.trim().replace(',','.');
      const restore=()=>{box.value=record?.grade??'';};
      if(raw===''||(record&&Number(raw)===Number(record.grade)))return restore();
      const value=Number(raw);
      if(!Number.isFinite(value)||value<0||value>5){restore();return toast('Оцінка має бути від 0 до 5.',true);}
      box.disabled=true;
      try{
        await api('/journal',{action:'submit',period:data.period,user_id:student.id,subject_key:subject.key,kind:column.kind,number:column.number,part:column.part,grade:value,note:record?.note||'',...(record?{record_id:record.id,revision:record.revision}:{})});
        const fresh=await api('/journal'+query());
        if(!tables.isConnected)return;
        const focused=document.activeElement?.dataset?.cell;
        Object.assign(data,fresh);draw();
        if(focused)tables.querySelector('[data-cell="'+focused+'"]')?.focus();
      }catch(err){restore();toast(err.message,true);}
      finally{box.disabled=false;}
    };
    // Spreadsheet-style keys: arrows jump between grade cells, Enter saves.
    const moveFocus=e=>{
      const box=e.target;
      if(e.key==='Enter'){e.preventDefault?.();return box.blur();}
      const atStart=box.selectionStart===0&&box.selectionEnd===0, atEnd=box.selectionStart===box.value.length;
      const whole=box.selectionStart===0&&box.selectionEnd===box.value.length;
      const cell=box.closest('td'), row=cell?.parentElement;
      if(!row)return;
      const inputOf=td=>td?.querySelector('.journal-input');
      let next=null;
      if(e.key==='ArrowLeft'&&(atStart||whole)){for(let td=cell.previousElementSibling;td&&!next;td=td.previousElementSibling)next=inputOf(td);}
      else if(e.key==='ArrowRight'&&(atEnd||whole)){for(let td=cell.nextElementSibling;td&&!next;td=td.nextElementSibling)next=inputOf(td);}
      else if(e.key==='ArrowUp'||e.key==='ArrowDown'){
        const step=e.key==='ArrowUp'?'previousElementSibling':'nextElementSibling';
        for(let tr=row[step];tr&&!next;tr=tr[step])next=inputOf(tr.children[cell.cellIndex]);
      }else return;
      if(!next)return;
      e.preventDefault?.();next.focus();
    };
    const groupsFor=subject=>{
      const labs=rows(subject.labs).length?subject.labs:[{number:1,parts:1}];
      const groups=labs.map(l=>({kind:'lab',number:l.number,parts:l.parts,title:l.title||'ЛР '+l.number,optional_last:l.optional_last,attestation:l.attestation}));
      for(const record of rows(data.records).filter(r=>r.subject_key===subject.key&&r.kind==='lab')){
        let group=groups.find(g=>g.number===record.number);
        if(!group){group={kind:'lab',number:record.number,parts:record.part||1,title:'ЛР '+record.number+' (раніше)',legacy:true};groups.push(group);}
        group.parts=Math.max(group.parts,record.part||1);
      }
      groups.sort((a,b)=>a.number-b.number);
      // An attestation is the average of its works: works of the 1st, «Атестація 1», the rest, «Атестація 2».
      const average=number=>({kind:'average',number,parts:1,title:'Атестація '+number});
      const first=groups.filter(g=>g.attestation===1);
      return [...first,average(1),...groups.filter(g=>g.attestation!==1),average(2)];
    };
    const draw=()=>{
      const needle=search.value.trim().toLocaleLowerCase('uk-UA');
      const students=rows(data.students).filter(s=>!needle||s.name.toLocaleLowerCase('uk-UA').includes(needle));
      // Own row first, so on a phone it is visible without scrolling the table.
      if(!data.group)students.sort((a,b)=>(b.id===data.viewer_id)-(a.id===data.viewer_id));
      const sections=subjects.filter(s=>s.key===subjectFilter.value).map(subject=>{
        const groups=groupsFor(subject), columns=groups.flatMap(g=>Array.from({length:g.parts},(_,i)=>({...g,part:i+1})));
        const head=el('thead',{},el('tr',{},el('th',{rowspan:2,scope:'col',class:'journal-name'},'Прізвище та ім’я'),groups.map(g=>{
          const due=rows(data.dates).find(d=>d.subject_key===subject.key&&d.number===g.number&&g.kind==='average')?.due_date;
          return el('th',{colspan:g.parts,scope:'colgroup',class:g.kind==='average'?'journal-average':null},g.title,due?el('small',{},due):null);
        })),
          el('tr',{},columns.map(c=>el('th',{scope:'col',class:c.kind==='average'?'journal-average':null},c.optional_last&&c.part===3?'3*':c.kind==='average'?'Сер. бал':String(c.part)))));
        const body=el('tbody',{},students.map(student=>el('tr',{class:student.id===data.viewer_id?'journal-own-row':''},el('th',{scope:'row',class:'journal-name'},student.name),columns.map(column=>{
          if(column.kind==='average'){const a=data.averages?.[student.id]?.[subject.key]?.[column.number];return el('td',{class:'journal-average'},a&&a.average!==null?a.average.toFixed(2):'—');}
          const record=rows(data.records).find(r=>r.user_id===student.id&&r.subject_key===subject.key&&r.kind===column.kind&&r.number===column.number&&(r.part||1)===column.part);
          const configured=subject.labs?.find(l=>l.number===column.number)?.parts||1;
          const editable=record?record.can_edit:((student.id===data.viewer_id||data.group)&&!column.legacy&&column.part<=configured);
          const cls='journal-cell'+(record?'':' cell-empty');
          if(editable)return el('td',{},el('span',{class:cls},
            el('input',{class:'journal-input'+tone(record?.grade),'data-cell':[student.id,subject.key,column.kind,column.number,column.part].join(':'),type:'text',inputmode:'decimal',autocomplete:'off',maxlength:4,value:record?.grade??'','aria-label':(data.group?student.name+', ':'')+column.title+', оцінка '+column.part,
              onchange:e=>saveInline(student,subject,column,record,e.target),onfocus:e=>e.target.select?.(),onkeydown:moveFocus})));
          return el('td',{},el('span',{class:cls},record?grade(record.grade):el('span',{'aria-label':'Оцінки ще немає'},'—')));
        }))));
        return el('section',{class:'journal-subject'},el('h2',{},subject.name),
          el('div',{class:'journal-table-scroll',role:'region',tabindex:0,'aria-label':'Таблиця оцінок: '+subject.name},el('table',{class:'journal-table'},el('caption',{class:'screen-reader-only'},subject.name+' — журнал групи'),head,body)),
          groups.some(g=>g.optional_last)?small('* Третя оцінка за лабораторну — за потреби.'):null,
          !students.length?small('У списку немає студентів за цим пошуком.'):null);
      });
      tables.replaceChildren(...sections);
      subjectSelect.value=subjectFilter.value;
    };
    search.addEventListener('input',draw);draw();
    const dates=card(el('h2',{},'Дати атестацій'),!rows(data.dates).some(d=>d.due_date)?small('Дати ще не вказані. Власник додасть їх пізніше.'):small('Дати показані в заголовках атестацій у таблицях.'));
    if(data.can_set_dates)dates.append(btn('Вказати дату',()=>{
      const subject=select(subjects.map(s=>[s.key,s.name]),subjectFilter.value||undefined),number=input({type:'number',min:1,max:100,step:1,value:1}),due=input({type:'date',required:false});
      sheet('Дата атестації','Порожня дата прибирає попередню дату.').append(form([field('Предмет',subject),field('Номер атестації',number),field('Дата',due)],'Зберегти дату',ctx=>save({action:'date',period:data.period,subject_key:subject.value,number:Number(number.value),due_date:due.value},ctx)));
    },'button secondary'));
    return [el('section',{class:'card group-journal-controls'},
      field('Предмет',subjectSelect),field('Пошук студента',search),
      rows(data.periods).length>1?field('Навчальний період',period):small(data.period.replace(':',' · семестр ')),
      small('Зміни оцінок зберігаються автоматично.')),tables,dates];
  }


  function page(key) {
    const main = el(
      "main",
      { class: "feature-page" + (key==='journal'?(state.data?.group?' group-journal-page':' personal-journal-page'):'') },
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
      el("div", { class: "page-title" }, el("h1", {}, key==='journal'&&state.data?.group?'Журнал групи':titles[key] || "Розділ")),
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
        btn(
          "Спробувати знову",
          () => open(key, false, state.query),
          "button primary",
        ),
      );
      return main;
    }
    const data = state.data || {};
    const screens = {
      create: createPage,
      proposals: proposalsPage,
      bugs: bugsPage,
      help: helpPage,
      journal: journalPage,
      "admin-history": historyPage,
      "admin-group": groupPage,
      "admin-subjects": subjectsPage,
      "admin-replacements": replacementsPage,
      "admin-saturdays": saturdaysPage,
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
  return { open, page,
    tick: () => { if(S.feature==='admin-saturdays' && allowed('admin-saturdays') && !state.loading && !state.error) saturdayTicker?.(); },
  };
};

