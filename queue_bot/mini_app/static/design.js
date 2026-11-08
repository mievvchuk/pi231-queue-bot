"use strict";
/* Native-DOM adaptation of the user's Figma Make source. Domain actions stay in app.js/features.js. */
window.createPI231Design = function (H) {
  const {S, el, btn, small, api, sheet, closeSheet, navigate, openFeature, openQueue, labForm,
    avatarFace, consultation, labText, isAdmin, render, toast} = H;
  const glyph = name => el('i', {class:'bi bi-'+name, 'aria-hidden':'true'});
  const button = (text, action, variant='primary', name) => btn([name ? glyph(name) : null,text],action,'btn btn-'+variant);
  const badge = (text,tone='neutral') => el('span',{class:'badge badge-'+tone},text);
  const card = (cls,...children) => el('section',{class:'card '+cls},...children);
  const heading = (eyebrow,title,action) => el('header',{class:'page-header'},el('div',{},el('p',{class:'eyebrow'},eyebrow),el('h1',{},title)),action);
  const ownQueues = () => S.data.queues.filter(q=>q.my_position != null);
  const initials = name => (name||'ПІ').split(' ').filter(Boolean).map(x=>x[0]).slice(0,2).join('');
  const subjectIcon = name => /машин/i.test(name)?'cpu':/інтернет/i.test(name)?'globe2':/курсов/i.test(name)?'mortarboard':/людин|людон/i.test(name)?'person-workspace':'code-slash';
  const avatar = (name,cls='avatar-button') => btn(avatarFace('me',initials(name)),()=>navigate('profile'),cls);
  const studentNav = [['journal','Мої оцінки','journal-bookmark'],['queues','Черги','people'],['schedule','Розклад','calendar3'],['profile','Профіль','person']];
  const adminNav = [['admin','Огляд','grid','Робота'],['admin-history','Черги','people','Робота'],['admin-group','Студенти','person-lines-fill','Робота'],['admin-subjects','Предмети й викладачі','journal-bookmark','Навчання'],['admin-replacements','Розклад і заміни','calendar2-week','Навчання'],['admin-saturdays','Робочі суботи','calendar-check','Навчання'],['admin-announcements','Оголошення','megaphone','Комунікації'],['admin-bugs','Звернення','chat-left-text','Система'],['owner','Налаштування','gear','Система']];
  adminNav.splice(3,0,['journal','Журнал групи','journal-bookmark','Навчання']);
  const go = key => {
    closeMenu();
    if(S.busy)return;
    if(key==='admin-announcements')return openFeature('owner');
    if(key==='appearance') return appearance();
    if(key==='swaps') return navigate('swaps');
    if(key==='home')return isAdmin()?navigate('admin'):openFeature('journal');
    if(key==='journal' || key.startsWith('admin-') || key==='owner') return openFeature(key);
    navigate(key);
  };
  function switchMode() {
    if(S.busy||S.loading||!S.data?.me?.is_admin)return;
    S.mode=isAdmin()?'student':'admin';
    go(isAdmin()?'admin':'home');
  }
  let shell=null, shellMode=null;
  function closeMenu(){ shell?.querySelector('.sidebar')?.classList.remove('mobile-open'); document.getElementById('menu-scrim')?.remove(); }
  function openMenu(){
    shell?.querySelector('.sidebar')?.classList.add('mobile-open');
    if(!document.getElementById('menu-scrim'))document.body.append(el('button',{id:'menu-scrim',class:'scrim','aria-label':'Закрити меню',onclick:closeMenu}));
  }
  function navigation(){
    const admin=isAdmin(), active=S.queue?'queues':S.feature||S.tab;
    const identity=[S.mode,S.data.me.is_admin,S.data.me.is_owner,S.data.me.name].join('|');
    if(!shell || shellMode!==identity){
      closeMenu(); shellMode=identity;
      const links=admin?adminNav.filter(n=>!['owner','admin-announcements','admin-bugs'].includes(n[0])||S.data.me.is_owner):studentNav;
      const nav=el('nav',{'aria-label':'Розділи застосунку'});
      let group=null;
      links.forEach(([key,label,name,g])=>{
        if(g && group!==g){nav.append(el('p',{class:'nav-group'},g));group=g;}
        nav.append(el('button',{type:'button','data-page':key,onclick:()=>go(key)},glyph(name),label));
      });
      const aside=el('aside',{class:'sidebar'},
        el('div',{class:'brand'},el('span',{class:'brand-mark'},avatarFace('bot','ПІ')),el('div',{},el('strong',{},'ПІ-231'),el('small',{},'Черги та розклад')),btn(glyph('x-lg'),closeMenu,'close-menu')),
        S.data.me.is_admin?btn([el('span',{},glyph(admin?'person':'shield-check')),el('div',{},el('small',{},'Режим роботи'),el('strong',{},admin?'Адміністратор':'Студент')),glyph('arrow-left-right')],switchMode,'mode-switch'):null,
        nav,
        el('div',{class:'sidebar-profile'},el('span',{class:'mini-avatar'},avatarFace('me',initials(S.data.me.name))),el('div',{},el('strong',{},S.data.me.name),el('small',{},admin?'Адміністратор':'Студент')),el('button',{type:'button','aria-label':'Оформлення',onclick:appearance},glyph('palette'))));
      const mobile=el('nav',{class:'bottom-nav','aria-label':'Основні розділи'});
      const tabs=admin?[adminNav[0],adminNav[1],adminNav.find(n=>n[0]==='journal'),adminNav.find(n=>n[0]==='admin-group')]:studentNav;
      tabs.forEach(([key,label,name])=>mobile.append(el('button',{type:'button','data-page':key,onclick:()=>go(key)},glyph(name),el('span',{},label))));
      if(admin)mobile.append(el('button',{type:'button','aria-label':'Усі розділи',onclick:openMenu},glyph('grid'),el('span',{},'Ще')));
      shell=el('div',{class:'make-navigation'},aside,mobile);
    }
    shell.querySelectorAll('[data-page]').forEach(n=>{
      const selected=n.dataset.page===active||(S.queue&&n.dataset.page===(admin?'admin-history':'queues'));
      n.classList.toggle('active',selected);
      selected?n.setAttribute('aria-current','page'):n.removeAttribute('aria-current');
    });
    return shell;
  }
  function topbar(){
    const title=(isAdmin()?adminNav:studentNav).find(n=>n[0]===(S.feature||S.tab))?.[1]||'ПІ-231';
    return el('div',{class:'mobile-top'},btn([el('span',{class:'brand-mark'},avatarFace('bot','ПІ')),el('strong',{},title)],()=>go(isAdmin()?'admin':'home'),'brand-mini'),
      el('div',{},S.data.me.is_admin?el('button',{type:'button',class:'button secondary mobile-mode-switch','aria-label':isAdmin()?'Режим студента':'Режим адміністратора',onclick:switchMode},isAdmin()?'Адмін ↔':'Студент ↔'):null,el('button',{type:'button',class:'icon-button','aria-label':'Оформлення',onclick:appearance},glyph('palette')),avatar(S.data?.me.name)));
  }
  function homePage(){
    const lesson=S.data.next_lesson, own=ownQueues(), primary=own[0];
    const names=[...new Set(lesson?.lessons.map(l=>l.subject)||[])];
    const rooms=[...new Set(lesson?.lessons.flatMap(l=>l.teachers_raw?.map(t=>t.room).filter(Boolean)||[l.room].filter(Boolean))||[])];
    const pair=lesson?.pair || lesson?.number || ({'08:30':1,'10:00':2,'11:50':3,'13:20':4,'14:50':5})[lesson?.startTime];
    const date=new Date(S.data.today+'T12:00:00Z').toLocaleDateString('uk-UA',{weekday:'long',day:'numeric',month:'long',timeZone:'Europe/Kyiv'});
    const quick=(name,title,description,action)=>btn([el('span',{},glyph(name)),el('strong',{},title),el('small',{},description)],action,'quick-action');
    return el('main',{class:'make-home page'},heading(date,'Привіт, '+S.data.me.name.split(' ').slice(0,2).join(' '),avatar(S.data.me.name)),
      card('hero-card',el('div',{class:'hero-copy'},badge([el('span',{class:'live-dot'}),lesson?(lesson.ongoing?'Зараз':'Найближча пара'):'Твій навчальний день'],'accent'),el('h2',{},lesson?names.join(' / '):'Переглянь свій розклад'),el('p',{},glyph('geo-alt'),rooms.length?' ауд. '+rooms.join(' / '):' ПІ-231 · розклад і заміни'),el('div',{class:'hero-time'},el('strong',{},lesson?.startTime||'—'),el('span',{},lesson?[lesson.date!==S.data.today?new Date(lesson.date+'T12:00:00Z').toLocaleDateString('uk-UA',{day:'numeric',month:'long'}):'Сьогодні',lesson.startTime+'–'+lesson.endTime].join(' · '):'Актуальні заняття у вкладці «Розклад»'))),el('div',{class:'hero-orbit'},el('span',{},pair||'—'),el('small',{},'пара'))),
      el('div',{class:'section-heading'},el('div',{},el('p',{class:'eyebrow'},'Твій запис'),el('h2',{},'Наступна черга')),primary?button(['Деталі ',glyph('arrow-right')],()=>openQueue(primary.id),'ghost'):null),
      primary?btn([el('div',{class:'position-number'},el('small',{},'місце'),el('strong',{},String(primary.my_position).padStart(2,'0'))),el('div',{class:'position-info'},el('div',{},primary.status==='archived'?badge('Архівна черга','neutral'):null,el('h3',{},primary.subject),el('p',{},consultation(primary)?'Консультація':labText({labs:primary.my_labs,planned_minutes:primary.my_planned_minutes})||primary.teacher)),glyph('chevron-right'))],()=>openQueue(primary.id),'card position-card'):card('empty-state',glyph('people'),el('h3',{},'Ти ще не в черзі'),small('Обери предмет і зручне місце.')),
      el('div',{class:'quick-grid'},quick('person-plus','Стати в чергу',S.data.queues.filter(q=>q.status==='open').length+' відкритих',()=>go('queues')),quick('calendar3','Розклад','Пари й заміни',()=>go('schedule')),quick('arrow-left-right','Обміни',S.data.swaps.length+' запитів',()=>go('swaps'))),
      own.length>1?el('section',{class:'other-records'},el('div',{class:'section-heading'},el('h2',{},'Інші записи'),small(own.length-1)),el('div',{class:'queue-list'},own.slice(1).map(queueRow))):null);
  }
  function queueRow(q){return btn([el('span',{class:'subject-icon'},glyph(subjectIcon(q.subject))),el('div',{class:'queue-main'},el('div',{class:'queue-title'},el('h3',{},q.subject),badge(q.my_position!=null?'Ти №'+q.my_position:q.status==='open'?'Відкрито':'Архівна черга',q.my_position!=null?'accent':q.status==='open'?'success':'neutral')),el('p',{},q.teacher),el('div',{class:'meta-line'},el('span',{},glyph('people'),q.count+' учасників'),el('span',{},glyph('book'),consultation(q)?'Консультація':'Лабораторні'))),glyph('chevron-right')],()=>openQueue(q.id),'card queue-row');}
  function queuesPage(){
    const list=el('div',{class:'queue-list'}), count=small(''), search=el('input',{type:'search',placeholder:'Знайти предмет або викладача',value:S.search||'','aria-label':'Знайти предмет або викладача'});
    const update=()=>{S.search=search.value;const qs=H.filteredQueues();count.replaceChildren('Знайдено: '+qs.length);list.replaceChildren(...(qs.length?qs.map(queueRow):[card('empty-state',glyph('search'),el('h3',{},'Нічого не знайдено'),small('Спробуй змінити запит або фільтр.'))]));};
    search.addEventListener('input',update);update();
    return el('main',{class:'page make-queues'},heading('Навчання','Черги',button('Стати в чергу',pickQueue,'primary','plus-lg')),
      el('div',{class:'toolbar'},el('div',{class:'segment queue-filters'},[['all','Усі',S.data.queues.length],['mine','Мої',ownQueues().length]].map(([key,label,count])=>el('button',{type:'button','aria-pressed':String(S.queueFilter===key),class:S.queueFilter===key?'active':'',onclick:()=>{S.queueFilter=key;render();}},label,el('span',{},count)))),el('label',{class:'search'},glyph('search'),search)),
      el('div',{class:'segment queue-type-filters',role:'group','aria-label':'Тип черги'},H.queueTypes.map(([key,label])=>el('button',{type:'button','aria-pressed':String(S.queueTypeFilter===key),class:S.queueTypeFilter===key?'active':'',onclick:()=>{S.queueTypeFilter=key;render();}},label))),count,list);
  }
  function pickQueue(){
    const panel=sheet('Стати в чергу','Обери відкриту чергу.');
    const qs=S.data.queues.filter(q=>q.status==='open'&&q.my_position==null);
    panel.append(el('div',{class:'queue-list'},qs.map(q=>button(q.subject+' · '+q.teacher,async()=>{
      try{const current=await api('/queues/'+q.id);joinWizard(current);}catch(error){toast(error.message,true);}
    },'secondary','people'))));
    if(!qs.length)panel.append(el('p',{class:'notice'},'Немає доступних черг для нового запису.'));
  }
  function joinWizard(initial){
    let q=initial, step=q.labs?.length?1:3, labs=[], minutes=5, position=null, append=false, message='';
    if(q.entries?.some(row=>row.is_me)){toast('Ти вже в цій черзі.');return openQueue(q.id);}
    const panel=sheet('Стати в чергу',q.subject);panel.classList.add('join-modal');
    const progress=el('div',{class:'wizard-progress'}),body=el('div',{class:'wizard-panel'}),actions=el('div',{class:'modal-actions'});
    panel.append(progress,body,actions);
    const max=()=>q.labs.filter(l=>labs.includes(l.number)).reduce((sum,l)=>sum+l.max_minutes,0);
    const row=(label,value,cls='')=>el('div',{class:cls},el('span',{},label),el('strong',{},value));
    const refresh=async()=>{q=await api('/queues/'+q.id);if(position!=null&&q.entries?.some(s=>s.position===position&&!s.vacant))position=null;};
    const submit=async()=>{
      if(S.busy)return;
      S.busy=true; draw();
      try{
        const result=await api('/queues/'+q.id+'/join',{...(q.labs.length?{labs,minutes}:{}),...(!append?{position}:{})});
        closeSheet();await H.load();joinSuccess(q,{...result,labs,minutes});
      }catch(error){
        message=error.message;
        if(error.status===401||error.status===403){closeSheet();await H.load();}
        else {try{await refresh();}catch(_){} step=3;}
      }finally{S.busy=false;if(panel.isConnected)draw();}
    };
    function draw(){
      const steps=q.labs.length?[1,2,3,4]:[3,4];
      const alreadyJoined=q.entries?.some(row=>row.is_me);
      progress.style.gridTemplateColumns='repeat('+steps.length+',1fr)';
      progress.replaceChildren(...steps.map(n=>el('div',{class:n===step?'active':n<step?'done':''},el('span',{},n<step?glyph('check-lg'):steps.indexOf(n)+1),el('small',{},['Лабораторні','Час','Місце','Перевірка'][n-1]))));
      body.replaceChildren();
      if(message)body.append(el('p',{class:'notice',role:'alert'},message));
      if(alreadyJoined)body.append(el('p',{class:'notice'},'Ти вже в цій черзі.'),button('Переглянути свій запис',()=>{closeSheet();openQueue(q.id);},'secondary'));
      if(step===1){
        body.append(el('h3',{},'Які лабораторні здаватимеш?'),el('p',{},'Можна обрати кілька. Їхні ліміти часу сумуються.'),el('div',{class:'lab-grid'},q.labs.map(l=>el('button',{type:'button',class:labs.includes(l.number)?'selected':'','aria-pressed':String(labs.includes(l.number)),onclick:()=>{labs=labs.includes(l.number)?labs.filter(n=>n!==l.number):[...labs,l.number].sort((a,b)=>a-b);minutes=Math.min(minutes,Math.max(5,max()));draw();}},el('span',{},'Лабораторна №'+l.number),el('small',{},'до '+l.max_minutes+' хв'),labs.includes(l.number)?glyph('check-circle-fill'):null))));
      } else if(step===2){
        body.append(el('h3',{},'Скільки часу плануєш?'),el('p',{},'Для вибраних лабораторних доступно від 5 до '+max()+' хвилин.'),el('div',{class:'time-picker'},el('button',{type:'button','aria-label':'Зменшити час',disabled:minutes<=5,onclick:()=>{minutes=Math.max(5,minutes-5);draw();}},glyph('dash-lg')),el('div',{},el('strong',{},minutes),el('span',{},'хвилин')),el('button',{type:'button','aria-label':'Збільшити час',disabled:minutes>=max(),onclick:()=>{minutes=Math.min(max(),minutes+5);draw();}},glyph('plus-lg'))),el('div',{class:'time-presets'},[5,10,15,20,25,30,40,60,80].filter(n=>n<=max()).map(n=>btn(n+' хв',()=>{minutes=n;draw();},minutes===n?'active':''))),el('div',{class:'demo-note'},glyph('lightbulb'),el('span',{},'Обирай реалістичний час — це допомагає планувати здачу.')));
      } else if(step===3){
        const slots=q.join_slots||[];
        const custom=el('input',{type:'number',min:1,max:100,step:1,inputmode:'numeric',placeholder:'Наприклад 14','aria-label':'Вільний номер від 1 до 100'});
        if(!append&&position)custom.value=position;
        custom.addEventListener('change',()=>{const n=Number(custom.value);if(!Number.isInteger(n)||n<1||n>100){message='Вкажи номер від 1 до 100.';draw();return;}if(q.entries?.some(s=>s.position===n&&!s.vacant)){message='Це місце вже зайняте.';draw();return;}position=n;append=false;message='';draw();});
        body.append(el('label',{class:'field'},'Вільний номер від 1 до 100',custom),small('На схемі — перші 20 місць. Будь-який інший номер можна ввести вище.'));
        body.append(el('div',{class:'section-heading'},el('div',{},el('h3',{},'Обери вільне місце'),el('p',{},q.status==='open'?'Місця перевіряються сервером перед записом.':'Запис у цю чергу закрито.')),el('button',{type:'button',class:'icon-button','aria-label':'Оновити місця',onclick:async()=>{try{await refresh();message='';draw();}catch(error){message=error.message;draw();}}},glyph('arrow-clockwise'))),
          el('div',{class:'slot-legend'},[['free','Вільне'],['busy','Зайняте'],['chosen','Обране'],['yours','Власне']].map(([cls,label])=>el('span',{},el('i',{class:cls},cls==='yours'?'Ти':null),label))),
          el('div',{class:'slots'},slots.map(s=>el('button',{type:'button',class:s.state+(position===s.number&&!append?' selected':''),disabled:s.state!=='free'||q.status!=='open','aria-label':'Місце '+s.number+', '+(s.state==='free'?'вільне':s.state==='yours'?'твоє':'зайняте'),'aria-pressed':String(position===s.number&&!append),onclick:()=>{position=s.number;append=false;draw();}},el('strong',{},s.number),el('small',{},s.state==='busy'?'Зайнято':s.state==='yours'?'Ти':position===s.number&&!append?'✓ Обрано':'Вільно')))),
          button('Додатися в кінець · місце №'+q.append_position,()=>{append=true;position=null;draw();},append?'primary':'secondary','arrow-right-circle'));
        if(!slots.some(s=>s.state==='free'))body.append(el('p',{class:'notice'},'У наявних комірках немає вільних місць. Можна додатися в кінець.'));
      } else {
        body.append(el('h3',{},'Перевір деталі запису'),el('p',{},'Перед підтвердженням сервер ще раз перевірить доступність місця.'),card('summary-card',row('Предмет',q.subject),...(q.labs.length?[row('Лабораторні',labs.map(n=>'№'+n).join(', ')),row('Планований час',minutes+' хв')]:[row('Тип','Консультація')]),row('Позиція','Місце №'+(append?q.append_position:position),'summary-place')));
      }
      const next=button(step===4?(S.busy?'Записую…':'Зайняти місце №'+(append?q.append_position:position)):'Продовжити',()=>{if(step===4)submit();else{step++;draw();}},'primary',step===4?'check2-circle':'arrow-right');
      next.disabled=S.busy||alreadyJoined||q.status!=='open'||(step===1&&!labs.length)||(step===3&&!append&&position==null);
      actions.replaceChildren(button(step===steps[0]?'Скасувати':'Назад',()=>{if(step===steps[0])closeSheet();else{step--;draw();}},'secondary'),next);
      if(S.busy)panel.querySelectorAll('button').forEach(n=>n.disabled=true);
    }
    draw();
  }
  function joinSuccess(q,result){
    const panel=sheet('Ти в черзі!','');panel.classList.add('success-modal');
    panel.querySelector('.section-heading')?.remove();panel.querySelector(':scope > .muted')?.remove();
    panel.append(el('div',{class:'success-check'},glyph('check-lg')),el('h2',{},'Ти в черзі!'),el('p',{class:'success-place'},'Місце №'+result.position),card('summary-card',el('div',{},el('span',{},'Предмет'),el('strong',{},q.subject)),q.labs.length?el('div',{},el('span',{},'Лабораторні'),el('strong',{},result.labs.map(n=>'№'+n).join(', '))):null,q.labs.length?el('div',{},el('span',{},'Планований час'),el('strong',{},result.minutes+' хв')):null),el('div',{class:'modal-actions'},button('Готово',closeSheet,'secondary'),button('Переглянути чергу',()=>{closeSheet();openQueue(q.id);},'primary','arrow-right')));
  }
  function appearance(){
    if(S.busy)return;
    const prefs=H.appearanceSettings();
    const panel=sheet('Оформлення','Персоналізація');panel.classList.add('appearance-modal');
    const body=el('div',{class:'modal-body'}), themeOptions=el('div',{class:'theme-options'}), colors=el('div',{class:'accent-options'});
    const refresh=()=>{
      const p=H.appearanceSettings();
      themeOptions.querySelectorAll('button').forEach(n=>{n.classList.toggle('selected',n.dataset.theme===p.theme);n.setAttribute('aria-pressed',String(n.dataset.theme===p.theme));});
      colors.querySelectorAll('button').forEach(n=>{n.classList.toggle('selected',n.dataset.accent===p.accent);n.setAttribute('aria-pressed',String(n.dataset.accent===p.accent));});
    };
    [['light','Світла'],['dark','Темна'],['system','Системна']].forEach(([key,label])=>themeOptions.append(el('button',{type:'button','data-theme':key,'aria-label':label+' тема',onclick:()=>{H.setAppearance({theme:key});refresh();}},el('span',{class:'theme-preview '+key},el('i'),el('i'),el('i')),el('strong',{},label),glyph('check-circle-fill'))));
    [['lime','Лаймовий'],['blue','Синій'],['violet','Фіолетовий'],['teal','Бірюзовий'],['pink','Рожевий'],['orange','Помаранчевий']].forEach(([key,label])=>colors.append(el('button',{type:'button','data-accent':key,'aria-label':label+' акцент',onclick:()=>{H.setAppearance({accent:key});refresh();}},el('i',{class:'swatch swatch-'+key},glyph('check-lg')),el('span',{},label))));
    body.append(el('section',{class:'appearance-section'},el('h3',{},'Тема'),themeOptions),el('section',{class:'appearance-section'},el('h3',{},'Акцентний колір'),colors),el('div',{class:'live-preview'},el('div',{},badge('Попередній перегляд','accent'),el('h3',{},S.data?.queues[0]?.subject||'ПІ-231'),el('p',{},'Твоє місце в черзі — №4')),button('Дія',()=>{},'primary')),
      el('label',{class:'switch-row'},el('span',{},el('strong',{},'Зменшений рух'),el('small',{},'Мінімум анімацій і переходів')),el('input',{type:'checkbox',checked:prefs.reduced,onchange:event=>H.setAppearance({reduced:event.target.checked})})));
    panel.append(body,el('div',{class:'modal-actions'},el('span',{class:'saved-hint'},glyph('cloud-check'),' Зберігається автоматично'),button('Готово',closeSheet)));refresh();
  }
  function profilePage(){
    const own=ownQueues(),name=S.data.me.name;
    const settings=(name,title,description,action)=>btn([el('span',{class:'subject-icon'},glyph(name)),el('div',{},el('strong',{},title),el('small',{},description)),glyph('chevron-right')],action,'');
    return el('main',{class:'page make-profile'},heading('Telegram-профіль','Мій профіль'),card('profile-card',el('div',{class:'profile-avatar'},avatarFace('me',initials(name))),el('div',{},el('h2',{},name),el('p',{},'ПІ-231 · '+(isAdmin()?'Адміністратор':'Студент')),badge([glyph('shield-check'),' Telegram підтверджено'],'success'))),
      el('div',{class:'profile-grid'},card('',el('p',{class:'eyebrow'},'Мої записи'),el('div',{class:'stat-row'},el('div',{},el('strong',{},own.length),el('span',{},'Активних')),el('div',{},el('strong',{},S.data.swaps.length),el('span',{},'Запитів на обмін')))),
      card('settings-list',S.data.me.is_admin?settings('shield-check',isAdmin()?'Режим студента':'Режим адміністратора','Перемкнути режим роботи',switchMode):null,settings('question-circle','Допомога','Як користуватися застосунком',()=>go('help')),settings('palette','Оформлення','Тема, колір та анімації',appearance),settings('arrow-left-right','Обміни','Запити та повторний обмін',()=>go('swaps')),settings('phone','Встановити PWA','Додати на головний екран',async()=>toast(await window.PI231PWA?.install()||'Скористайся меню браузера → Встановити застосунок.')),settings('chat-left-text','Звернення та відповіді','Повідомити про проблему',()=>openFeature('bugs')),settings('journal-bookmark','Мої пропозиції','Предмети та викладачі',()=>openFeature('proposals')))),
      own.length?el('div',{class:'queue-list'},own.map(queueRow)):null,
      !H.initData?button('Вийти з профілю',H.logout,'ghost','box-arrow-right'):null);
  }
  function adminPage(){
    const qs=S.data.queues,stats=[[qs.length,'постійних черг','people'],[S.users.length,'студентів групи','person-lines-fill'],[qs.reduce((s,q)=>s+q.count,0),'записів у чергах','journal-check']];
    const max=Math.max(1,...qs.map(q=>q.count));
    return el('main',{class:'page make-admin'},heading('Адміністрування · ПІ-231','Панель керування',button('Створити чергу',()=>openFeature('create'),'primary','plus-lg')),
      el('div',{class:'stats-grid'},stats.map(([n,label,i])=>card('stat-card',el('span',{class:'subject-icon'},glyph(i)),el('div',{},el('strong',{},n),el('p',{},label))))),
      el('div',{class:'dashboard-grid'},card('activity-card',el('div',{class:'section-heading'},el('h2',{},'Учасники активних черг'),badge('Зараз','success')),el('div',{class:'chart-bars'},qs.length?qs.slice(0,12).map(q=>el('div',{'aria-label':q.subject+': '+q.count},el('span',{style:'height:'+Math.max(5,q.count/max*100)+'%'},el('span',{class:'screen-reader-only'},q.count)),el('small',{},q.subject))):small('Активних черг немає.')),el('div',{class:'chart-labels'},el('span',{},'Поточні дані'),el('span',{},'Кількість учасників'))),
      card('',el('div',{class:'section-heading'},el('h2',{},'Швидкі дії')),el('div',{class:'attention-list'},[['admin-history','Активні черги','Учасники та результати здачі'],['admin-group','Студенти групи','Обмеження та тимчасові бани'],['admin-replacements','Розклад і заміни','Автоматичні заміни й підтвердження']].map(([key,title,text])=>btn([el('span',{class:'status-dot success'}),el('div',{},el('strong',{},title),el('small',{},text)),glyph('chevron-right')],()=>openFeature(key),''))))),
      card('admin-table',adminNav.slice(1).filter(n=>!['owner','admin-announcements','admin-bugs'].includes(n[0])||S.data.me.is_owner).map(([key,label,name,group])=>btn([el('span',{class:'subject-icon'},glyph(name)),el('div',{},el('strong',{},label),el('small',{},group)),badge('Відкрити','neutral'),glyph('chevron-right')],()=>go(key),'admin-row'))),
      el('p',{class:'modebar'},'Режим адміністратора · Telegram ID доступні лише тут'));
  }
  function queuePage(q,own,entries,controls,dock,otherActions){
    const chips=own?.labs?.map(n=>badge('Лаб. №'+n,'accent'))||[];
    const ownStatus=q.lesson ? (q.lesson.states[String(own?.id)] || 'Місце збережене') : 'Позиція в загальній черзі';
    return el('main',{class:'page queue-detail make-detail'},heading('Черга · '+(consultation(q)?'консультація':'лабораторні'),q.subject,button('Назад',()=>{S.queue=null;navigate('queues');},'secondary','arrow-left')),
      el('div',{class:'detail-grid'},el('div',{class:'detail-main'},card('teacher-card',el('span',{class:'subject-icon large'},glyph(subjectIcon(q.subject))),el('div',{},el('h3',{},q.teacher),el('p',{},consultation(q)?'Консультація':q.title||'Лабораторні роботи')),q.status==='archived'?badge('Архівна черга','neutral'):null),
        own?card('your-place',el('div',{},el('p',{class:'eyebrow'},q.lesson?'Твоє збережене місце':'Твоє місце'),el('div',{class:'big-place'},'№'+own.position),el('p',{},ownStatus),q.lesson?.people_ahead!=null ? small('Людей попереду: '+q.lesson.people_ahead) : null),el('div',{class:'lab-chips'},chips,el('span',{},consultation(q)?'Консультація':own.planned_minutes?own.planned_minutes+' хв':'')),el('div',{class:'action-row'},dock)):null,
        el('div',{class:'section-heading'},el('div',{},el('p',{class:'eyebrow'},'Наживо'),el('h2',{},'Учасники')),small(q.count+' студентів')),q.lesson?small('Номери — збережені місця. Порядок виклику — зверху вниз серед присутніх.'):null,el('section',{class:'card entry-list'},entries.length?entries:el('div',{class:'empty-state'},glyph('people'),el('h3',{},'Ти можеш бути першим')))),
      el('aside',{class:'detail-side'},own?card('',el('p',{class:'eyebrow'},'Твій запис'),el('h3',{},'Лабораторні та позиція'),el('p',{class:'muted'},'Зміни роботи або залиш побажання щодо місця.'),el('div',{class:'stack-buttons'},otherActions)):card('',el('p',{class:'eyebrow'},'Ще не в черзі?'),el('h3',{},'Обери зручне місце'),el('p',{class:'muted'},'Вільні позиції перевіряються сервером перед записом.'),button('Переглянути місця',()=>labForm(q),'primary','grid-3x3-gap')),controls)));
  }
  function loginPage(){
    const session=H.browserSession;
    const content=card('login-card',el('div',{class:'telegram-icon'},glyph('telegram')),el('h2',{},'ПІ-231 · Черги'),el('p',{},'Увійди через Telegram, щоб підтвердити профіль і отримувати сповіщення про свою позицію.'));
    if(!session.ready)content.append(el('p',{role:'status'},'Перевіряємо профіль…'));
    else if(session.enabled){
      const query=new URLSearchParams(location.search);
      if(query.get('login')==='failed')content.append(el('p',{role:'alert',class:'notice'},H.loginFailureMessage(query.get('reason'))));
      content.append(el('a',{href:'/auth/telegram/start',class:'btn btn-primary full'},glyph('telegram'),'Увійти через Telegram'));
    }else content.append(el('p',{class:'notice'},'Вхід у браузері поки не налаштовано. Відкрий мініапку в Telegram.'));
    content.append(button('Встановити застосунок',async()=>toast(await window.PI231PWA?.install()||'Відкрий меню браузера → Встановити застосунок.'),'secondary','download'),el('div',{class:'security-note'},glyph('shield-check'),el('span',{},'Твій профіль підтверджує Telegram. Telegram ID видно лише адміністраторам.')));
    return el('main',{class:'login-page make-login'},el('div',{class:'login-visual'},el('div',{class:'brand-mark large'},'ПІ'),el('div',{},el('p',{class:'eyebrow'},'Telegram Mini App · PWA'),el('h1',{},'Черга без зайвого очікування.'),el('p',{},'Обирай лабораторні, зручне місце та стеж за своєю позицією в реальному часі.')),el('div',{class:'floating-slots'},el('span',{},'2'),el('span',{class:'active'},'4 ',glyph('check-lg')),el('span',{},'7'))),content);
  }
  function decorate(page){
    page.classList.add('content');
    if(!page.classList.contains('page'))page.classList.add('legacy-page');
    if(S.feature)page.dataset.feature=S.feature;
    const title=page.querySelector('.page-title'),date=page.querySelector('.date-control');
    if(title && date){const copy=el('div',{},...title.childNodes);title.className='page-header';title.append(copy,date);}
    return page;
  }
  function helpPage(){
    const answers=[['Як змінити місце в черзі?','Відкрий деталі черги й натисни на іншого учасника, щоб запропонувати обмін. На відповідь є 10 хвилин. Побажання вище або нижче також доступні в деталях.'],['Що робити, якщо місце зайняли?','Сітка оновиться й запропонує іншу вільну комірку. Обрані лабораторні та час залишаться збереженими у відкритому вікні.'],['Як встановити застосунок?','Відкрий вебверсію у Safari або Chrome та скористайся пунктом «Додати на головний екран» чи «Встановити застосунок».'],['Чому не надходять сповіщення?','Сповіщення надходять у чат із ботом у Telegram. Перевір, що ти запускав бота, не заблокував його та дозволив сповіщення в Telegram.']];
    return el('main',{class:'page make-help'},heading('Підтримка','Допомога й звернення',button('Нове звернення',()=>openFeature('bugs'),'primary','plus-lg')),
      el('div',{class:'detail-grid'},el('div',{class:'detail-main'},card('guide-card',el('span',{class:'icon-bubble'},glyph('play-circle')),el('div',{},el('h2',{},'Як працюють черги?'),el('p',{},'Коротка інструкція: від вибору лабораторних до завершення здачі.')),button('Переглянути',()=>{const panel=sheet('Як працюють черги?','');panel.append(el('p',{class:'notice'},'Відкрий «Черги», обери предмет і натисни «Переглянути місця». Познач лабораторні, час та вільну комірку. Перевір деталі й підтвердь запис. Після здачі натисни «Здав». Якщо робіт кілька — познач фактично здані; поки продовжуєш підхід, місце зберігається. Після «Завершити підхід» решта робіт переходить у кінець черги.'));},'secondary')),
        videoGuides(),el('div',{class:'section-heading'},el('h2',{},'Поширені питання')),card('faq',answers.map(([question,answer],i)=>{const content=el('p',{hidden:i!==0},answer);const control=el('button',{type:'button','aria-expanded':String(i===0),onclick:()=>{content.hidden=!content.hidden;control.setAttribute('aria-expanded',String(!content.hidden));}},el('strong',{},question),glyph('chevron-down'));return el('div',{class:'faq-item'},control,content);}))),
      el('aside',{class:'detail-side'},card('',el('h3',{},'Твої звернення'),el('p',{class:'muted'},'Історія повідомлень та відповіді адміністратора.'),button('Переглянути',()=>openFeature('bugs'),'secondary','chat-left-text')),card('',el('h3',{},'Зв’язатися з нами'),el('p',{class:'muted'},'Опиши проблему й додай скриншот, якщо він допоможе.'),button('Нове звернення',()=>openFeature('bugs'),'primary','plus-lg')))));
  }
  function videoGuides(){
    return el('section',{class:'video-guides'},el('div',{class:'section-heading'},el('h2',{},'Відеоінструкції'),small('По 40 секунд · українська озвучка й підписи')),
      el('div',{class:'video-guide-grid'},(window.PI231VideoGuides||[]).map(guide=>btn([
        el('span',{class:'video-guide-cover'},el('img',{src:guide.poster,alt:'',loading:'lazy',decoding:'async'}),el('span',{class:'video-guide-play','aria-hidden':'true'},glyph('play-fill')),el('span',{class:'video-guide-duration'},guide.duration+' с')),
        el('span',{class:'video-guide-copy'},el('strong',{},glyph(guide.icon),' '+guide.title),el('small',{},guide.description)),
      ],()=>openVideoGuide(guide),'card video-guide-card'))));
  }
  function openVideoGuide(guide){
    const panel=sheet(guide.title,'Навчальний приклад на тестових даних. Увімкни звук, щоб почути пояснення українською. За потреби обери повільніший темп під відео.');panel.classList.add('video-guide-dialog');
    const error=el('p',{class:'notice',hidden:true,role:'alert'},'Не вдалося завантажити відео. Спробуй відкрити його окремо або прочитай кроки нижче.');
    const video=el('video',{class:'guide-video',controls:true,playsInline:true,preload:'metadata',poster:guide.poster,onerror:()=>{error.hidden=false;}},el('source',{src:guide.src,type:'video/mp4'}),el('track',{src:guide.captions,kind:'captions',srclang:'uk',label:'Українські підписи'}));
    const speed=el('select',{'aria-label':'Швидкість відео',onchange:event=>{const rate=Number(event.target.value);if([0.75,1,1.25].includes(rate))video.playbackRate=rate;}},el('option',{value:'0.75'},'0,75× · повільніше'),el('option',{value:'1',selected:true},'1× · звичайно'),el('option',{value:'1.25'},'1,25× · швидше'));
    panel.append(video,error,el('label',{class:'guide-speed'},el('span',{},'Швидкість'),speed),el('details',{class:'guide-transcript'},el('summary',{},'Текстова інструкція'),el('ol',{},guide.steps.map(step=>el('li',{},step)))),el('a',{class:'button secondary',href:guide.src,target:'_blank',rel:'noopener'},'Відкрити відео окремо'));
  }
  function swapsPage(){
    const main=el('main',{class:'page make-swaps'},heading('Черги','Обміни місцями')),list=el('div',{class:'two-col'}),tabs=el('div',{class:'segment'});
    const choose=outgoing=>{
      tabs.querySelectorAll('button').forEach((n,i)=>n.classList.toggle('active',Boolean(i)===outgoing));
      const rows=outgoing?(S.data.outgoing_swaps||[]):S.data.swaps;
      list.replaceChildren(...(rows.length?rows.map(inv=>{
        const subject=S.data.queues.find(q=>q.id===inv.session_id)?.subject||'Черга';
        const timer=badge(inv.status==='accepted'?'Обмін погоджено':'10 хв','warning');
        if(inv.status==='pending')timer.dataset.expires=inv.expires_at;
        const actions=el('div',{class:'action-row'});
        if(!outgoing)actions.append(button(inv.status==='accepted'?'Повторити обмін':'Прийняти',()=>H.mutate('/swaps/'+inv.token,{accept:true}),'primary','check-lg'));
        if(!outgoing&&inv.status==='pending')actions.append(button('Відхилити',()=>H.mutate('/swaps/'+inv.token,{accept:false}),'secondary','x-lg'));
        if(outgoing)actions.append(el('p',{class:'muted'},inv.status==='accepted'?'Обмін погоджено.':'Очікуємо відповідь іншого студента.'));
        return card('swap-card',el('div',{class:'swap-top'},timer,el('span',{class:'muted'},subject)),el('div',{class:'swap-people'},el('div',{},el('span',{class:'mini-avatar'},initials(inv.sender_name)),el('small',{},outgoing?'Ти':inv.sender_name),el('strong',{},inv.sender_position?'№'+inv.sender_position:'—')),glyph('arrow-left-right'),el('div',{},el('span',{class:'mini-avatar accent'},initials(inv.target_name)),el('small',{},outgoing?inv.target_name:'Ти'),el('strong',{},inv.target_position?'№'+inv.target_position:'—'))),actions);
      }):[card('empty-state',glyph('arrow-left-right'),el('h3',{},'Запитів поки немає'),el('p',{},'Запропонуй обмін у деталях черги.'))]));tick();
    };
    tabs.append(btn(['Вхідні ',el('span',{},S.data.swaps.length)],()=>choose(false),'active'),btn(['Вихідні ',el('span',{},S.data.outgoing_swaps?.length||0)],()=>choose(true),''));
    main.append(el('div',{class:'toolbar'},tabs),list);choose(false);return main;
  }
  function tick(){
    document.querySelectorAll('[data-expires]').forEach(node=>{
      const seconds=Math.max(0,Math.ceil((Date.parse(node.dataset.expires)-Date.now())/1000));
      node.textContent=seconds?String(Math.floor(seconds/60)).padStart(2,'0')+':'+String(seconds%60).padStart(2,'0'):'Час вичерпано';
      if(!seconds)node.closest('.swap-card')?.querySelectorAll('button').forEach(button=>button.disabled=true);
    });
  }
  function lessonCard(slot){
    const lessons=slot.lessons||[];
    return el('div',{class:'lesson-row '+(!lessons.length?'lesson-empty':'')},el('div',{class:'lesson-time'},el('strong',{},slot.number),el('span',{},slot.startTime+'–'+slot.endTime)),card('lesson-card',el('div',{},lessons.length?lessons.map(l=>el('div',{class:'real-lesson'},el('h3',{},l.subject),el('p',{},l.teachers_raw?.length?l.teachers_raw.map(t=>t.name+(t.room?' · ауд. '+t.room:'')).join(' / '):[l.teacher,l.room&&'ауд. '+l.room].filter(Boolean).join(' · ')),l.is_replacement?badge('Заміна','warning'):null)):[el('h3',{},'Заняття немає'),el('p',{},'Вільний час')])));
  }
  return {navigation,topbar,homePage,queuesPage,profilePage,adminPage,queuePage,loginPage,lessonCard,appearance,decorate,glyph,heading,card,badge,button,closeMenu,joinWizard,helpPage,swapsPage,tick};
};

