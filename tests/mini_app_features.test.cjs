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
  return { S, features, panels, notices, context, renders: () => renders };
}
const deferred = () => {
  let resolve, reject;
  const promise = new Promise((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { resolve, reject, promise };
};

const journalData = (group=false) => ({period:'2026-2027:1',periods:['2026-2027:1'],group,can_set_dates:group,
  viewer_id:1,subjects:[{key:'кпз',name:'КПЗ',labs:[{number:1,parts:1},{number:2,parts:1},{number:3,parts:1}]}],students:[{id:1,name:'Петренко Іван'},{id:2,name:'Коваль Андрій'}],dates:[],
  records:[5,2,3].map((grade,i)=>({id:i+1,user_id:1,subject_key:'кпз',subject_name:'КПЗ',student_name:'Петренко Іван',kind:'lab',number:i+1,part:1,can_edit:true,grade,revision:0,note:''}))});

test('personal journal opens a subject overview without roster tables',async()=>{
  const data=journalData();
  data.subjects=Array.from({length:8},(_,i)=>({...data.subjects[0],key:'subject-'+i,name:'Предмет '+(i+1)}));
  const {features}=setup(async()=>data);
  await features.open('journal');
  const page=features.page('journal');
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-subject-card').length,8);
  assert.equal(descendants(page).filter(n=>n.tag==='table').length,0);
  assert.doesNotMatch(page.textContent,/Коваль Андрій|Борги|Не здано/);
  assert.match(page.textContent,/Внесено оцінок: 0 із 3/);
});

test('admin edits another students grade inline in group mode without approval',async()=>{
  const calls=[];
  const data=journalData(true);
  data.records.forEach(r=>{r.user_id=2;r.student_name='Коваль Андрій';});
  const {features,S,panels}=setup(async(path,body)=>{calls.push([path,body]);return body?{message:'Збережено'}:data;});
  S.mode='admin';
  await features.open('journal',false,'?period=2026-2027:1&scope=group');
  const nodes=descendants(features.page('journal'));
  assert.ok(!nodes.some(n=>n.tag==='button'&&/Підтвердити|виправлення/.test(n.textContent)));
  const cell=nodes.find(n=>n.tag==='input'&&n.attrs['aria-label']==='Коваль Андрій, ЛР 1, оцінка 1');
  assert.ok(cell);
  cell.value='3';
  await cell.events.change({target:cell});
  assert.equal(panels.length,0);
  assert.equal(calls[1][0],'/journal');
  assert.deepEqual([calls[1][1].action,calls[1][1].user_id,calls[1][1].record_id,calls[1][1].revision,calls[1][1].grade],['submit',2,1,0,3]);
  assert.match(calls[2][0],/period=2026-2027%3A1&scope=group/);
});

test('personal work editor saves only viewer grades and retains selected subject',async()=>{
  const data=journalData();data.records=[];
  data.subjects[0].labs=[{number:1,parts:5,attestation:1},{number:2,parts:1,attestation:2}];
  const calls=[];
  const {features,panels}=setup(async(path,body)=>{calls.push([path,body]);return body?{message:'Збережено'}:data;});
  await features.open('journal',false,'?period=2026-2027:1&subject='+encodeURIComponent('кпз'));
  const page=features.page('journal');
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-work').length,6);
  assert.equal(descendants(page).filter(n=>n.tag==='table').length,0);
  assert.match(page.textContent,/Не внесено/);
  let work=descendants(page).filter(n=>n.attrs.class==='personal-work')[4];
  descendants(work).find(n=>n.tag==='button'&&n.textContent==='Внести оцінку').events.click();
  work=descendants(page).filter(n=>n.attrs.class==='personal-work')[4];
  const grade=descendants(work).find(n=>n.tag==='input'&&n.attrs.type==='number');
  grade.value='4';
  const note=descendants(work).find(n=>n.tag==='input'&&n!==grade);note.value='';
  await descendants(work).find(n=>n.tag==='form').events.submit({preventDefault(){}});
  assert.equal(panels.length,0);
  assert.equal(calls[1][1].grade,4);
  assert.equal(calls[1][1].part,5);
  assert.equal(calls[1][1].user_id,1);
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-work').length,6);
});

test('personal journal excludes other students records from its work cards',async()=>{
  const data=journalData();data.records.forEach(r=>{r.user_id=2;r.can_edit=false;});
  const {features}=setup(async()=>data);
  await features.open('journal',false,'?subject='+encodeURIComponent('кпз'));
  const page=features.page('journal');
  assert.doesNotMatch(page.textContent,/Коваль Андрій/);
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-grade').length,0);
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-missing').length,3);
});

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

test('administrator names accompany identifiers with a truthful unregistered fallback', async()=>{
  const {S,features}=setup(async()=>({items:[{telegram_id:123,full_name:'Петренко Іван'},{telegram_id:456,full_name:null}]}));
  S.mode='admin';await features.open('admin-admins');const text=features.page('admin-admins').textContent;
  assert.match(text,/Петренко Іван/);assert.match(text,/Telegram ID: 123/);
  assert.match(text,/Профіль ще не зареєстрований/);assert.match(text,/Telegram ID: 456/);
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
  await features.open("admin-history");
  assert.equal(S.fatal, "Доступ відкликано");
  assert.ok(renders() >= 2);
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

test("bug screenshots still open the authenticated image helper", async () => {
  const images=[];
  const {features,S}=setup(async()=>({items:[{id:8,text:'Помилка',has_photo:true,status:'pending'}]}),
    {openImage:async path=>images.push(path)});
  S.mode='admin';
  await features.open('admin-bugs');
  const photo=descendants(features.page('admin-bugs')).find(node=>node.tag==='button'&&node.textContent.includes('скріншот'));
  assert.ok(photo);await photo.events.click();
  assert.deepEqual(images,['/bugs/8/photo']);
});

test("removed reports cannot open or issue API requests", async()=>{
  const requests=[];
  const {features,S}=setup(async path=>{requests.push(path);return {};});
  S.mode='admin';
  await features.open('admin-reports');
  assert.deepEqual(requests,[]);
  assert.notEqual(S.feature,'admin-reports');
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
    session_id: path.includes("sid=") ? Number(path.split("sid=")[1]) : null,
  }));
  S.mode = "admin";
  await features.open("admin-history");
  const section = descendants(features.page("admin-history")).find(node => node.attrs.class === "history-subject" && node.textContent.includes("Машинне навчання"));
  const button = descendants(section).find(node => node.tag === "button" && node.textContent === "Учасники сесії");
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

test('open Saturday screen archives past 13:30 using server time without resetting the form',async()=>{
  let clock=Date.parse('2026-10-31T13:29:59+02:00');
  const {features,S,context}=setup(async()=>({server_time:'2026-10-31T13:29:59+02:00',items:[
    {date:'2026-10-31',source_day:'monday',archive_at:'2026-10-31T13:30:00+02:00'},
    {date:'2026-11-07',source_day:'tuesday',archive_at:'2026-11-07T13:30:00+02:00'},
  ],archived:[]}));
  context.Date=class extends Date {static now(){return clock;}};
  S.mode='admin';await features.open('admin-saturdays');
  const page=features.page('admin-saturdays');
  const formDate=descendants(page).find(n=>n.tag==='input'&&n.attrs.type==='date');
  formDate.value='2026-11-21';
  assert.equal(descendants(page).filter(n=>n.tag==='button'&&n.textContent==='Повідомити групу').length,2);
  clock+=2000;features.tick();
  const archive=descendants(page).find(n=>n.tag==='details');
  assert.match(archive.textContent,/2026-10-31/);
  assert.doesNotMatch(archive.textContent,/2026-11-07/);
  assert.equal(descendants(archive).filter(n=>n.tag==='button').length,0);
  assert.equal(descendants(page).filter(n=>n.tag==='button'&&n.textContent==='Повідомити групу').length,1);
  assert.equal(formDate.value,'2026-11-21');
});

test("history groups attempts by stable student identity and renders Kyiv timestamps", async()=>{
 const {features,S}=setup(async()=>({session_id:5,sessions:[{id:5,subject:'КПЗ',date:'2026-10-31 21:37:45.651405+00:00',status:'archived'}],entries:[
 {id:1,user_id:7,full_name:'Один Студент',status:'completed',created_at:'2026-10-31 20:00:00',completed_at:'2026-10-31T21:38:09.602856+00:00'},
 {id:2,user_id:7,full_name:'Один Студент',status:'completed',created_at:'2026-10-31 21:00:00',completed_at:'2026-10-31T21:39:19Z'},
 {id:3,user_id:8,full_name:'Один Студент',status:'active',position:1,created_at:'2026-07-01 21:30:00'}]}));
 S.mode='admin';await features.open('admin-history');const page=features.page('admin-history');
 assert.equal(descendants(page).filter(n=>n.attrs.class==='card request-card history-person').length,2);
 assert.match(page.textContent,/Учасники сесії · 2/);assert.match(page.textContent,/23:37/);assert.match(page.textContent,/02.07.2026, 00:30/);
 assert.doesNotMatch(page.textContent,/T21:|651405|602856/);assert.match(page.textContent,/Історія записів · 2/);
});

test('arrow keys move focus between journal grade cells like a spreadsheet',async()=>{
  const data=journalData(true);
  const {features,S}=setup(async()=>data);
  S.mode='admin';
  await features.open('journal',false,'?period=2026-2027:1&scope=group');
  const keydown=descendants(features.page('journal')).find(n=>n.tag==='input'&&n.events.keydown).events.keydown;
  // 2x3 grid: name cell, two inputs, an average cell without input.
  let focused=null;
  const rows=[0,1].map(()=>({children:[]}));
  rows.forEach((tr,r)=>{
    for(let c=0;c<4;c++){
      const td={cellIndex:c,parentElement:tr};
      const box=c===1||c===2?{value:'4',selectionStart:0,selectionEnd:0,closest:()=>td,focus(){focused=this;},id:`${r}:${c}`,blur(){focused=null;}}:null;
      td.querySelector=()=>box;tr.children.push(td);
    }
    tr.children.forEach((td,i)=>{td.previousElementSibling=tr.children[i-1]||null;td.nextElementSibling=tr.children[i+1]||null;});
  });
  rows[0].nextElementSibling=rows[1];rows[1].previousElementSibling=rows[0];
  const box=(r,c)=>rows[r].children[c].querySelector();
  const press=(key,target,extra={})=>{Object.assign(target,extra);keydown({key,target,preventDefault(){}});};
  press('ArrowRight',box(0,1),{selectionStart:1,selectionEnd:1});
  assert.equal(focused.id,'0:2');
  focused=null;press('ArrowRight',box(0,2),{selectionStart:1,selectionEnd:1});
  assert.equal(focused,null);
  press('ArrowDown',box(0,2));
  assert.equal(focused.id,'1:2');
  press('ArrowUp',box(1,1));
  assert.equal(focused.id,'0:1');
  focused=null;press('ArrowLeft',box(0,2),{selectionStart:1,selectionEnd:1});
  assert.equal(focused,null);
  press('ArrowLeft',box(0,2),{selectionStart:0,selectionEnd:0});
  assert.equal(focused.id,'0:1');
});


test('coursework remains editable inside its parent subject using its original key',async()=>{
  const data=journalData();data.subjects[0].name='Конструювання програмного забезпечення';
  data.subjects.push({key:'course',name:'Курсова робота з КПЗ',labs:[{number:1,parts:1}]});
  const calls=[];
  const {features}=setup(async(path,body)=>{calls.push([path,body]);return body?{}:data;});
  await features.open('journal');
  let page=features.page('journal');
  assert.equal(descendants(page).filter(n=>n.attrs.class==='personal-subject-card').length,1);
  descendants(page).find(n=>n.attrs.class==='personal-subject-card').events.click();
  let card=descendants(page).find(n=>n.attrs.class==='personal-work'&&n.textContent.includes('Курсова робота'));
  descendants(card).find(n=>n.tag==='button').events.click();
  card=descendants(page).find(n=>n.attrs.class==='personal-work'&&n.textContent.includes('Курсова робота'));
  descendants(card).find(n=>n.tag==='input'&&n.attrs.type==='number').value='5';
  descendants(card).find(n=>n.tag==='input'&&n.attrs.type!=='number').value='';
  await descendants(card).find(n=>n.tag==='form').events.submit({preventDefault(){}});
  assert.equal(calls[1][1].subject_key,'course');
  assert.equal(calls[1][1].user_id,1);
});

test('personal grade editing preserves revision and cancellation never writes',async()=>{
  const data=journalData(),calls=[];
  const {features}=setup(async(path,body)=>{calls.push([path,body]);return body?{}:data;});
  await features.open('journal',false,'?subject='+encodeURIComponent('кпз'));
  const page=features.page('journal');
  let work=descendants(page).find(n=>n.attrs.class==='personal-work');
  descendants(work).find(n=>n.tag==='button').events.click();
  work=descendants(page).find(n=>n.attrs.class==='personal-work');
  descendants(work).find(n=>n.tag==='button'&&n.textContent==='Скасувати').events.click();
  assert.equal(calls.length,1);
  work=descendants(page).find(n=>n.attrs.class==='personal-work');
  descendants(work).find(n=>n.tag==='button').events.click();
  work=descendants(page).find(n=>n.attrs.class==='personal-work');
  descendants(work).find(n=>n.tag==='input'&&n.attrs.type==='number').value='4';
  descendants(work).find(n=>n.tag==='input'&&n.attrs.type!=='number').value='Уточнення';
  await descendants(work).find(n=>n.tag==='form').events.submit({preventDefault(){}});
  assert.equal(calls[1][1].record_id,1);
  assert.equal(calls[1][1].revision,0);
  assert.equal(calls[1][1].note,'Уточнення');
});

test('admin subject selector keeps every student in the full grade table',async()=>{
  const data=journalData(true);
  data.subjects.push({key:'ml',name:'Машинне навчання',labs:[{number:1,parts:2}]});
  data.students.push({id:3,name:'Новий Студент'});
  const {features,S}=setup(async()=>data);S.mode='admin';
  await features.open('journal',false,'?scope=group');
  const page=features.page('journal');
  const selector=descendants(page).find(n=>n.tag==='select'&&n.attrs['aria-label']==='Предмет журналу групи');
  assert.ok(selector);selector.value='ml';selector.events.change();
  const rows=descendants(page).filter(n=>n.tag==='th'&&n.attrs.scope==='row').map(n=>n.textContent);
  assert.deepEqual(rows,['Петренко Іван','Коваль Андрій','Новий Студент']);
  assert.match(page.textContent,/Машинне навчання/);
  assert.equal(descendants(page).filter(n=>n.tag==='input'&&String(n.attrs.class||'').includes('journal-input')).length,6);
});
