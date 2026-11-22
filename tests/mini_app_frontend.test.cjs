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
    this.classList = { add() {}, remove() {}, toggle() {} };
    this.style = { setProperty() {} };
  }
  append(...items) {
    this.children.push(...items);
  }
  replaceChildren(...items) {
    this.children = items;
  }
  setAttribute(key, value) {
    this.attrs[key] = value;
    if(key==='value') this.value=value;
    if(key.startsWith('data-')){this.dataset||={};this.dataset[key.slice(5)]=value;}
  }
  remove() {}
  querySelectorAll(selector) {
    return allNodes(this).filter(n=>selector==='[data-page]'?n.attrs['data-page']!==undefined:selector.startsWith('.')?String(n.className||n.attrs.class||'').split(' ').includes(selector.slice(1)):n.tag===selector);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0]||null; }
  addEventListener(type, callback) {
    this.listeners ||= {};
    this.listeners[type] = callback;
  }
  removeAttribute(key) {
    delete this.attrs[key];
  }
  getBoundingClientRect() {
    return { left: 0, width: 1 };
  }
  get textContent() {
    return this.children
      .map((c) => (c instanceof Element ? c.textContent : String(c)))
      .join(" ");
  }
}
function setup(enableDesign = false, featureStub = null) {
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
  if (enableDesign) {
    vm.runInContext(fs.readFileSync(
      path.join(__dirname, '../queue_bot/mini_app/static/design.js'), 'utf8'), context);
    const createDesign=context.window.createPI231Design;
    // Let interaction tests render just their page instead of the whole shell.
    context.window.createPI231Design=hooks=>createDesign({...hooks,render:()=>context.appTest.rerender()});
  }
  if (featureStub) context.window.createPI231Features = () => featureStub;
  // Export the existing closure only in this VM; production contains no test hooks.
  vm.runInContext(
    source.slice(0, source.indexOf('  document.addEventListener("keydown"')) +
      "\nthis.appTest = {S, design, load, openInitialFeature, loadSchedule, loadWeek, navigate, adminPage, topbar, profilePage, loadAvatars, homePage, morePage, bottomnav, queuePage, queuesPage, rerender:()=>render(), setRender: fn => {render=fn;}, loginFailureMessage, attachLiquidLens: typeof attachLiquidLens === 'function' ? attachLiquidLens : null, installPressOptics: typeof installPressOptics === 'function' ? installPressOptics : null};\n})();",
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

test('journal launch link opens the exact feature after authenticated bootstrap',async()=>{
  const calls=[];
  const {app,context}=setup(false,{open:async key=>calls.push(key)});
  context.location={search:'?page=journal'};
  app.S.data.me.is_registered=true;
  await app.openInitialFeature();
  assert.deepEqual(calls,['journal']);
  assert.equal(app.S.mode,'student');
});

test('launch links require registration and ignore arbitrary feature names and old poll links',async()=>{
  const calls=[];
  const {app,context}=setup(false,{open:async key=>calls.push(key)});
  context.location={search:'?page=journal'};
  app.S.data.me.is_registered=false;
  await app.openInitialFeature();
  assert.deepEqual(calls,[]);
  app.S.data.me.is_registered=true;
  app.S.fatal='Потрібно повторити вхід';
  await app.openInitialFeature();
  assert.deepEqual(calls,[]);
  app.S.fatal=null;
  context.location={search:'?page=owner'};
  await app.openInitialFeature();
  assert.deepEqual(calls,['journal']);
  calls.length=0;
  context.location={search:'?poll=12'};
  await app.openInitialFeature();
  assert.deepEqual(calls,['journal']);
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
  for (const text of ["Сьогодні", "Завтра", "Ще", "Спробувати знову"])
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

test("Liquid Optics v2.8 keeps a pure-black dark canvas with accessible fallbacks", () => {
  const css = fs.readFileSync(
    path.join(__dirname, "../queue_bot/mini_app/static/glass.css"),
    "utf8",
  );
  assert.match(css, /--bg:\s*#000(?:000)?\b/);
  assert.match(css, /\.lesson\.liquid-pressed/);
  assert.match(css, /prefers-reduced-motion/);
  assert.match(css, /prefers-reduced-transparency/);
  assert.match(css, /@supports not \(backdrop-filter/);
});

test("Liquid Optics exposes one reusable lens controller for navigation and segments", () => {
  const { app } = setup();
  assert.equal(typeof app.attachLiquidLens, "function");
});

test("Liquid Optics exposes press handling separately from queue business actions", () => {
  const { app } = setup();
  assert.equal(typeof app.installPressOptics, "function");
});

test("Liquid lens prevents a synthetic click from navigating twice after pointer release", () => {
  const source = fs.readFileSync(
    path.join(__dirname, "../queue_bot/mini_app/static/app.js"),
    "utf8",
  );
  assert.match(source, /suppressClickUntil/);
  assert.match(source, /activate/);
});

const allNodes = node => node instanceof Element ? [node,...node.children.flatMap(allNodes)] : [];

test('both sidebar modes expose the journal and student bottom tabs retain Profile',()=>{
  const opened=[];
  const {app}=setup(true,{open:key=>opened.push(key)});
  app.S.mode='student';
  let shell=app.design.navigation();
  const journal=shell.querySelectorAll('[data-page]').find(n=>n.dataset.page==='journal');
  assert.ok(journal);
  journal.listeners.click();
  assert.deepEqual(opened,['journal']);
  const bottom=shell.querySelector('.bottom-nav');
  assert.ok(bottom.querySelectorAll('[data-page]').some(n=>n.dataset.page==='profile'));
  app.S.mode='admin';
  shell=app.design.navigation();
  assert.ok(shell.querySelectorAll('[data-page]').some(n=>n.dataset.page==='journal'));
});

for(const design of [false,true]) test('permanent queues have no signup toggles or whole-queue finish actions, design='+design,()=>{
  const {app}=setup(design);
  app.S.mode='admin';app.S.data.me.is_admin=true;app.S.data.me.is_owner=true;
  app.S.queue={id:1,subject:'КПЗ',teacher:'Викладач',status:'open',count:0,labs:[],entries:[]};
  const text=app.queuePage().textContent;
  assert.doesNotMatch(text,/Закрити запис|Відкрити запис|завершення черги|Завершити сесію|Почати здачу|Зупинити здачу/);
  assert.doesNotMatch(text,/Постійна черга|Запис доступний завжди/i);
});

for (const design of [false, true]) test('admin menus do not expose removed statistics or reports, design='+design, () => {
  const {app}=setup(design);
  app.S.mode='admin';
  app.S.data.me.is_admin=true;
  app.S.data.me.is_owner=true;
  const page=app.adminPage();
  assert.doesNotMatch(page.textContent,/Звіти|Статистика|діаграми/);
});
test('four-tab navigation keeps secondary features reachable through More', () => {
  const {app}=setup();
  const nav=app.bottomnav();
  assert.deepEqual(allNodes(nav).filter(n=>n.tag==='button').map(n=>n.textContent.trim()),['Головна','Черги','Розклад','Ще']);
  for(const text of ['Профіль','Допомога','Звернення','Керування групою','PWA'])
    assert.ok(app.morePage().textContent.includes(text));
  assert.ok(!app.morePage().textContent.includes('Опитування'));
});
test('home shows real upcoming class and personal positions without invented waits', () => {
  const {app}=setup();
  app.S.data.next_lesson={date:'2026-10-30',startTime:'11:50',endTime:'13:10',lessons:[{subject:'Машинне навчання',room:'231'}]};
  app.S.data.queues=[{id:1,subject:'Машинне навчання',status:'open',my_position:8,my_labs:[4],my_planned_minutes:30},
    {id:2,subject:'Людино-машинна взаємодія',status:'closed',my_position:15,my_labs:[3],my_planned_minutes:10}];
  const page=app.homePage();
  for(const text of ['НАЙБЛИЖЧА ПАРА','11:50–13:10','231','ТВІЙ ЗАПИС','8','Інші записи','#15','≈ 30 хв']) assert.ok(page.textContent.includes(text),text);
  assert.ok(!page.textContent.includes('очікування'));
});
test('queue detail puts participants before tools and preserves completion and retry actions', () => {
  const {app}=setup();
  app.S.queue={id:1,subject:'Машинне навчання',teacher:'Викладач',status:'open',count:1,labs:[{number:4}],entries:[{id:1,name:'Тест Студент',position:8,is_me:true,labs:[4],planned_minutes:30}]};
  const page=app.queuePage(), text=page.textContent;
  assert.ok(text.indexOf('Учасники')<text.indexOf('Лабораторні та час'));
  for(const value of ['Здав','Завершити підхід з рештою робіт','Хочу вище','Хочу нижче','Вийти з черги','Обмін місцями']) assert.ok(text.includes(value));
  assert.equal(allNodes(page).filter(n=>n.className==='queue-dock').length,1);
});

test('login errors distinguish credentials, browser context and nonce while ignoring untrusted query text',()=>{
  const {app}=setup();
  assert.match(app.loginFailureMessage('client_config'),/Client ID і Client Secret/);
  assert.match(app.loginFailureMessage('browser_changed'),/те саме вікно/);
  assert.match(app.loginFailureMessage('nonce_missing'),/Код для підтримки: nonce_missing/);
  assert.equal(app.loginFailureMessage('PRIVATE_TOKEN<script>'),'Вхід не завершено. Спробуй ще раз.');
});

test('a custom upcoming date does not select Today or show a false lens',async()=>{
  const {app,context,root}=setup();
  context.fetch=async()=>result(schedule('2026-11-02'));
  await app.loadSchedule('2026-11-02');
  const segments=allNodes(root).find(n=>n.className==='segments');
  assert.ok(segments);
  assert.equal(allNodes(segments).filter(n=>n.tag==='button'&&n.className==='selected').length,0);
  assert.equal(allNodes(segments).find(n=>n.className==='liquid-lens').hidden,true);
});
for(const design of [false,true]) test('secondary queue actions are grouped and collapsed, design='+design,()=>{
  const {app}=setup(design);
  app.S.queue={id:1,subject:'Предмет',teacher:'Викладач',status:'open',count:1,labs:[{number:1}],entries:[{id:1,name:'Тест Студент',position:1,is_me:true,labs:[1]}]};
  const page=app.queuePage();
  const extra=allNodes(page).find(n=>n.tag==='details');
  assert.ok(extra);
  assert.ok(!extra.attrs.open);
  assert.ok(extra.textContent.includes('Інші дії'));
  for(const value of ['Лабораторні та час','Обмін місцями','Вийти з черги']) assert.ok(extra.textContent.includes(value));
  assert.ok(!page.textContent.includes('Не здав — у кінець'));
});

test('active lesson exposes readiness and actual completion even before a serving turn',()=>{
  const {app}=setup();
  app.S.queue={id:1,subject:'КПЗ',status:'open',count:1,labs:[{number:1}],
    entries:[{id:1,name:'Тест Студент',position:5,is_me:true,labs:[1]}],
    lesson:{id:1,ends_at:1800000000,people_ahead:0,current_name:'Тест Студент',
      own:{state:'called',token:'test'},states:{'1':'Викликано'}}};
  const page=app.queuePage();
  const buttons=allNodes(page).filter(n=>n.tag==='button');
  assert.ok(page.textContent.includes('10 хвилин'));
  assert.ok(buttons.some(n=>n.textContent.includes('Готовий здавати')));
  assert.ok(buttons.some(n=>n.textContent.includes('Не готовий, пропустити мене')));
  assert.ok(!buttons.some(n=>n.textContent.includes('Не здав — у кінець')));
  assert.ok(buttons.some(n=>n.textContent.includes('Завершити підхід з рештою робіт')));
  assert.ok(buttons.some(n=>n.textContent.includes('Здав')));
  app.S.queue.lesson.own.state='serving';
  const serving=app.queuePage();
  assert.ok(serving.textContent.includes('Завершити підхід з рештою робіт'));
});

test('late student can confirm arrival without losing their queue position',()=>{
  const {app}=setup();
  app.S.queue={id:1,subject:'КПЗ',status:'open',count:1,labs:[{number:1}],
    entries:[{id:1,name:'Тест Студент',position:2,is_me:true,labs:[1]}],
    lesson:{id:1,ends_at:1800000000,people_ahead:null,own:{state:'late',token:'test'},states:{'1':'Запізнюється'}}};
  const page=app.queuePage();
  assert.ok(page.textContent.includes('Запізнюється'));
  assert.ok(page.textContent.includes('Я вже на місці'));
  assert.ok(page.textContent.includes('На цій парі не буду'));
  assert.equal(app.S.queue.entries[0].position,2);
});

for (const enableDesign of [false,true]) test('live queue groups present students before not-ready and late students with saved numbers, design='+enableDesign,()=>{
  const {app}=setup(enableDesign);
  app.S.queue={id:1,subject:'КПЗ',status:'open',count:3,labs:[{number:1}],
    entries:[{id:2,name:'Присутній Студент',position:2,labs:[1]},
      {id:1,name:'Неготовий Студент',position:1,is_me:true,labs:[1]},
      {id:3,name:'Запізнілий Студент',position:3,labs:[1]}],
    lesson:{id:1,ends_at:1800000000,people_ahead:null,current_name:'Присутній Студент',
      ready_ids:[2],deferred_ids:[1,3],own:{state:'not_ready',token:'test'},
      states:{'2':'Викликано · підтверджує готовність','1':'Не готовий · тимчасово пропускаємо',
        '3':'Запізнюється · очікуємо о 08:55'}}};
  const page=app.queuePage();
  const list=allNodes(page).find(n=>(n.className||'').split(' ').includes('entry-list'));
  assert.ok(list.textContent.indexOf('На місці — порядок виклику') < list.textContent.indexOf('Присутній Студент'));
  assert.ok(list.textContent.indexOf('Присутній Студент') < list.textContent.indexOf('Поки пропускаємо'));
  assert.ok(list.textContent.indexOf('Поки пропускаємо') < list.textContent.indexOf('Неготовий Студент'));
  assert.ok(list.textContent.includes('Запізнюється · очікуємо о 08:55'));
  assert.ok(page.textContent.includes('Вже готовий, повернути до виклику'));
  assert.ok(!page.textContent.includes('Твоя черга'));
  assert.deepEqual(app.S.queue.entries.map(e=>e.position),[2,1,3]);
  app.S.queue.lesson.own.state='present';
  assert.ok(app.queuePage().textContent.includes('Поки не готовий, пропустити мене'));
});

test('serving student sees their reminder and a separate response deadline in Kyiv time',()=>{
  const {app}=setup();
  app.S.queue={id:1,subject:'КПЗ',status:'open',count:1,labs:[{number:1}],
    entries:[{id:1,name:'Тест Студент',position:1,is_me:true,labs:[1],planned_minutes:20}],
    lesson:{id:1,ends_at:1800000000,people_ahead:0,own:{state:'serving',token:'test',turn_due:Date.parse('2026-11-03T07:00:00Z')/1000},states:{'1':'Зараз здає'}}};
  let page=app.queuePage();
  assert.ok(page.textContent.includes('Нагадування про завершення о 09:00'));
  assert.ok(page.textContent.includes('Ще здаю'));
  assert.ok(page.textContent.includes('Завершити підхід з рештою робіт'));
  app.S.queue.lesson.own.turn_due=0;
  app.S.queue.lesson.own.deadline=Date.parse('2026-11-03T07:10:00Z')/1000;
  page=app.queuePage();
  assert.ok(page.textContent.includes('продовжіть час до 09:10'));
});

for(const design of [false,true]) test('queue search combines type and personal filters without losing the input, design='+design,()=>{
  const {app,root}=setup(design);
  app.S.data.queues=[
    {id:1,subject:'КПЗ',teacher:'Щуцький',type:'lesson',status:'open',count:5,my_position:2},
    {id:2,subject:'КПЗ',teacher:'Щуцький',type:'consultation',status:'closed',count:3,my_position:null},
    {id:3,subject:'ЛМВ',teacher:'Кондратюк',type:'lesson',status:'open',count:2,my_position:null},
    {id:4,subject:'Курсова',teacher:'Кондратюк',type:'coursework',status:'open',count:1,my_position:null},
  ];
  const original=JSON.stringify(app.S.data.queues);
  let renders=0;
  const render=()=>{renders++;root.replaceChildren(app.queuesPage());};
  app.setRender(render);render();
  const list=()=>allNodes(root).find(n=>['subject-list','queue-list'].includes(n.className));
  const input=()=>allNodes(root).find(n=>n.tag==='input'&&n.attrs.type==='search');
  const results=()=>list().children.filter(n=>n.tag==='button');
  const typeButton=label=>allNodes(root).find(n=>n.tag==='button'&&n.textContent===label);
  const scopeButton=label=>allNodes(allNodes(root).find(n=>(n.className||'').split(' ').includes('queue-filters')))
    .find(n=>n.tag==='button'&&n.textContent.trim().startsWith(label));
  const search=value=>{const field=input(),before=renders;field.value=value;field.listeners.input();assert.equal(input(),field);assert.equal(renders,before);};
  assert.equal(results().length,4);
  search('  кПз  ');
  assert.equal(results().length,2);
  typeButton('Консультації').listeners.click();
  assert.equal(input().value,'  кПз  ');
  assert.equal(typeButton('Консультації').attrs['aria-pressed'],'true');
  assert.equal(results().length,1);
  assert.ok(list().textContent.includes('Консультація'));
  scopeButton('Мої').listeners.click();
  assert.equal(results().length,0);
  assert.ok(list().textContent.includes('Нічого не знайдено'));
  assert.ok(root.textContent.includes('Знайдено: 0'));
  typeButton('Звичайні пари').listeners.click();
  assert.equal(results().length,1);
  assert.equal(app.S.queueFilter,'mine');
  search(' КОНДРАТЮК ');
  assert.equal(results().length,0);
  scopeButton('Усі').listeners.click();
  assert.equal(results().length,1);
  assert.ok(list().textContent.includes('ЛМВ'));
  assert.ok(!list().textContent.includes('Курсова'));
  typeButton('Усі типи').listeners.click();
  assert.equal(results().length,2);
  search('');
  assert.equal(results().length,4);
  search('невідомий предмет');
  assert.equal(results().length,0);
  assert.equal(JSON.stringify(app.S.data.queues),original);
});



test('admin mode switch remains visible in mobile topbar and profile',()=>{
  const {app}=setup(true,{open(){}});
  app.S.mode='student';app.S.data.me.is_admin=true;
  assert.ok(allNodes(app.design.topbar()).some(n=>n.tag==='button'&&n.attrs['aria-label']==='Режим адміністратора'));
  assert.ok(allNodes(app.design.profilePage()).some(n=>n.tag==='button'&&n.textContent.includes('Режим адміністратора')));
  app.S.data.me.is_admin=false;
  assert.ok(!allNodes(app.design.topbar()).some(n=>n.attrs['aria-label']==='Режим адміністратора'));
});

test('bootstrap selects admin mode once and preserves deliberate student mode',async()=>{
  const {app,context}=setup(false,{open(){}});
  app.setRender(()=>{});app.S.tab='home';app.S.modeInitialized=false;
  context.fetch=async url=>result(String(url).includes('/admin/users')?{users:[]}:{me:{name:'Власник',is_admin:true,is_registered:true},queues:[],swaps:[],today:'2026-11-06'});
  await app.load();assert.equal(app.S.mode,'admin');assert.equal(app.S.tab,'admin');
  app.S.mode='student';app.S.tab='queues';
  await app.load();assert.equal(app.S.mode,'student');
});

test('admin journal entry requests full group scope',()=>{
  const calls=[];const {app}=setup(true,{open:(...args)=>calls.push(args)});
  app.S.mode='admin';app.S.data.me.is_admin=true;
  const link=app.design.navigation().querySelectorAll('[data-page]').find(n=>n.dataset.page==='journal');
  link.listeners.click();
  assert.equal(calls[0][0],'journal');assert.match(calls[0][2],/scope=group/);
});

for (const design of [false, true]) test('remaining subject pairs appear in queue lists and details, design='+design, () => {
  const {app} = setup(design);
  const queue = {id:1,subject:'КПЗ',teacher:'Щуцький',type:'lesson',status:'open',count:0,my_position:null,labs:[],entries:[],
    pair_plan:{remaining:2,finish_date:'2026-12-03'}};
  app.S.data.queues = [queue];
  app.S.queue = queue;
  const detail = /Залишилось пар: 2 · остання пара 03\.12\.2026/;
  assert.match(app.queuesPage().textContent, detail);
  assert.match(app.queuePage().textContent, detail);
  queue.pair_plan = null;
  assert.doesNotMatch(app.queuePage().textContent, /Залишилось пар/);
});

for (const design of [false, true]) test('consultation queues display selected labs and planned time, design='+design, () => {
  const {app} = setup(design);
  app.S.queue = {id:1,subject:'КПЗ',teacher:'Щуцький',type:'consultation',status:'open',count:1,my_position:1,
    labs:[{number:1,max_minutes:40}],entries:[{id:1,name:'Студент',is_me:true,position:1,labs:[1],planned_minutes:30}]};
  const text = app.queuePage().textContent;
  assert.match(text, /№1/);
  assert.match(text, /30 хв/);
  assert.match(text, /Здав/);
  assert.match(text, /Лабораторні та час/);
});
