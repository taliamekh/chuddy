'use strict';
// Full app, disposable HTTP fixtures; never a real desktop/session/browser launch.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('casino openings route once and Back stays usable across polls and reloads',async t=>{
  const state={settings:{rules:[],distraction_destination:'game'},profile:{id:'one',name:'Friend'},
    profiles:[{id:'one',name:'Friend'}],scores:{total:0,recent:[]},balance:100,
    messages:[],deadlines:[],memory:[],logs:[],hardware:{mode:'simulator'},agent:{status:'idle'},
    audio:{recording:false},voice:{speaking:false},session:'running',session_remaining:120,
    leaderboard:[],catalog:[],desktop_overlay:{status:'unavailable',hotkey:false},
    desktop_interception:{pending_restorations:0,covered:false},casino:{profile_id:'one',round:null,balance:100}};
  const actions=[];
  const send=(res,data)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/api/state')return send(res,state);
    if(req.url==='/api/devices')return send(res,[]);
    if(req.url==='/api/action'){
      let raw='';for await(const chunk of req)raw+=chunk;
      const p=JSON.parse(raw);actions.push(p);
      if(p.action==='session'&&p.operation==='stop'){state.session='stopped';state.interruption=null;state.casino_opening_id=null;state.casino_interruption_active=false;}
      if(p.action==='casino_leave'&&p.opening_id===state.casino_opening_id)state.desktop_interception.covered=false;
      if(p.action==='settings')Object.assign(state.settings,p.values);
      if(p.action==='dismiss'){state.interruption=null;state.desktop_interception={pending_restorations:0,covered:false};}
      return send(res,{});
    }
    const relative=req.url==='/'?'web/index.html':decodeURIComponent(req.url.slice(1)).split('?')[0];
    const file=path.resolve(root,relative);
    if(!relative.startsWith('web/')||!file.startsWith(root+path.sep)||!fs.existsSync(file)||!fs.statSync(file).isFile()){
      res.writeHead(404);return res.end();
    }
    res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'}[path.extname(file)]||'application/octet-stream'});
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  t.after(async()=>{try{if(browser)await browser.close();}finally{await new Promise(resolve=>server.close(resolve));}});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1250,height:850}});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/#companion`);
  const companion=page.locator('#page-companion');
  const casino=page.locator('#page-casino');
  const back=page.getByRole('button',{name:/^Back to Chuddy/});
  const sync=async()=>{
    state.balance++;
    await page.waitForFunction(balance=>document.querySelector('#monies-balance').textContent===balance+' monies',state.balance);
  };
  await page.locator('.chud-cursor').waitFor({state:'visible'});
  assert.equal(await page.locator('.chud-cursor').evaluate(n=>getComputedStyle(n).pointerEvents),'none');
  state.monitoring={desktop_enabled:false,desktop_running:false};
  await page.waitForFunction(()=>document.querySelector('#desktop-interception-status').textContent.includes('Work detection is off'));
  state.monitoring={desktop_enabled:true,desktop_running:true};
  await page.waitForFunction(()=>!document.querySelector('#desktop-interception-status').textContent.includes('Work detection is off'));
  state.work_detection={status:'work',is_work:true,label:'Google Docs',source:'rule'};
  state.interruption={id:'one',phase:'approaching',destination:'casino',label:'Report',test:false};
  await sync();
  assert.equal(await page.locator('#interruption-overlay,#interruption-dismiss,#session-stop,#session-pause,#character-quote').count(),0);
  assert.equal(await companion.isVisible(),true);
  assert.equal(await page.locator('#desktop-interception-status').textContent(),'Work detected');
  Object.assign(state.interruption,{phase:'opening',target_close:'confirmed',
    destination_delivery:'route_ready',fullscreen:'pending'});
  state.casino_opening_id='one';
  state.casino_interruption_active=false;
  state.desktop_interception.covered=true;
  await back.waitFor({state:'visible'});
  assert.equal(await page.locator('.casino-host-bubble').count(),0);
  await page.keyboard.press('Escape');
  assert.equal(await casino.isVisible(),true);
  await page.reload();
  await sync();
  await back.focus();
  await page.keyboard.press('Enter');
  await companion.waitFor({state:'visible'});
  assert.equal(new URL(page.url()).hash,'#companion');
  await sync();
  await sync();
  assert.equal(await companion.isVisible(),true);
  assert.equal(await page.locator('#session-state').textContent(),'Chudding active');
  assert.deepEqual(actions.splice(0),[{action:'casino_leave',opening_id:'one'}]);
  Object.assign(state.interruption,{phase:'error',fullscreen:'failed',
    error_detail:'Windows did not give Chuddy foreground focus.'});
  await page.reload();
  await sync();
  assert.equal(await companion.isVisible(),true,'A late fullscreen result must not replay a routed opening.');

  state.interruption=null;
  await sync();
  await page.locator('.nav-item[data-page="casino"]').click();
  await sync();
  await page.setViewportSize({width:390,height:640});
  await page.reload();
  await sync();
  await back.click();
  assert.equal(await companion.isVisible(),true);
  await page.reload();
  await sync();
  assert.equal(await companion.isVisible(),true,'The active flag may outlive retirement without taking over navigation.');
  await page.setViewportSize({width:1250,height:850});

  // The opening completed and was retired before the next app poll.
  state.casino_opening_id='retired-before-poll';
  await back.waitFor({state:'visible'});
  await back.click();
  await page.reload();
  await sync();
  assert.equal(await companion.isVisible(),true,'A retired opening routes once and stays acknowledged after Back.');

  state.interruption={id:'two',phase:'approaching',destination:'casino',label:'Another report',test:false};
  await sync();
  assert.equal(await companion.isVisible(),true);
  state.interruption.phase='opened';
  state.casino_opening_id='two';
  await back.waitFor({state:'visible'});
  await page.keyboard.press('Control+Alt+Escape');
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent==='Session stopped');
  await back.click();
  assert.deepEqual(actions,[{action:'session',operation:'stop'}]);
  state.casino_opening_id='unseen-stopped';
  await sync();
  assert.equal(await companion.isVisible(),true,'A stopped session does not route late openings.');
  state.session='paused';
  await sync();
  assert.equal(await companion.isVisible(),true,'A paused session does not route late openings.');
  state.casino_opening_id=null;
  state.session='running';
  state.interruption={id:'three',phase:'opened',destination:'lcbo',label:'Report',test:false};
  await sync();
  assert.equal(await companion.isVisible(),true);
  state.interruption={id:'four',phase:'preview',destination:'beer_store',label:'Rehearsal',test:true};
  await sync();
  assert.equal(await companion.isVisible(),true);
  state.interruption={id:'five',phase:'preview',destination:'game',label:'Rehearsal',test:true};
  state.casino_opening_id='five';
  await back.click();
  await sync();
  assert.equal(await companion.isVisible(),true);
  state.interruption={id:'six',phase:'error',destination:'casino',error_detail:'The active tab address could not be verified.'};
  await page.waitForFunction(()=>document.querySelector('#desktop-interception-status').textContent==='The active tab address could not be verified.');
  await sync();
  await sync();
  assert.equal(await page.locator('#desktop-interception-status').textContent(),'The active tab address could not be verified.');
  assert.equal(actions.some(p=>p.action==='clam_game_start'),false);
  await page.keyboard.press('Escape');
  assert.equal(actions.some(p=>p.action==='dismiss'),false);
  state.interruption=null;state.desktop_interception.pending_restorations=1;
  await page.locator('[data-page="rules"]').first().click();
  assert.equal(await page.locator('#interception').count(),0);
  assert.equal(await page.locator('#distraction-destination option[value="league"]').textContent(),'Open League of Legends');
  await page.locator('#distraction-destination').selectOption('league');
  await page.locator('#cooldown').fill('0');
  await page.locator('#session-settings-form button').click();
  await page.waitForFunction(()=>document.querySelector('.toast-region').textContent.includes('Session choices saved'));
  assert.equal(actions.find(p=>p.action==='settings').values.distraction_destination,'league');
  assert.equal(actions.find(p=>p.action==='settings').values.cooldown,0);
  assert.equal(Object.hasOwn(actions.find(p=>p.action==='settings').values,'interception'),false);
  state.session='stopped';
  await page.locator('.chud-cursor').waitFor({state:'hidden'});
  assert.deepEqual(errors,[]);
});
