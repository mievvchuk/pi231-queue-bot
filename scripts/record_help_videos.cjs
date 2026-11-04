/* Reproducible, narrated UI tutorials. All identities and API responses are synthetic.
 * node scripts/record_help_videos.cjs [playwright-module] [output-dir] [ffmpeg-exe] [optional-topic] [narration-directory]
 * Never opens production or sends Telegram messages. FFmpeg/Playwright are authoring tools only.
 */
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const {execFileSync}=require('node:child_process');
const {chromium}=require(process.argv[2]||'playwright');
const assets=path.resolve(__dirname,'../queue_bot/mini_app/static');
const out=path.resolve(process.argv[3]||assets),ffmpeg=process.argv[4]||'ffmpeg';
const sandbox={window:{}};vm.runInNewContext(fs.readFileSync(path.join(assets,'help-videos.js'),'utf8'),sandbox);
const guides=sandbox.window.PI231VideoGuides.filter(g=>!process.argv[5]||g.id===process.argv[5]);
const narration=JSON.parse(fs.readFileSync(path.join(__dirname,'help_video_narration.json'),'utf8'));
const audioDir=path.resolve(process.argv[6]||path.join(__dirname,'../../../outputs/guide-audio'));
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,Math.max(0,ms)));
function fixture(topic){
 let joined=topic!=='join',completed=false,remaining=[1,2],accepted=false;
 const expires=new Date(Date.now()+600000).toISOString();
 const queue=()=>({id:1,subject:'Машинне навчання',teacher:'Викладач предмета',type:'lesson',status:'open',count:completed?2:joined?3:2,my_position:completed||!joined?null:accepted?3:2,my_labs:remaining,my_planned_minutes:remaining.length===2?15:10});
 const request=()=>({token:'synthetic-tutorial-request',session_id:1,status:accepted?'accepted':'pending',expires_at:expires,sender_name:'Інший Студент',target_name:'Студент Приклад',sender_position:accepted?2:3,target_position:accepted?3:2});
 return async route=>{
  const u=new URL(route.request().url()),p=u.pathname;
  if(u.host==='telegram.org')return route.fulfill({contentType:'text/javascript',body:'window.Telegram={WebApp:{initData:"",colorScheme:"light",ready(){},expand(){},onEvent(){}}}'});
  if(p==='/auth/session')return route.fulfill({json:{authenticated:true,csrf:'synthetic-only',login_enabled:true}});
  if(p==='/api/miniapp/bootstrap')return route.fulfill({json:{today:'2026-11-02',me:{name:'Студент Приклад',is_registered:true,is_admin:false,is_owner:false},queues:[queue()],swaps:topic==='swap'?[request()]:[],outgoing_swaps:[],next_lesson:{date:'2026-11-02',startTime:'11:50',endTime:'13:10',pair:3,lessons:[{subject:'Машинне навчання',room:'231'}]}}});
  if(p.startsWith('/api/miniapp/avatar/'))return route.fulfill({status:204});
  if(p==='/api/miniapp/queues/1')return route.fulfill({json:{...queue(),can_pick_position:true,append_position:4,labs:[{number:1,max_minutes:30,title:'Лабораторна 1'},{number:2,max_minutes:30,title:'Лабораторна 2'}],join_slots:Array.from({length:10},(_,i)=>({number:i+1,state:joined&&i===1&&!completed?'yours':i===0||i===2?'busy':'free'})),entries:[{id:1,name:'Перший Студент',position:1,is_me:false,labs:[1],planned_minutes:10,entry_revision:0},...(!completed&&joined?[{id:2,name:'Студент Приклад',position:accepted?3:2,is_me:true,labs:remaining,planned_minutes:remaining.length===2?15:10,entry_revision:0}]:[]),{id:3,name:'Інший Студент',position:accepted?2:3,is_me:false,labs:[1],planned_minutes:10,entry_revision:0}]}});
  if(p==='/api/miniapp/queues/1/join'){joined=true;return route.fulfill({json:{position:2,queue_id:1,message:'Ви записалися. Ваше місце: 2.'}});}
  if(p==='/api/miniapp/queues/1/complete'){if(remaining.length===2)remaining=[2];else completed=true;return route.fulfill({json:{message:completed?'Здачу завершено.':'Залишилась одна лабораторна. Місце збережено.'}});}
  if(p==='/api/miniapp/swaps/synthetic-tutorial-request'){accepted=true;return route.fulfill({json:{message:'Обмін підтверджено.'}});}
  if(p.startsWith('/api/'))return route.fulfill({status:404,json:{error:'Unconfigured offline tutorial route'}});
  const name=p.endsWith('/')?'index.html':path.basename(p),file=path.join(assets,name);
  if(!fs.existsSync(file))return route.fulfill({status:404});
  const type={'.html':'text/html','.js':'text/javascript','.css':'text/css','.ttf':'font/ttf','.woff2':'font/woff2','.woff':'font/woff','.png':'image/png','.jpg':'image/jpeg'}[path.extname(name)]||'application/octet-stream';
  return route.fulfill({body:fs.readFileSync(file),contentType:type});
 };
}
const wrapper=(title)=>`<!doctype html><html lang="uk"><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;font-family:Arial,sans-serif;background:#111410;color:#f1f4ec}
header{height:52px;display:flex;align-items:center;justify-content:space-between;padding:0 18px;font-size:14px;background:#1c211a;border-bottom:1px solid #404b37}header strong{color:#b9d46e}header small{font-size:10px;color:#c4cebb}
iframe{position:absolute;top:52px;bottom:144px;width:100%;height:calc(100% - 196px);border:0;background:#f4f5f0}
footer{position:absolute;bottom:0;height:144px;width:100%;padding:16px 20px;background:#1c211a;display:flex;flex-direction:column;gap:8px;border-top:2px solid #97b83e}footer small{font-size:11px;color:#b9d46e;text-transform:uppercase;letter-spacing:.06em}#caption{font-size:20px;line-height:1.4;font-weight:600;margin:0}
#tap{position:fixed;z-index:1000;pointer-events:none;width:42px;height:42px;border-radius:50%;border:3px solid #a1bf4b;background:#97b83e44;transform:translate(-50%,-50%);display:none}
#plate{position:absolute;z-index:10;left:7%;right:7%;top:24%;border:1px solid #97b83e;padding:24px;border-radius:22px;background:#1c211af5;box-shadow:0 10px 60px #0008;display:none}#plate h2{font-size:22px;color:#bbd474;margin:0 0 14px}#plate p{font-size:19px;line-height:1.65;white-space:pre-line;margin:0}
</style></head><body><header><strong>ПІ-231 · ${title}</strong><small>ТЕСТОВІ ДАНІ</small></header><iframe title="Навчальний приклад мініапки" src="/app/"></iframe><footer><small id="step">Відеоінструкція · з голосовим поясненням</small><p id="caption">${title}</p></footer><div id="tap"></div><section id="plate"><h2>Крок у браузері</h2><p></p></section></body></html>`;
function stamp(n){return new Date(Math.round(n*1000)).toISOString().slice(11,23);}
(async()=>{
 fs.mkdirSync(out,{recursive:true});const raw=path.resolve(out,'../../../..','outputs/video-raw');fs.mkdirSync(raw,{recursive:true});
 const browser=await chromium.launch({channel:process.env.PI231_BROWSER_CHANNEL||'chrome',headless:true});
 try{for(const guide of guides){
  const scenes=narration.guides[guide.id],duration=scenes.reduce((total,scene)=>total+scene.seconds,0);
  const audio=path.join(audioDir,'guide-'+guide.id+'.wav');if(!fs.existsSync(audio))throw new Error('Generate narration first: '+audio);
  const timings=[];let elapsed=0;for(const scene of scenes){timings.push(elapsed);elapsed+=scene.seconds;}
  const desktop=guide.id==='install-desktop';const size=desktop?{width:1280,height:800}:{width:480,height:960};
  const context=await browser.newContext({viewport:size,recordVideo:{dir:raw,size},...(guide.id==='install-phone'?{userAgent:'Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15 Version/18.0 Mobile/15E148 Safari/604.1'}:{})});
  const handle=fixture(guide.id);await context.route('**/*',route=>route.request().url()==='https://tutorial.invalid/tutorial.html'?route.fulfill({contentType:'text/html',body:wrapper(guide.title)}):handle(route));
  const before=Date.now(),page=await context.newPage(),errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto('https://tutorial.invalid/tutorial.html');const app=page.frameLocator('iframe');await app.locator('.make-home').waitFor();await page.frames()[1].evaluate(()=>document.fonts.ready);
  const start=Date.now(),offset=(start-before)/1000,captions=[],taps=[];
  const tap=async locator=>{const label=(await locator.textContent()||await locator.getAttribute('aria-label')||'Контроль').trim().slice(0,100);await locator.scrollIntoViewIfNeeded();const box=await locator.boundingBox();await page.evaluate(({x,y})=>{const n=document.getElementById('tap');n.style.left=x+'px';n.style.top=y+'px';n.style.display='block';},{x:box.x+box.width/2,y:box.y+box.height/2});await sleep(500);await locator.click();taps.push({at:(Date.now()-start)/1000,label});await sleep(1500);await page.evaluate(()=>document.getElementById('tap').style.display='none');};
  const scene=async(index,text,action)=>{
    await sleep(start+timings[index]*1000-Date.now());
    captions.push({text:scenes[index].text,start:timings[index],end:timings[index]+scenes[index].seconds});
    await page.evaluate(({text,index})=>{document.getElementById('caption').textContent=text;document.getElementById('step').textContent=(index+1)+' / 6 · українська озвучка';},{text,index});
    await sleep(800);if(action)await action();
    const end=start+(timings[index]+scenes[index].seconds)*1000;
    if(Date.now()>end+150)throw new Error(guide.id+' scene '+index+' ran beyond its narration timing; increase scene duration');
    await sleep(end-Date.now());
  };
  const queue=async()=>{await tap(app.locator(desktop?'.sidebar [data-page="queues"]':'.bottom-nav [data-page="queues"]'));await app.locator('.queue-row').waitFor();await tap(app.locator('.queue-row'));await app.locator('.make-detail').waitFor();};
  const plate=async text=>page.evaluate(text=>{const p=document.getElementById('plate');p.style.display='block';p.querySelector('p').textContent=text;},text);
  if(guide.id==='join'){
   await scene(0,'Як записатися на здачу: лабораторні, час і вільна комірка.');
   await scene(1,'Відкрий предмет у «Чергах» і натисни «Переглянути місця».',async()=>{await queue();await tap(app.getByRole('button',{name:'Переглянути місця',exact:true}));});
   await scene(2,'Познач потрібні лабораторні. Їхні ліміти часу сумуються.',async()=>{await tap(app.getByRole('button',{name:'Лабораторна №1',exact:false}));await tap(app.getByRole('button',{name:'Лабораторна №2',exact:false}));await tap(app.getByRole('button',{name:'Продовжити',exact:true}));});
   await scene(3,'Обери планований час. Мінімум — 5 хвилин.',async()=>{await tap(app.getByRole('button',{name:'15 хв',exact:true}));await tap(app.getByRole('button',{name:'Продовжити',exact:true}));});
   await scene(4,'Натисни вільну комірку й перевір деталі запису.',async()=>{await tap(app.getByRole('button',{name:'Місце 2, вільне',exact:true}));await tap(app.getByRole('button',{name:'Продовжити',exact:true}));});
   await scene(5,'Підтвердь запис. Галочка означає, що сервер зберіг твоє місце.',async()=>{await tap(app.getByRole('button',{name:'Зайняти місце №2',exact:true}));await app.locator('.success-modal').waitFor();});
  }else if(guide.id==='complete'){
   await scene(0,'Після здачі познач фактично завершені лабораторні.');
   await scene(1,'Відкрий свій запис у потрібній черзі.',queue);
   await scene(2,'Натисни «Здав лабораторні». Бот уточнить, які саме роботи здано.',()=>tap(app.getByRole('button',{name:'Здав лабораторні',exact:true})));
   await scene(3,'Познач здану лабораторну й підтвердь.',async()=>{await tap(app.getByRole('dialog').getByRole('checkbox').first());await tap(app.getByRole('button',{name:'Підтвердити здачу',exact:true}));});
   await scene(4,'Нездана робота залишилася на поточному місці.');
   await scene(5,'Коли залишилась одна робота, «Здав» завершує її без зайвого запитання.',()=>tap(app.getByRole('button',{name:'Здав',exact:true})));
  }else if(guide.id==='swap'){
   await scene(0,'На відповідь на запит обміну є 10 хвилин.');
   await scene(1,'Відкрий «Ще» → «Обміни».',async()=>{await tap(app.getByRole('button',{name:'Усі розділи',exact:true}));await tap(app.locator('.sidebar [data-page="swaps"]'));});
   await scene(2,'Перевір предмет, учасників і позиції. Таймер показує залишок часу.');
   await scene(3,'Щоб погодитися, натисни «Прийняти».',()=>tap(app.getByRole('button',{name:'Прийняти',exact:true})));
   await scene(4,'Позиції оновлено. Погоджений обмін можна застосувати повторно.');
   await scene(5,'Новий запит можна створити, натиснувши іншого учасника в деталях черги.');
  }else if(guide.id==='appearance'){
   await scene(0,'Налаштуй мініапку під себе. Вибір зберігається автоматично.');
   await scene(1,'Натисни палітру у верхній панелі.',()=>tap(app.locator('.mobile-top [aria-label="Оформлення"]')));
   await scene(2,'Обери темну, світлу або системну тему.',()=>tap(app.getByRole('button',{name:'Темна тема',exact:true})));
   await scene(3,'Натисни акцентний колір. Усього доступно шість палітр.',async()=>{await tap(app.getByRole('button',{name:'Фіолетовий акцент',exact:true}));});
   await scene(4,'Для мінімуму анімацій увімкни «Зменшений рух».',async()=>{await tap(app.getByRole('dialog').getByRole('checkbox'));});
   await scene(5,'Натисни «Готово» — оформлення вже збережене.',()=>tap(app.getByRole('button',{name:'Готово',exact:true})));
  }else{
   await scene(0,'Відкрий pi231-queue-bot.onrender.com/app/ у браузері та увійди через Telegram.');
   await scene(1,'Відкрий профіль. PWA працюватиме окремо від Telegram.',()=>tap(app.locator(desktop?'.sidebar [data-page="profile"]':'.bottom-nav [data-page="profile"]')));
   await scene(2,'Натисни «Встановити PWA» та прочитай підказку.',()=>tap(app.getByRole('button',{name:/Встановити PWA/})));
   if(desktop){
    await scene(3,'Наступний крок — у самому браузері.',()=>plate('Chrome / Edge\nНатисни значок встановлення біля адреси, якщо він є.\nАбо знайди встановлення застосунку в меню браузера.'));
    await scene(4,'Підтвердь встановлення у вікні браузера.',()=>plate('Підтвердь «Встановити».\nНазва й розташування пунктів залежать від версії браузера.'));
    await scene(5,'Запускай ПІ-231 як окремий застосунок із меню програм.',()=>plate('Готово!\nМініапка відкриватиметься окремим вікном.\nТвої черги залишаться тими самими.'));
   }else{
    await scene(3,'iPhone: у Safari відкрий «Поділитися» → «На початковий екран».',()=>plate('iPhone · Safari\nПоділитися → На початковий екран\nЗалиш «Відкривати як вебзастосунок» увімкненим, якщо цей пункт є.'));
    await scene(4,'Android: обери встановлення застосунку в меню Chrome.',()=>plate('Android · Chrome\nВідкрий меню браузера та обери встановлення застосунку.\nПідтвердь додавання.'));
    await scene(5,'Після підтвердження запускай застосунок із нової іконки.',()=>plate('Готово!\nВідкривай ПІ-231 з головного екрана.\nНазви пунктів залежать від версії браузера.'));
   }
  }
  if(errors.length)throw new Error(errors.join('\n'));
  for(let i=1;i<taps.length;i++)if(taps[i].at-taps[i-1].at<1.9)throw new Error('Tutorial actions are too close together');
  fs.writeFileSync(path.join(raw,guide.id+'-timeline.json'),JSON.stringify({duration,captions,taps},null,2));
  await sleep(500);const video=page.video();await context.close();const input=await video.path(),output=path.join(out,'guide-'+guide.id+'.mp4');
  execFileSync(ffmpeg,['-y','-hide_banner','-loglevel','error','-ss',String(offset),'-i',input,'-i',audio,'-t',String(duration),'-map','0:v:0','-map','1:a:0','-c:a','aac','-b:a','96k','-c:v','libx264','-preset','fast','-crf','25','-pix_fmt','yuv420p','-r','24','-movflags','+faststart',output],{stdio:'pipe'});
  execFileSync(ffmpeg,['-y','-hide_banner','-loglevel','error','-ss','12','-i',output,'-vf','scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2:color=0x111410','-frames:v','1','-q:v','3',path.join(out,'guide-'+guide.id+'.jpg')],{stdio:'pipe'});
  fs.writeFileSync(path.join(out,'guide-'+guide.id+'.vtt'),'WEBVTT\n\n'+captions.map(cue=>stamp(cue.start)+' --> '+stamp(cue.end)+'\n'+cue.text+'\n').join('\n'));
  console.log(guide.id+': recorded '+duration+'s narrated MP4, poster, Ukrainian VTT; '+Math.round(fs.statSync(output).size/1024)+' KiB');
 }}finally{await browser.close();}
})().catch(error=>{console.error(error);process.exitCode=1;});
