const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

// Minimal DOM values let us test asynchronous UI state without a browser or Telegram.
class Element {
  constructor(tag = "") {
    this.tag = tag;
    this.children = [];
    this.attrs = {};
    this.classList = { toggle() {} };
  }
  append(...items) {
    this.children.push(...items);
  }
  replaceChildren(...items) {
    this.children = items;
  }
  setAttribute(key, value) {
    this.attrs[key] = value;
  }
  addEventListener() {}
  get textContent() {
    return this.children
      .map((c) => (c instanceof Element ? c.textContent : String(c)))
      .join(" ");
  }
}
function setup() {
  const root = new Element("root");
  const context = vm.createContext({
    Node: Element,
    window: {
      Telegram: { WebApp: { initData: "signed-test-data" } },
      scrollTo() {},
    },
    document: {
      getElementById: () => root,
      createElement: (tag) => new Element(tag),
      createElementNS: (_, tag) => new Element(tag),
      createTextNode: (text) => text,
    },
    fetch: async () => {
      throw new Error("Set the expected transport in this test");
    },
    URLSearchParams,
    Date,
    setTimeout,
    clearTimeout,
  });
  const source = fs.readFileSync(
    path.join(__dirname, "../queue_bot/mini_app/static/app.js"),
    "utf8",
  );
  // Export the existing closure only in this VM; production contains no test hooks.
  vm.runInContext(
    source.slice(0, source.indexOf('  document.addEventListener("keydown"')) +
      "\nthis.appTest = {S, loadSchedule, loadWeek, navigate, adminPage, topbar, profilePage, loadAvatars};\n})();",
    context,
  );
  const app = context.appTest;
  app.S.data = {
    today: "2026-10-30",
    me: { name: "Тест Студент", is_admin: true },
    queues: [],
    swaps: [],
  };
  app.S.tab = "schedule";
  return { app, context, root };
}
const result = (data, status = 200) => ({
  ok: status === 200,
  status,
  json: async () => data,
});
const schedule = (date) => ({
  date,
  day: "П’ятниця",
  week_type: "denominator",
  lessons: [],
  slots: [
    ["08:30", "09:50"],
    ["10:00", "11:20"],
    ["11:50", "13:10"],
    ["13:20", "14:40"],
    ["14:50", "16:10"],
  ].map(([startTime, endTime], i) => ({
    number: i + 1,
    startTime,
    endTime,
    lessons: [],
  })),
});
const deferred = () => {
  let resolve;
  const promise = new Promise((r) => (resolve = r));
  return { promise, resolve };
};

test("successful retry leaves the fatal screen after access is restored", async () => {
  const { app, context } = setup();
  context.fetch = async () => result({ error: "Тимчасовий бан" }, 403);
  await app.loadSchedule();
  assert.equal(app.S.fatal, "Тимчасовий бан");
  context.fetch = async () => result(schedule("2026-10-30"));
  await app.loadSchedule();
  assert.equal(app.S.fatal, null);
});

test("transient schedule failure preserves date controls and navigation", async () => {
  const { app, context, root } = setup();
  context.fetch = async () => result({ error: "Тимчасова помилка" }, 500);
  await app.loadSchedule("2026-10-31");
  assert.equal(app.S.scheduleDate, "2026-10-31");
  assert.ok(!app.S.fatal);
  for (const text of ["Сьогодні", "Завтра", "Профіль", "Спробувати знову"])
    assert.ok(root.textContent.includes(text));
  assert.ok(!root.textContent.includes("На обрану дату занять немає."));
});

test("latest selected date wins and the initial request shows loading", async () => {
  const { app, context, root } = setup();
  const first = deferred(),
    second = deferred();
  let requests = 0;
  context.fetch = () => (++requests === 1 ? first.promise : second.promise);
  const oldRequest = app.loadSchedule("2026-10-30");
  assert.ok(!root.textContent.includes("На обрану дату занять немає."));
  assert.equal(app.S.scheduleLoading, true);
  const newRequest = app.loadSchedule("2026-10-31");
  second.resolve(result(schedule("2026-10-31")));
  await newRequest;
  first.resolve(result(schedule("2026-10-30")));
  await oldRequest;
  assert.equal(app.S.schedule.date, "2026-10-31");
  assert.equal(app.S.loading, false);
});

test("admin queue list does not invent a personal position", () => {
  const { app } = setup();
  app.S.mode = "admin";
  app.S.data.queues = [
    {
      id: 1,
      subject: "Предмет",
      teacher: "Викладач",
      status: "open",
      type: "labs",
      count: 2,
      my_position: null,
    },
  ];
  function nodes(node) {
    return node instanceof Element
      ? [node, ...node.children.flatMap(nodes)]
      : [];
  }
  assert.equal(
    nodes(app.adminPage()).filter((node) => node.className === "position-tile")
      .length,
    0,
  );
});

