/* Offline browser integration of the Figma adaptation against synthetic API data.
 * node scripts/check_figma_design.cjs [path-to-playwright] [output-directory]
 */
const {chromium}=require(process.argv[2]||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const assets=path.resolve(__dirname,'../queue_bot/mini_app/static');
for(const name of ['bootstrap-icons.woff2','manrope-400.ttf','manrope-500.ttf','manrope-600.ttf','manrope-700.ttf','manrope-800.ttf'])assert.ok(fs.statSync(path.join(assets,name)).size>1000,name+' is empty');
const out=process.argv[3]; if(out)fs.mkdirSync(out,{recursive:true});
const queues=[{id:1,subject:'Машинне навчання',teacher:'Барабаш Д.С.',status:'open',type:'lesson',count:2,my_position:null},
 {id:2,subject:'Людино-машинна взаємодія',teacher:'Кондратюк Є.С.',status:'open',type:'lesson',count:4,my_position:4,my_labs:[1],my_planned_minutes:10},
 {id:3,subject:'Курсова робота з КПЗ',teacher:'Жереб Д.В.',status:'open',type:'consultation',count:0,my_position:null}];
let joined=false,conflict=false,joinCalls=0,anonymous=false;
function detail(id){const q=queues.find(q=>q.id===id);const own=id===2;
 const entries=id===3?[]:[{id:11,position:1,name:'Коваль Андрій',vacant:false,is_me:false,labs:[1],planned_minutes:10,entry_revision:0},
 {id:12,position:2,name:conflict?'Інший студент':'Вільне місце',vacant:!conflict,is_me:false,labs:null,entry_revision:0},
 {id:13,position:3,name:'Шевчук Роман',vacant:false,is_me:false,labs:[1],entry_revision:0},
 ...(own?[{id:14,position:4,name:'Євчук Михайло',is_me:true,labs:[1],planned_minutes:10,entry_revision:0}]:[])];
 return {...q,labs:id===3?[]:[{number:1,title:'Лабораторна 1',max_minutes:30},{number:2,title:'Лабораторна 2',max_minutes:20}],entries,
 can_pick_position:true,append_position:own?5:4,join_slots:Array.from({length:10},(_,i)=>({number:i+1,state:own&&i===3?'yours':i===0||i===2||(conflict&&i===1)?'busy':'free'}))};}
(async()=>{const browser=await chromium.launch({channel:process.env.PI231_BROWSER_CHANNEL||'chrome',headless:true});try{
 const page=await browser.newPage(),errors=[],failedAssets=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://telegram.org/**',r=>r.fulfill({contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:'+JSON.stringify(anonymous?'':'offline')+',colorScheme:"light",ready(){},expand(){},onEvent(){}}}'}));
 await page.route('https://design.invalid/**',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(p==='/auth/session')return route.fulfill({json:{authenticated:false,login_enabled:true}});
  if(p==='/api/miniapp/bootstrap')return route.fulfill({json:{today:'2026-11-02',me:{name:'Євчук Михайло',is_registered:true,is_admin:true,is_owner:true},queues,swaps:[],next_lesson:{date:'2026-11-02',startTime:'11:50',endTime:'13:10',pair:3,lessons:[{subject:'Машинне навчання',room:'231'}]}}});
  if(p.startsWith('/api/miniapp/avatar/'))return route.fulfill({status:204});
  if(p==='/api/miniapp/admin/users')return route.fulfill({json:{users:[{id:1,name:'Тест Студент'}]}});
  if(/^\/api\/miniapp\/queues\/\d+$/.test(p))return route.fulfill({json:detail(Number(p.split('/').pop()))});
  if(p==='/api/miniapp/queues/1/join'){
   joinCalls++;const data=route.request().postDataJSON();assert.deepEqual(data.labs,[1,2]);assert.ok(data.minutes>=5&&data.minutes<=50);
   if(joinCalls===1){conflict=true;return route.fulfill({status:400,json:{error:'Місце №2 уже зайняте. Оберіть інший вільний номер.'}});}
   assert.equal(data.position,5);joined=true;queues[0].my_position=5;return route.fulfill({json:{queue_id:1,position:5,message:'Ви записалися.'}});
  }
  if(p==='/api/miniapp/schedule')return route.fulfill({json:{date:url.searchParams.get('date'),day:'Понеділок',week_type:'numerator',lessons:[{subject:'Машинне навчання'}],slots:Array.from({length:5},(_,i)=>({number:i+1,startTime:['08:30','10:00','11:50','13:20','14:50'][i],endTime:['09:50','11:20','13:10','14:40','16:10'][i],lessons:i===2?[{subject:'Машинне навчання',teacher:'Барабаш Д.С.',room:'231'}]:[]}))}});
  if(p.startsWith('/api/miniapp/'))return route.fulfill({json:{items:[],users:[],sessions:[],subjects:[],teachers:[],polls:[],reports:[],archived:[],stats:{},summary:{},server_time:'2026-11-02T10:00:00+02:00'}});
  const name=p.endsWith('/')?'index.html':path.basename(p),file=path.join(assets,name);
  if(!fs.existsSync(file)){failedAssets.push(name);return route.fulfill({status:404});}
  const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf','.png':'image/png'};
  return route.fulfill({body:fs.readFileSync(file),contentType:types[path.extname(name)]||'text/plain'});
 });
 const settle=async()=>{await page.waitForFunction(()=>!document.querySelector('.progress'));await page.evaluate(()=>document.fonts.ready);await page.waitForFunction(()=>[...document.getAnimations()].every(a=>a.playState==='finished'||a.effect?.getTiming().iterations===Infinity));};
 const noOverflow=async(label)=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label);
 for(const width of [320,390,768,1024,1440,1920]){
  await page.setViewportSize({width,height:900});await page.goto('https://design.invalid/app/');await page.locator('.make-home').waitFor();await settle();
  await noOverflow('home '+width);
  assert.equal(await page.evaluate(()=>getComputedStyle(document.body).fontFamily.includes('Manrope')),true);
  assert.equal(await page.evaluate(()=>document.fonts.check('16px bootstrap-icons')),true);
  assert.equal(await page.locator('.quick-action .bi').first().evaluate(node=>{const css=getComputedStyle(node,'::before');return css.fontFamily.includes('bootstrap-icons')&&css.content!=='none'&&node.getBoundingClientRect().width>0;}),true,'Bootstrap icon slot missing');
  if(out&&[390,1440].includes(width))await page.screenshot({path:path.join(out,'figma-home-'+width+'.png'),fullPage:true});
 }
 await page.setViewportSize({width:390,height:844});await page.reload();await page.locator('.make-home').waitFor();await settle();
 await page.locator('.mobile-top button[aria-label="Оформлення"]').click();
 const modal=page.getByRole('dialog',{name:'Оформлення'});
 for(const theme of ['Темна','Світла','Системна']){
  await modal.getByRole('button',{name:theme+' тема',exact:true}).click();
  for(const accent of ['Лаймовий','Синій','Фіолетовий','Бірюзовий','Рожевий','Помаранчевий']){
   await modal.getByRole('button',{name:accent+' акцент',exact:true}).click();await noOverflow(theme+' '+accent);
  }
 }
 await modal.getByRole('button',{name:'Фіолетовий акцент',exact:true}).click();
 await modal.getByRole('checkbox').check();await modal.getByRole('button',{name:'Готово',exact:true}).click();
 await page.reload();await page.locator('.make-home').waitFor();
 assert.equal(await page.evaluate(()=>document.documentElement.dataset.accent),'violet');
 assert.equal(await page.evaluate(()=>document.documentElement.classList.contains('reduce-motion')),true);
 await page.locator('.bottom-nav button[data-page="queues"]').click();await page.locator('.make-queues').waitFor();await settle();
 await page.getByRole('textbox',{name:'Знайти предмет або викладача'}).fill('Машинне');assert.equal(await page.locator('.make-queues .queue-row').count(),1);
 await page.locator('.make-queues .queue-row').click();await page.locator('.make-detail').waitFor();await settle();await noOverflow('queue detail');
 await page.getByRole('button',{name:'Переглянути місця',exact:true}).click();
 await page.getByRole('button',{name:'Лабораторна №1',exact:false}).click();await page.getByRole('button',{name:'Лабораторна №2',exact:false}).click();
 await page.getByRole('button',{name:'Продовжити',exact:true}).click();await page.getByRole('button',{name:'15 хв',exact:true}).click();await page.getByRole('button',{name:'Продовжити',exact:true}).click();
 assert.equal(await page.getByRole('button',{name:'Місце 1, зайняте',exact:true}).isEnabled(),false);
 await page.getByRole('button',{name:'Місце 2, вільне',exact:true}).click();await page.getByRole('button',{name:'Продовжити',exact:true}).click();
 await page.getByRole('button',{name:'Зайняти місце №2',exact:true}).click();await page.getByRole('alert').filter({hasText:'уже зайняте'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Місце 2, зайняте',exact:true}).isEnabled(),false);
 await page.getByRole('button',{name:'Місце 5, вільне',exact:true}).click();await page.getByRole('button',{name:'Продовжити',exact:true}).click();
 await page.getByRole('button',{name:'Зайняти місце №5',exact:true}).click();await page.locator('.success-modal').waitFor();assert.ok(joined);
 assert.match(await page.locator('.success-modal').textContent(),/Місце №5/);if(out)await page.screenshot({path:path.join(out,'figma-join-success.png'),fullPage:true});
 await page.getByRole('button',{name:'Готово',exact:true}).click();
 await page.locator('.bottom-nav button[data-page="queues"]').click();await page.locator('.make-queues').waitFor();await settle();
 await page.getByRole('textbox',{name:'Знайти предмет або викладача'}).fill('Курсова');await page.locator('.make-queues .queue-row').click();await page.locator('.make-detail').waitFor();await settle();
 await page.getByRole('button',{name:'Переглянути місця',exact:true}).click();assert.equal(await page.locator('.wizard-progress > div').count(),2,'consultation must skip labs/time');
 assert.match(await page.locator('.wizard-panel').textContent(),/Обери вільне місце/);await page.getByRole('button',{name:'Скасувати',exact:true}).click();
 await page.locator('.bottom-nav button[data-page="schedule"]').click();await page.locator('.timeline .lesson-row').first().waitFor();await settle();assert.equal(await page.locator('.timeline .lesson-row').count(),5);await noOverflow('schedule');
 await page.locator('.bottom-nav button[data-page="profile"]').click();await page.locator('.make-profile').waitFor();await settle();await noOverflow('profile');
 await page.getByRole('button',{name:'Усі розділи',exact:true}).click();await page.locator('.mode-switch').click();await page.locator('.make-admin').waitFor();await settle();await noOverflow('admin');
 if(out)await page.screenshot({path:path.join(out,'figma-admin-mobile.png'),fullPage:true});
 for(const key of ['admin-history','admin-group','admin-subjects','admin-replacements','admin-saturdays','admin-polls','admin-reports','admin-bugs','owner']){
  await page.getByRole('button',{name:'Усі розділи',exact:true}).click();
  await page.locator('.sidebar button[data-page="'+key+'"]').click();
  await page.waitForFunction(()=>!document.querySelector('main .loading'));
  await settle();await noOverflow(key);
  assert.equal(await page.locator('main .notice.error:visible').count(),0,key+' failed to render: '+await page.locator('main').textContent());
  assert.equal(await page.locator('main input, main textarea, main button').count()>0,true,key+' controls missing');
 }
 await page.getByRole('button',{name:'Усі розділи',exact:true}).click();await page.locator('.mode-switch').click();await page.locator('.make-home').waitFor();
 for(const key of ['swaps','polls','help']){
  await page.getByRole('button',{name:'Усі розділи',exact:true}).click();await page.locator('.sidebar button[data-page="'+key+'"]').click();
  await settle();await noOverflow(key);
 }
 anonymous=true;await page.goto('https://design.invalid/app/?login=failed&reason=profile_missing');await page.locator('.make-login').waitFor();await settle();
 assert.equal(await page.getByRole('link',{name:'Увійти через Telegram',exact:true}).getAttribute('href'),'/auth/telegram/start');
 assert.match(await page.locator('.login-card').textContent(),/profile_missing/);assert.equal(await page.locator('.mobile-top').count(),0);await noOverflow('login');
 if(out)await page.screenshot({path:path.join(out,'figma-login.png'),fullPage:true});
 assert.deepEqual(errors,[]);assert.deepEqual(failedAssets,[]);console.log('PASS: real Figma shell 320–1920px; local assets; themes/palette/motion persistence; queue wizard conflict + success; schedule/profile/admin; no JS errors or overflow');
 }finally{await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
