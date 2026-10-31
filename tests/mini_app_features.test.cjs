const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

class Element {
  constructor(tag, attrs = {}, children = []) {
    this.tag = tag;
    this.attrs = attrs;
    this.children = children;
    this.events = {};
    this.value = attrs.value ?? "";
    this.classList = { toggle() {} };
    this.checked = Boolean(attrs.checked);
    this.disabled = Boolean(attrs.disabled);
    this.isConnected = true;
    this.hidden = Boolean(attrs.hidden);
    for (const [key, value] of Object.entries(attrs))
      if (key.startsWith("on")) this.events[key.slice(2)] = value;
  }
  append(...nodes) {
    // Native Element.append stringifies non-node values, including null.
    this.children.push(
      ...nodes.map((n) => (n instanceof Element ? n : String(n))),
    );
  }
  replaceChildren(...nodes) {
    this.children = [];
    this.append(...nodes);
  }
  addEventListener(name, handler) {
    this.events[name] = handler;
  }
  setAttribute(key, value) {
    this.attrs[key] = value;
  }
  getAttribute(key) {
    return this.attrs[key];
  }
  reportValidity() {
    return true;
  }
  querySelectorAll(selectors) {
    return descendants(this).filter(
      (node) => node !== this && selectors.split(",").includes(node.tag),
    );
  }
  get textContent() {
    return this.children
      .map((node) =>
        node instanceof Element ? node.textContent : String(node),
      )
      .join(" ");
  }
  set textContent(value) {
    this.children = [value];
  }
}
function descendants(node) {
  return node instanceof Element
    ? [node, ...node.children.flatMap(descendants)]
    : [];
}
function setup(transport, helpers = {}) {
  const context = vm.createContext({
    window: {},
    URLSearchParams,
    Date,
    FileReader: class {},
  });
  vm.runInContext(
    fs.readFileSync(
      path.join(__dirname, "../queue_bot/mini_app/static/features.js"),
      "utf8",
    ),
    context,
  );
  const S = {
    epoch: 0,
    busy: false,
    mode: "student",
    data: { me: { is_admin: true, is_owner: true } },
  };
  const el = (tag, attrs = {}, ...children) => {
    const node = new Element(tag, attrs);
    node.append(
      ...children
        .flat(Infinity)
        .filter((n) => n !== null && n !== false && n !== undefined),
    );
    return node;
  };
  const panels = [],
    notices = [];
  let renders = 0;
  const features = context.window.createPI231Features({
    S,
    el,
    btn: (label, fn, cls) => el("button", { onclick: fn, class: cls }, label),
    small: (label) => el("small", {}, label),
    badge: (label) => el("span", {}, label),
    api: transport,
    toast: (...args) => notices.push(args),
    sheet: () => {
      const panel = el("section");
      panels.push(panel);
      return panel;
    },
    closeSheet() {},
    navigate() {},
    render() {
      renders++;
    },
    isAdmin: () => S.mode === "admin" && S.data.me.is_admin,
    ...helpers,
  });
  return { S, features, panels, notices, renders: () => renders };
}
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { resolve, reject, promise };
};

test("an old feature response cannot overwrite the newly opened feature", async () => {
  const pending = deferred();
  const { features, S } = setup((path) =>
    path === "/bugs" ? pending.promise : Promise.resolve({ items: [] }),
  );
  const old = features.open("bugs");
  assert.match(features.page("bugs").textContent, /Завантаження/);
  await features.open("polls");
  pending.reject(Object.assign(new Error("stale error"), { status: 403 }));
  await old;
  assert.doesNotMatch(
    features.page("polls").textContent,
    /stale error|Завантаження/,
  );
  assert.match(features.page("polls").textContent, /немає записів/);
  assert.equal(S.fatal, null);
});

test("owner-only features never request data after switching to student mode", async () => {
  let requests = 0;
  const { features } = setup(async () => {
    requests++;
    return {};
  });
  await features.open("owner");
  assert.equal(requests, 0);
  assert.match(features.page("owner").textContent, /Недостатньо прав/);
});

test("native anonymous poll remains read-only and shows actual result counts", async () => {
  const { features, panels } = setup(async (path) =>
    path === "/polls"
      ? { items: [] }
      : {
          poll: {
            id: 7,
            question: "Запитання",
            options: ["Так", "Ні"],
            is_anonymous: true,
          },
          results: { options: [{ index: 0, count: 3, percent: 75 }] },
          my_options: [],
          can_vote: false,
          can_vote_reason: "Голосуй у Telegram",
        },
  );
  await features.open("polls");
  await features.openPoll(7);
  assert.match(panels[0].textContent, /Так · 3 \(75%\)/);
  assert.match(panels[0].textContent, /Голосуй у Telegram/);
  assert.equal(
    descendants(panels[0]).filter((node) => node.tag === "form").length,
    0,
  );
  assert.ok(
    descendants(panels[0])
      .filter((node) => node.tag === "input")
      .every((node) => node.disabled),
  );
});

