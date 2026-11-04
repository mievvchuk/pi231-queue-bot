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
    journal: "Електронний журнал",
    "admin-history": "Історія черг",
    "admin-group": "Група",
    "admin-subjects": "Предмети й викладачі",
    "admin-replacements": "Заміни",
    "admin-saturdays": "Робочі суботи",
    "admin-polls": "Керування опитуваннями",
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
    const sessionMeta=session=>small([session?.teacher,historyTime(session?.date),session?.status==='archived'?'Архів':null].filter(Boolean).join(' · '));
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
  let journalSubject='';
  function journalPage(data) {
    const query = (review=data.group,period=data.period) => '?period='+encodeURIComponent(period)+(review?'&scope=group':'');
    const save = async (values, ctx) => {
      await send('/journal', values, ctx, false);
      if(current(ctx.token,ctx.epoch,ctx.key))await open('journal',true,query());
    };
    const labels={pending:'Чекає підтвердження',approved:'Підтверджено',rejected:'Повернуто на виправлення'};
    const period=select(rows(data.periods).map(p=>[p,p.replace(':',' · семестр ')]),data.period);
    period.addEventListener('change',()=>open('journal',false,query(data.group,period.value)));
    const subjects=rows(data.subjects);
    const initial=subjects.some(s=>s.key===journalSubject)?journalSubject:subjects.find(s=>/конструювання/i.test(s.name))?.key||subjects[0]?.key||'';
    const subjectFilter={value:initial};
    const pick=key=>{subjectFilter.value=journalSubject=key;draw();};
    const pager=el('nav',{class:'journal-pager','aria-label':'Предмети'});
    const avgText=a=>a&&a.average!==null?a.average.toFixed(2):'Немає оцінок';
    const mine=()=>data.averages?.[data.viewer_id]||{};
    const summary=card();
    const search=input({type:'search',required:false,placeholder:'Прізвище або ім’я','aria-label':'Пошук студента'});
    const tables=el('div',{class:'journal-tables'});
    const tone = value => value===undefined||value===''?'':Number(value)===5?' grade-five':Number(value)<3?' grade-low':'';
    const grade = (value,status) => el('span',{class:'journal-grade'+tone(value),'aria-label':'Оцінка '+value+' із 5. '+labels[status]},String(value));
    const mark = record => record&&record.status!=='approved'?el('small',{class:'journal-status-mark','aria-hidden':'true'},record.status==='pending'?'?':'!'):null;
    const reviewRecord=(record,status)=>{
      const note=text({required:status==='rejected',maxlength:500});
      sheet(status==='approved'?'Підтвердити оцінку?':'Повернути на виправлення',record.student_name+' · '+record.subject_name+' · '+record.grade+' із 5').append(form([field('Коментар адміністратора',note)],status==='approved'?'Підтвердити':'Повернути',ctx=>save({action:'review',record_id:record.id,revision:record.revision,status,note:note.value},ctx)));
    };
    const editCell=(student,subject,column,record)=>{
      const score=input({type:'number',min:0,max:5,step:0.1,value:record?.grade??'',placeholder:'Від 0 до 5'});
      const note=text({required:false,maxlength:500});note.value=record?.note||'';
      const panel=sheet(student.name,subject.name+' · '+column.title+' · оцінка '+column.part);
      if(record)panel.append(badge(labels[record.status]),record.review_note?el('p',{},'Коментар: '+record.review_note):null);
      panel.append(form([field('Оцінка',score),field('Примітка',note)],'Зберегти',ctx=>save({action:'submit',period:data.period,user_id:student.id,subject_key:subject.key,kind:column.kind,number:column.number,part:column.part,grade:Number(score.value),note:note.value,...(record?{record_id:record.id,revision:record.revision}:{})},ctx)));
      if(data.can_review&&record?.status==='pending')panel.append(el('div',{class:'button-row'},btn('Підтвердити',()=>reviewRecord(record,'approved'),'button primary'),btn('На виправлення',()=>reviewRecord(record,'rejected'),'button secondary')));
    };
    // Own row: the grade is typed straight into the cell and saved on change, no dialog.
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
          const editable=record?record.can_edit:((student.id===data.viewer_id||data.can_review)&&!column.legacy&&(column.kind==='attestation'||column.part<=configured));
          const cls='journal-cell'+(record?' cell-'+record.status:' cell-empty');
          if(editable&&!data.group&&student.id===data.viewer_id)return el('td',{},el('span',{class:cls},
            el('input',{class:'journal-input'+tone(record?.grade),'data-cell':[subject.key,column.kind,column.number,column.part].join(':'),type:'text',inputmode:'decimal',autocomplete:'off',maxlength:4,value:record?.grade??'','aria-label':column.title+', оцінка '+column.part,
              onchange:e=>saveInline(student,subject,column,record,e.target),onkeydown:e=>{if(e.key==='Enter')e.target.blur();}}),mark(record)));
          const content=record?[grade(record.grade,record.status),mark(record)]:el('span',{'aria-label':'Оцінки ще немає'},'—');
          const node=editable?el('button',{class:cls,type:'button','aria-label':student.name+', '+column.title+', оцінка '+column.part+(record?': '+record.grade:' — додати'),onclick:()=>editCell(student,subject,column,record)},content):el('span',{class:cls},content);
          return el('td',{},node);
        }))));
        return el('section',{class:'journal-subject'},el('h2',{},subject.name),
          el('div',{class:'journal-table-scroll',role:'region',tabindex:0,'aria-label':'Таблиця оцінок: '+subject.name},el('table',{class:'journal-table'},el('caption',{class:'screen-reader-only'},subject.name+' — журнал групи'),head,body)),
          groups.some(g=>g.optional_last)?small('* Третя оцінка за лабораторну — за потреби.'):null,
          !students.length?small('У списку немає студентів за цим пошуком.'):null);
      });
      tables.replaceChildren(...sections);
      const index=subjects.findIndex(s=>s.key===subjectFilter.value);
      const step=(label,to)=>el('button',{type:'button',class:'button secondary journal-pager-step','aria-label':label==='‹'?'Попередній предмет':'Наступний предмет',disabled:!subjects[to],onclick:()=>subjects[to]&&pick(subjects[to].key)},label);
      pager.replaceChildren(step('‹',index-1),
        ...subjects.map((s,i)=>el('button',{type:'button',class:'journal-pager-page'+(i===index?' active':''),title:s.name,'aria-label':s.name,'aria-current':i===index?'page':null,onclick:()=>pick(s.key)},String(i+1))),
        step('›',index+1));
      summary.replaceChildren(el('h2',{},'Мої середні бали'),el('div',{class:'journal-summary'},subjects.filter(s=>mine()[s.key]).map(subject=>{
        const a=mine()[subject.key];
        return el('button',{type:'button',class:'journal-summary-row'+(subjectFilter.value===subject.key?' active':''),onclick:()=>pick(subject.key)},
          el('strong',{},subject.name),el('span',{},'1 атест.: '+avgText(a['1'])),el('span',{},'2 атест.: '+avgText(a['2'])),
          el('small',{},'Заповнено '+a.all.filled+' з '+a.all.total));
      })));
    };
    search.addEventListener('input',draw);draw();
    const dates=card(el('h2',{},'Дати атестацій'),!rows(data.dates).some(d=>d.due_date)?small('Дати ще не вказані. Власник додасть їх пізніше.'):small('Дати показані в заголовках атестацій у таблицях.'));
    if(data.can_set_dates)dates.append(btn('Вказати дату',()=>{
      const subject=select(subjects.map(s=>[s.key,s.name]),subjectFilter.value||undefined),number=input({type:'number',min:1,max:100,step:1,value:1}),due=input({type:'date',required:false});
      sheet('Дата атестації','Порожня дата прибирає попередню дату.').append(form([field('Предмет',subject),field('Номер атестації',number),field('Дата',due)],'Зберегти дату',ctx=>save({action:'date',period:data.period,subject_key:subject.value,number:Number(number.value),due_date:due.value},ctx)));
    },'button secondary'));
    return [card(small(data.group?'Режим перевірки: натисни на клітинку, щоб підтвердити або повернути оцінку.':'Впиши оцінку прямо в клітинку свого рядка — вона збережеться сама, а адміністратор потім її підтвердить.'),
      field('Навчальний період',period),field('Студент',search),
      small('? — чекає підтвердження. ! — повернуто на виправлення.'),
      S.data?.me?.is_admin?btn(data.group?'Завершити перевірку':'Перевірка адміністратора',()=>{if(!data.group)S.mode='admin';open('journal',false,query(!data.group));},'button secondary'):null),summary,tables,pager,dates];
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
      polls: pollsPage,
      "admin-history": historyPage,
      "admin-group": groupPage,
      "admin-subjects": subjectsPage,
      "admin-replacements": replacementsPage,
      "admin-saturdays": saturdaysPage,
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
  return { open, page, openPoll: pollDetail,
    tick: () => { if(S.feature==='admin-saturdays' && allowed('admin-saturdays') && !state.loading && !state.error) saturdayTicker?.(); },
  };
};
