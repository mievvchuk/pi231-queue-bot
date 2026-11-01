/* Offline browser checks for reports, history and student Telegram profiles.
 * node scripts/check_admin_views.cjs [path-to-playwright] [output-directory]
 */
const {chromium}=require(process.argv[2]||'playwright');
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const assets=path.resolve(__dirname,'../queue_bot/mini_app/static');
for(const name of ['bootstrap-icons.woff2','manrope-400.ttf','manrope-500.ttf','manrope-600.ttf','manrope-700.ttf','manrope-800.ttf'])assert.ok(fs.statSync(path.join(assets,name)).size>1000,name+' is empty');
const out=process.argv[3]; if(out)fs.mkdirSync(out,{recursive:true});
const queues=[];let anonymous=false;
(async()=>{const browser=await chromium.launch({channel:process.env.PI231_BROWSER_CHANNEL||'chrome',headless:true});try{
 const page=await browser.newPage(),errors=[],failedAssets=[],mediaRequests=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('https://telegram.org/**',r=>r.fulfill({contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:'+JSON.stringify(anonymous?'':'offline')+',colorScheme:"light",ready(){},expand(){},onEvent(){}}}'}));
 await page.route('https://design.invalid/**',async route=>{
  const url=new URL(route.request().url()),p=url.pathname;
  if(p==='/auth/session')return route.fulfill({json:{authenticated:false,login_enabled:true}});
  if(p==='/api/miniapp/bootstrap')return route.fulfill({json:{today:'2026-11-02',me:{name:'Євчук Михайло',is_registered:true,is_admin:true,is_owner:true},queues,swaps:[],next_lesson:{date:'2026-11-02',startTime:'11:50',endTime:'13:10',pair:3,lessons:[{subject:'Машинне навчання',room:'231'}]}}});
  if(p.startsWith('/api/miniapp/avatar/'))return route.fulfill({status:204});
  if(p==='/api/miniapp/admin/users'||p==='/api/miniapp/admin/manage/group')return route.fulfill({json:{users:[{id:1,telegram_id:123,name:'Євчук Михайло',protected:true},{id:2,telegram_id:456,name:'Шевчук Роман'},{id:3,telegram_id:-1,name:'Студент без Telegram'}]}});
  if(p.endsWith('/telegram'))return route.fulfill({json:{username:p.includes('/1/')?'example_student':null,username_available:true,has_photo:p.includes('/1/')}});
  if(p.endsWith('/avatar'))return route.fulfill({contentType:'image/png',body:Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=','base64')});
  if(p==='/api/miniapp/admin/manage/reports')return route.fulfill({json:{subjects:[],statistics:{registered_users:22,placeholder_users:2,active_sessions:16,archived_sessions:5,pass_rate:67,lessons_count:11,consults_count:3,cw_count:7,total_passed:59,total_removed:29,total_left:20,total_active:89}}});
  if(p==='/api/miniapp/admin/manage/history')return route.fulfill({json:{session_id:url.searchParams.has('sid')?11:null,sessions:[{id:11,subject_id:1,subject:'Людино-машинна взаємодія',teacher:'Кондратюк Є.С.',date:'2026-10-31T21:37:45.651405+00:00',status:'archived'},{id:12,subject_id:1,subject:'Людино-машинна взаємодія',teacher:'Кондратюк Є.С.',date:'2026-10-20 14:01:11',status:'open'},{id:13,subject_id:2,subject:'Машинне навчання',date:'2026-10-30 14:00:00',status:'open'}],entries:[{id:1,user_id:1,full_name:'Євчук Михайло',status:'completed',created_at:'2026-10-31 21:00:00',completed_at:'2026-10-31T21:38:09.602856+00:00'},{id:2,user_id:1,full_name:'Євчук Михайло',status:'completed',created_at:'2026-10-31 21:00:00',completed_at:'2026-10-31T21:39:19.602856+00:00'}]}});
  if(p==='/api/miniapp/admin/manage/admins')return route.fulfill({json:{items:[{telegram_id:123,full_name:'Петренко Приклад'},{telegram_id:456,full_name:null}]}});
  if(p.startsWith('/api/miniapp/'))return route.fulfill({json:{items:[],users:[],sessions:[],subjects:[],teachers:[],polls:[],reports:[],archived:[],stats:{},summary:{},server_time:'2026-11-02T10:00:00+02:00'}});
  const name=p.endsWith('/')?'index.html':path.basename(p),file=path.join(assets,name);
  if(!fs.existsSync(file)){failedAssets.push(name);return route.fulfill({status:404});}
  if(name.endsWith('.mp4'))mediaRequests.push(name);
  const types={'.html':'text/html','.js':'text/javascript','.css':'text/css','.woff2':'font/woff2','.woff':'font/woff','.ttf':'font/ttf','.png':'image/png','.jpg':'image/jpeg','.mp4':'video/mp4','.vtt':'text/vtt'};
  return route.fulfill({body:fs.readFileSync(file),contentType:types[path.extname(name)]||'text/plain'});
 });
 const settle=async()=>{await page.waitForFunction(()=>!document.querySelector('.progress'));await page.evaluate(()=>document.fonts.ready);await page.waitForFunction(()=>[...document.getAnimations()].every(a=>a.playState==='finished'||a.effect?.getTiming().iterations===Infinity));};
 const noOverflow=async(label)=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,label);
 for(const width of [320,390,768,1440]){
  await page.setViewportSize({width,height:900});await page.goto('https://design.invalid/app/');await page.locator('.make-home').waitFor();
  if(await page.getByRole('button',{name:'Усі розділи',exact:true}).isVisible())await page.getByRole('button',{name:'Усі розділи',exact:true}).click();await page.locator('.mode-switch').click();await page.locator('.make-admin').waitFor();
  for(const key of ['admin-reports','admin-history','admin-group']){
   if(await page.getByRole('button',{name:'Усі розділи',exact:true}).isVisible())await page.getByRole('button',{name:'Усі розділи',exact:true}).click();await page.locator('.sidebar button[data-page="'+key+'"]').click();await page.waitForFunction(()=>!document.querySelector('main .loading'));await settle();await noOverflow(key+' '+width);
   if(key==='admin-reports'){
    assert.equal(await page.locator('.report-stat').count(),5);assert.equal(await page.locator('.report-charts>.card').count(),2);
    assert.ok(await page.locator('.report-stat').first().evaluate(n=>n.getBoundingClientRect().height<200),'metric card stretched');
   }
   if(key==='admin-history'){
    assert.equal(await page.locator('.history-subject').count(),2);assert.match(await page.locator('main').textContent(),/23:37/);
    await page.locator('.history-session').first().getByRole('button',{name:'Учасники сесії',exact:true}).click();await page.locator('.history-person').waitFor();await settle();
    assert.equal(await page.locator('.history-person').count(),1);await page.locator('.history-attempts summary').click();assert.equal(await page.locator('.history-attempt').count(),2);await noOverflow('participants '+width);
   }
   if(key==='admin-group'){
    await page.getByText('@example_student',{exact:true}).waitFor();await page.locator('.student-avatar img').waitFor();
    assert.equal(await page.locator('.student-avatar img').evaluate(n=>n.complete&&n.naturalWidth>0),true);assert.match(await page.locator('main').textContent(),/Без Telegram/);
    const search=page.getByPlaceholder('Пошук за ПІБ');await search.fill('Шевчук');assert.equal(await page.locator('.student-card').count(),1);await search.fill('');await page.getByText('@example_student',{exact:true}).waitFor();
   }
   if(out&&[390,1440].includes(width))await page.screenshot({path:path.join(out,key+'-'+width+'.png'),fullPage:true});
  }
 }
 assert.deepEqual(errors,[]);assert.deepEqual(failedAssets,[]);console.log('Admin reports, history, attempts and Telegram profiles: 320–1440px passed.');
 }finally{await browser.close();}})().catch(error=>{console.error(error);process.exitCode=1;});