test("queue creation submits numeric catalog IDs and server errors stay in the form", async () => {
  const requests = [];
  const { features, S } = setup(async (path, body) => {
    requests.push([path, body]);
    if (path === "/catalog")
      return {
        subjects: [
          { id: 12, name: "Предмет", teachers: [{ id: 34, name: "Викладач" }] },
        ],
      };
    throw new Error("Черга вже існує");
  });
  await features.open("create");
  const page = features.page("create"),
    selects = descendants(page).filter((node) => node.tag === "select");
  selects[0].value = "normal";
  selects[1].value = "12";
  selects[1].events.change();
  selects[2].value = "34";
  const form = descendants(page).find((node) => node.tag === "form");
  await form.events.submit({ preventDefault() {} });
  assert.equal(requests[1][1].subject_id, 12);
  assert.equal(requests[1][1].teacher_id, 34);
  assert.match(form.textContent, /Черга вже існує/);
  assert.equal(S.busy, false);
  assert.ok(
    form
      .querySelectorAll("button,input,select,textarea")
      .every((node) => !node.disabled),
  );
});

test("a poll request that finishes after navigation cannot open a sheet", async () => {
  const pending = deferred();
  const { features, panels } = setup(async (path) =>
    path === "/polls/7" ? pending.promise : { items: [] },
  );
  await features.open("polls");
  const request = features.openPoll(7);
  await features.open("help");
  pending.resolve({ poll: { question: "Old", options: [] }, can_vote: false });
  await request;
  assert.equal(panels.length, 0);
});

test("access failures replace protected content with the global fatal state", async () => {
  const error = Object.assign(new Error("Доступ відкликано"), { status: 403 });
  const { features, S, renders } = setup(async () => {
    throw error;
  });
  S.mode = "admin";
  await features.open("admin-reports");
  assert.equal(S.fatal, "Доступ відкликано");
  assert.ok(renders() >= 2);
});

test("reports draw real totals and bars without raw implementation field labels", async () => {
  const { features, S } = setup(async () => ({
    subjects: [],
    statistics: {
      registered_users: 21,
      lessons_count: 3,
      consults_count: 1,
      cw_count: 0,
      total_passed: 8,
      total_active: 2,
      total_left: 1,
      total_removed: 0,
      subjects: [{ name: "Предмет А", passed: 8 }],
      top_students: [{ name: "Студент А", passed: 5 }],
    },
  }));
  S.mode = "admin";
  await features.open("admin-reports");
  const page = features.page("admin-reports");
  assert.match(page.textContent, /Типи черг/);
  assert.match(page.textContent, /75%/);
  assert.doesNotMatch(
    page.textContent,
    /lessons_count|total_passed|undefined|NaN/,
  );
  assert.ok(
    descendants(page).some((node) =>
      node.attrs.style?.includes("conic-gradient"),
    ),
  );
  descendants(page)
    .find((node) => node.attrs["data-report-view"] === "subjects")
    .events.click();
  assert.match(page.textContent, /Предмет А/);
  assert.ok(
    descendants(page).some((node) => node.attrs.style?.includes("height:100%")),
  );
  descendants(page)
    .find((node) => node.attrs["data-report-view"] === "students")
    .events.click();
  assert.match(page.textContent, /Студент А/);
});

test("anonymous poll results never show voter names but public polls may", async () => {
  let anonymous = true;
  const { features, panels } = setup(async (path) =>
    path === "/polls"
      ? { items: [] }
      : {
          poll: {
            question: "Опитування",
            options: ["Так"],
            is_anonymous: anonymous,
          },
          results: {
            options: [
              {
                index: 0,
                count: 1,
                percent: 100,
                voters: [{ name: "Публічне ім’я" }],
              },
            ],
          },
          can_vote: false,
        },
  );
  await features.open("polls");
  await features.openPoll(1);
  assert.doesNotMatch(panels[0].textContent, /Публічне ім’я/);
  anonymous = false;
  await features.openPoll(2);
  assert.match(panels[1].textContent, /Публічне ім’я/);
  assert.equal(
    descendants(panels[1]).find((node) => node.tag === "meter").attrs.value,
    100,
  );
});

