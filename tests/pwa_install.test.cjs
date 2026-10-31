const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
function setup(tg, standalone=false) {
  const calls=[];
  const window={Telegram:{WebApp:tg},addEventListener(){}};
  const context=vm.createContext({window,location:{origin:'https://queue.example'},URL,
    navigator:{onLine:true,userAgent:'iPhone'},matchMedia:()=>({matches:standalone}),
    document:{getElementById:()=>null},});
  vm.runInContext(fs.readFileSync(path.join(__dirname,'../queue_bot/mini_app/static/pwa.js'),'utf8'),context);
  return {pwa:window.PI231PWA,calls};
}
test('install in Telegram opens website rather than creating a Telegram home-screen shortcut',async()=>{
  const opened=[];let shortcuts=0;
  const {pwa}=setup({initData:'signed',openLink:(...args)=>opened.push(args),addToHomeScreen:()=>shortcuts++});
  const message=await pwa.install();
  assert.equal(shortcuts,0);
  assert.equal(opened.length,1);
  assert.equal(opened[0][0],'https://queue.example/app/');
  assert.match(message,/Safari або Chrome/);
});
test('installed PWA never opens Telegram when user presses install',async()=>{
  let opened=0;
  const {pwa}=setup({initData:'',openLink:()=>opened++},true);
  assert.match(await pwa.install(),/уже встановлено/);
  assert.equal(opened,0);
});
test('Safari gets PWA installation instructions, not a Telegram shortcut',async()=>{
  const {pwa}=setup({initData:''});
  assert.match(await pwa.install(),/Safari.*На початковий екран/);
});