test("leaving a pending schedule ignores its later failure", async () => {
  const { app, context } = setup();
  const request = deferred();
  context.fetch = (url) =>
    url.includes("/bootstrap")
      ? Promise.resolve(result(app.S.data))
      : request.promise;
  const pending = app.loadSchedule();
  app.navigate("profile");
  await new Promise((resolve) => setImmediate(resolve));
  request.resolve(result({ error: "Old forbidden response" }, 403));
  await pending;
  assert.equal(app.S.tab, "profile");
  assert.equal(app.S.loading, false);
  assert.equal(app.S.scheduleLoading, false);
  assert.ok(!app.S.fatal);
});

test("week schedule uses seven real dates and five timed slots per loaded day", async () => {
  const { app, context, root } = setup();
  const requested = [];
  context.fetch = async (url) => {
    const date = new URL(url, "https://offline.test").searchParams.get("date");
    requested.push(date);
    return result(schedule(date));
  };
  await app.loadWeek("2026-10-30");
  assert.equal(requested.length, 7);
  assert.equal(requested[0], "2026-10-26");
  assert.equal(requested[6], "2026-11-01");
  assert.equal(app.S.scheduleView, "week");
  assert.equal(app.S.weekSchedule.length, 7);
  const nodes = (node) =>
    node instanceof Element ? [node, ...node.children.flatMap(nodes)] : [];
  assert.equal(
    nodes(root).filter(
      (node) => node.tag === "article" && node.className.includes("lesson"),
    ).length,
    35,
  );
  assert.equal(root.textContent.split("14:50–16:10").length - 1, 7);
});

test("day timetable renders five numbered timed slots without inventing lessons", async () => {
  const { app, context, root } = setup();
  context.fetch = async () =>
    result({
      ...schedule("2026-11-03"),
      lessons: [{ number: 2, subject: "Машинне навчання" }],
      slots: [
        ["08:30", "09:50"],
        ["10:00", "11:20"],
        ["11:50", "13:10"],
        ["13:20", "14:40"],
        ["14:50", "16:10"],
      ].map((times, i) => ({
        number: i + 1,
        startTime: times[0],
        endTime: times[1],
        lessons:
          i === 1
            ? [
                {
                  number: 2,
                  subject: "Машинне навчання",
                  teachers_raw: [{ name: "Викладач", room: "231" }],
                },
              ]
            : [],
      })),
    });
  await app.loadSchedule("2026-11-03");
  const nodes = (node) =>
    node instanceof Element ? [node, ...node.children.flatMap(nodes)] : [];
  const slots = nodes(root).filter(
    (n) => n.tag === "article" && n.className.includes("lesson"),
  );
  assert.equal(slots.length, 5);
  assert.ok(slots[4].textContent.includes("5"));
  assert.ok(slots[4].textContent.includes("14:50–16:10"));
  assert.ok(slots[4].textContent.includes("Заняття немає"));
  assert.ok(!slots[4].textContent.includes("Машинне навчання"));
  assert.ok(slots[1].textContent.includes("Машинне навчання"));
  assert.ok(slots[1].textContent.includes("Викладач · ауд. 231"));
});

test("Telegram avatars keep initials on missing photo and never rerender an active form", async () => {
  const { app, context, root } = setup();
  app.S.data.me.is_registered = true;
  context.URL = {
    createObjectURL: () => "blob:own-telegram-photo",
    revokeObjectURL() {},
  };
  context.document.querySelectorAll = () => [];
  const requests = [];
  context.fetch = async (path, options) => {
    requests.push([path, options]);
    return path.includes("/bot")
      ? { status: 204 }
      : { status: 200, blob: async () => ({ type: "image/jpeg" }) };
  };
  const originalForm = root.children;
  await app.loadAvatars();
  assert.equal(root.children, originalForm);
  assert.equal(app.S.avatars.bot, null);
  assert.equal(app.S.avatars.me, "blob:own-telegram-photo");
  const nodes = (node) =>
    node instanceof Element ? [node, ...node.children.flatMap(nodes)] : [];
  assert.equal(
    nodes(app.topbar()).filter(
      (node) =>
        node.tag === "img" && node.attrs.src === "blob:own-telegram-photo",
    ).length,
    1,
  );
  assert.equal(
    nodes(app.profilePage()).filter((node) => node.tag === "img").length,
    1,
  );
  assert.ok(app.topbar().textContent.includes("ПІ"));
  assert.equal(requests.length, 2);
  assert.ok(
    requests.every(
      ([, opts]) => opts.headers.Authorization === "tma signed-test-data",
    ),
  );
  await app.loadAvatars();
  assert.equal(requests.length, 2);
});

test("an archived Saturday stays dated history and never carries into the next week", async () => {
  const { app, context, root } = setup();
  context.fetch = async (url) => {
    const date = new URL(url, "https://offline.test").searchParams.get("date");
    return result({
      ...schedule(date),
      source_day: date === "2026-10-31" ? "Понеділок" : null,
      working_day_archived: date === "2026-10-31",
    });
  };
  await app.loadSchedule("2026-10-31");
  assert.match(root.textContent, /Завершено/);
  await app.loadWeek("2026-11-03");
  assert.doesNotMatch(root.textContent, /За розкладом:|Завершено/);
});