test("missing or invalid report numbers have an empty chart instead of invalid CSS", async () => {
  const { features, S } = setup(async () => ({
    subjects: [],
    statistics: {
      lessons_count: NaN,
      consults_count: -3,
      cw_count: "4",
      registered_users: Infinity,
      subjects: [{ name: "Без здач", passed: undefined }],
    },
  }));
  S.mode = "admin";
  await features.open("admin-reports");
  const page = features.page("admin-reports");
  assert.match(page.textContent, /Немає даних для діаграми/);
  assert.doesNotMatch(page.textContent, /NaN|Infinity|undefined/);
  descendants(page)
    .find((node) => node.attrs["data-report-view"] === "subjects")
    .events.click();
  assert.match(page.textContent, /Поки немає зданих робіт/);
});

test("successful feature retry clears a previous global fatal error", async () => {
  const { features, S } = setup(async () => ({ items: [] }));
  S.fatal = "Доступ був тимчасово обмежений";
  await features.open("polls");
  assert.equal(S.fatal, null);
});

test("a successful mutation refresh releases the global busy controls", async () => {
  const busy = [],
    { features, S } = setup(
      async (path, body) => (body ? { message: "Надіслано" } : { items: [] }),
      { updateBusy: (value) => busy.push(value) },
    );
  await features.open("bugs");
  const page = features.page("bugs"),
    form = descendants(page).find((node) => node.tag === "form");
  descendants(form).find((node) => node.tag === "textarea").value =
    "Опис помилки";
  await form.events.submit({ preventDefault() {} });
  assert.equal(S.busy, false);
  assert.deepEqual(busy, [true, false]);
});

test("schedule JSON import previews text safely and requires separate confirmation", async () => {
  const requests = [],
    { features, S, panels } = setup(
      async (path, body) => {
        requests.push([path, body]);
        return { active: [], archived: [] };
      },
      { download: async () => {} },
    );
  S.mode = "admin";
  await features.open("admin-replacements");
  const page = features.page("admin-replacements");
  const file = descendants(page).find(
    (node) =>
      node.tag === "input" && node.attrs.accept === "application/json,.json",
  );
  assert.ok(file, "JSON import input exists");
  file.files = [
    {
      size: 80,
      text: async () => '{"monday":[{"subject":"<script>test</script>"}]}',
    },
  ];
  const importForm = descendants(page).find(
    (node) => node.tag === "form" && descendants(node).includes(file),
  );
  await importForm.events.submit({ preventDefault() {} });
  assert.equal(requests.filter(([, body]) => body).length, 0);
  assert.match(panels[0].textContent, /<script>test<\/script>/);
  const confirmation = descendants(panels[0]).find(
    (node) => node.tag === "form",
  );
  await confirmation.events.submit({ preventDefault() {} });
  assert.equal(
    requests.find(([, body]) => body)?.[1].action,
    "import_schedule",
  );
});

test("PDF download follows the selected subject and screenshot opens an authenticated helper", async () => {
  const downloads = [],
    images = [];
  const { features, S } = setup(
    async (path) =>
      path.startsWith("/admin/manage/reports")
        ? {
            subjects: [{ id: 5, name: "Предмет" }],
            subject_report: { students: [] },
          }
        : {
            items: [
              { id: 8, text: "Помилка", has_photo: true, status: "pending" },
            ],
          },
    {
      download: async (...args) => downloads.push(args),
      openImage: async (path) => images.push(path),
    },
  );
  S.mode = "admin";
  await features.open("admin-reports", false, "?subject_id=5");
  const report = features.page("admin-reports");
  const csv = descendants(report).find(
    (node) => node.tag === "button" && node.textContent.includes("PDF"),
  );
  assert.match(csv.textContent, /предмета/);
  await csv.events.click();
  assert.match(downloads[0][0], /subject_id=5/);
  assert.match(downloads[0][0], /format=pdf/);
  assert.match(downloads[0][1], /\.pdf$/);
  await features.open("admin-bugs");
  const photo = descendants(features.page("admin-bugs")).find(
    (node) => node.tag === "button" && node.textContent.includes("скріншот"),
  );
  assert.ok(photo);
  await photo.events.click();
  assert.deepEqual(images, ["/bugs/8/photo"]);
});

test("schedule import rejects oversized files before reading or uploading", async () => {
  let uploads = 0,
    reads = 0;
  const { features, S, panels } = setup(async (path, body) => {
    if (body) uploads++;
    return { active: [], archived: [] };
  });
  S.mode = "admin";
  await features.open("admin-replacements");
  const page = features.page("admin-replacements");
  const file = descendants(page).find(
    (node) =>
      node.tag === "input" && node.attrs.accept === "application/json,.json",
  );
  file.files = [
    {
      size: 65537,
      text: async () => {
        reads++;
        return "{}";
      },
    },
  ];
  const form = descendants(page).find(
    (node) => node.tag === "form" && descendants(node).includes(file),
  );
  await form.events.submit({ preventDefault() {} });
  assert.match(form.textContent, /64 КБ/);
  assert.equal(reads, 0);
  assert.equal(uploads, 0);
  assert.equal(panels.length, 0);
});

test("every feature page action has a real click handler or a submitted form", async () => {
  const { features, S } = setup(async () => ({
    items: [],
    subjects: [],
    users: [],
    sessions: [],
    entries: [],
    active: [],
    archived: [],
    statistics: {},
  }));
  S.mode = "admin";
  for (const key of [
    "create",
    "proposals",
    "bugs",
    "polls",
    "help",
    "admin-history",
    "admin-group",
    "admin-subjects",
    "admin-replacements",
    "admin-saturdays",
    "admin-polls",
    "admin-reports",
    "owner",
    "admin-admins",
    "admin-bugs",
    "admin-proposals",
  ]) {
    await features.open(key);
    const nodes = descendants(features.page(key));
    for (const button of nodes.filter((node) => node.tag === "button")) {
      assert.ok(
        typeof button.events.click === "function" ||
          (button.attrs.type === "submit" &&
            nodes.some(
              (node) =>
                node.tag === "form" &&
                descendants(node).includes(button) &&
                typeof node.events.submit === "function",
            )),
        key + ": " + button.textContent,
      );
    }
  }
});

test("existing mini-app votes can be withdrawn through the real vote endpoint", async () => {
  const requests = [],
    { features, panels } = setup(async (path, body) => {
      requests.push([path, body]);
      return path === "/polls/3"
        ? {
            poll: { question: "Запитання", options: ["Так", "Ні"] },
            my_options: [1],
            can_vote: true,
            results: { options: [] },
          }
        : { items: [] };
    });
  await features.open("polls");
  await features.openPoll(3);
  const withdraw = descendants(panels[0]).find(
    (node) =>
      node.tag === "form" && node.textContent.includes("Відкликати голос"),
  );
  assert.ok(withdraw);
  await withdraw.events.submit({ preventDefault() {} });
  const mutation = requests.find(
    ([path, body]) => path === "/polls/3/vote" && body,
  );
  assert.ok(mutation);
  assert.equal(mutation[1].options.length, 0);
});

test("empty replacement lists never render a literal null action", async () => {
  const { S, features } = setup(async () => ({ active: [], archived: [] }));
  S.mode = "admin";
  await features.open("admin-replacements");
  assert.ok(!features.page("admin-replacements").textContent.includes("null"));
});

test("admin session participants open a focused detail including an empty state", async () => {
  const sessions = [
    { id: 11, subject: "Машинне навчання", status: "open" },
    { id: 12, subject: "Інший предмет", status: "archived" },
  ];
  const { features, S } = setup(async (path) => ({
    sessions,
    entries: [],
    session_id: path.includes("sid=11") ? 11 : null,
  }));
  S.mode = "admin";
  await features.open("admin-history");
  const button = descendants(features.page("admin-history")).find(
    (node) => node.tag === "button" && node.textContent === "Учасники сесії",
  );
  await button.events.click();
  const page = features.page("admin-history");
  assert.match(page.textContent, /Машинне навчання/);
  assert.match(page.textContent, /У цій сесії ще немає учасників/);
  assert.doesNotMatch(page.textContent, /Інший предмет/);
  const back = descendants(page).find(
    (node) => node.tag === "button" && node.textContent === "До списку сесій",
  );
  assert.ok(back);
  await back.events.click();
  assert.match(features.page("admin-history").textContent, /Інший предмет/);
});

test("working Saturdays keep past dates in a read-only archive", async () => {
  const { features, S } = setup(async () => ({
    items: [
      { date: "2026-11-07", source_day: "tuesday", week_type: "numerator" },
    ],
    archived: [
      { date: "2026-10-31", source_day: "monday", week_type: "denominator" },
    ],
  }));
  S.mode = "admin";
  await features.open("admin-saturdays");
  const page = features.page("admin-saturdays");
  const archive = descendants(page).find((node) => node.tag === "details");
  assert.ok(archive);
  assert.match(archive.textContent, /2026-10-31/);
  assert.doesNotMatch(archive.textContent, /2026-11-07/);
  assert.equal(
    descendants(archive).filter((node) => node.tag === "button").length,
    0,
  );
  assert.equal(
    descendants(page).filter(
      (node) =>
        node.tag === "button" && node.textContent === "Повідомити групу",
    ).length,
    1,
  );
});
