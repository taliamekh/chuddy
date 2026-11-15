'use strict';
// Headless static shell + mocked API. No actual microphone, playback or models.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('far-right plus toggles PTT and explicit always listening; saving stays passive',async t=>{
  const profile={id:'one',name:'Friend'};
  const state={settings:{rules:[],microphone_mode:'push_to_talk',audio_device:null,audio_threshold:.35},profile,profiles:[profile],
    scores:{total:0,recent:[]},balance:100,messages:[],deadlines:[],memory:[],logs:[],hardware:{mode:'simulator'},
    agent:{status:'idle'},audio:{recording:false},voice:{speaking:false},session:'stopped',leaderboard:[],
    microphone:{mode:'push_to_talk',active:false,transcribing:false,status:'off',generation:0},catalog:[]};
  const actions=[];
  let holdId=null, startGate=null, stateOffline=false;
  let waitingForInput=false;
  const ended=new Set();
  const operations=()=>actions.filter(p=>p.action==='microphone').map(p=>p.operation);
  const capture=active=>{
    state.microphone.active=active;
    state.microphone.capturing=active && !waitingForInput;
    state.audio.recording=active;
  };
  const defer=()=>{let release; const promise=new Promise(resolve=>{release=resolve;});return {promise,release};};
  const send=(res,data)=>{res.writeHead(200,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/api/state'){
      if(stateOffline){res.writeHead(503,{'Content-Type':'application/json'});return res.end('{}');}
      return send(res,state);
    }
    if(req.url==='/api/devices')return send(res,[{id:7,name:'Mock computer microphone'}]);
    if(req.url==='/api/action'){
      let raw='';for await(const chunk of req)raw+=chunk;
      const p=JSON.parse(raw);actions.push(p);
      if(p.action==='microphone'){
        if(p.operation==='hold_start'){
          const gate=startGate;startGate=null;
          if(gate?.before) await gate.before.promise;
          if(ended.has(p.hold_id) || state.microphone.active || p.generation!==state.microphone.generation
              || state.microphone.mode!=='push_to_talk' || p.profile_id!==state.profile.id) return send(res,{cancelled:true});
          holdId=p.hold_id;state.microphone.generation++;capture(true);
          const response={...state.microphone};
          if(gate?.after) await gate.after.promise;
          return send(res,response);
        }
        if(p.operation.startsWith('hold_')){
          if(p.operation!=='hold_keepalive') ended.add(p.hold_id);
          if(holdId!==p.hold_id) return send(res,{cancelled:true});
          if(p.operation==='hold_keepalive') return send(res,{accepted:true});
          holdId=null;capture(false);
          if(p.operation==='hold_cancel') {state.microphone.generation++;return send(res,{cancelled:true});}
          return send(res,{text:'hello from held microphone',turn_id:'held-turn'});
        }
        if(p.operation!=='finish') state.microphone.generation++;
        capture(p.operation==='start');
        return send(res,p.operation==='finish'?{text:'hello from mock microphone',source:'mock ASR'}:state.microphone);
      }
      if(p.action==='settings'){
        Object.assign(state.settings,p.values);state.microphone.mode=state.settings.microphone_mode;
        state.microphone.active=false;state.audio.recording=false;
      }
      return send(res,{});
    }
    const relative=req.url==='/'?'web/index.html':decodeURIComponent(req.url.slice(1)).split('?')[0];
    const file=path.resolve(root,relative);
    if(!relative.startsWith('web/')||!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png'}[path.extname(file)]||'application/octet-stream'});
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  t.after(async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));});
  const page=await browser.newPage({viewport:{width:1320,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  const button=page.locator('#ptt-toggle');
  const chat=page.locator('#chat-input');
  const waitForOperation=async operation=>{
    const until=Date.now()+5000;
    while(!operations().includes(operation) && Date.now()<until) await new Promise(resolve=>setTimeout(resolve,10));
    assert.ok(operations().includes(operation),operation+' should reach the service');
  };
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>!document.querySelector('#ptt-toggle').disabled);
  for (const viewport of [{width:1320,height:900},{width:390,height:760}]) {
    await page.setViewportSize(viewport);
    assert.equal(await page.locator('.chat-suggestions, [data-prompt]').count(), 0);
    for (const control of [chat, page.getByRole('button',{name:'Send message'}), button]) {
      await control.scrollIntoViewIfNeeded();
      const box = await control.boundingBox();
      assert.ok(box && box.width > 0 && box.x >= 0 && box.x + box.width <= viewport.width,
                `Composer control fits at ${viewport.width}px`);
    }
  }
  await page.setViewportSize({width:1320,height:900});
  assert.equal(actions.length,0,'Startup must not enable capture.');
  for (const [message, submit] of [
    ['What is 2 + 2?', () => page.getByRole('button',{name:'Send message'}).click()],
    ['Can you spell report?', () => chat.press('Enter')],
  ]) {
    await chat.fill(message);
    await Promise.all([
      page.waitForResponse(response=>response.url().endsWith('/api/action')
        && response.request().postDataJSON()?.action==='chat'),
      submit(),
    ]);
    assert.deepEqual(actions.at(-1),{action:'chat',text:message});
  }
  assert.equal(await page.locator('#microphone-mode').inputValue(),'push_to_talk');
  assert.equal(await page.locator('#chat-form>button').last().getAttribute('id'),'ptt-toggle');
  assert.equal(await page.locator('#ptt-toggle').textContent(),'+');
  assert.match(await button.getAttribute('title'),/Hold numpad \+/);
  await page.locator('#ptt-toggle').click();
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='true');
  assert.match(await page.locator('#ptt-toggle').getAttribute('aria-label'),/Finish and send/);
  await page.locator('#ptt-toggle').click();
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='false');
  assert.deepEqual(actions.filter(p=>p.action==='microphone').map(p=>p.operation),['start','finish']);
  await page.locator('.nav-item[data-page="settings"]').click();
  await page.waitForFunction(()=>document.querySelector('#audio-device option[value="7"]'));
  await page.locator('#microphone-mode').selectOption('always_listening');
  await page.locator('#audio-device').selectOption('7');
  await page.locator('#audio-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('.toast').textContent.includes('Audio settings saved'));
  const settings=actions.find(p=>p.action==='settings').values;
  assert.equal(settings.microphone_mode,'always_listening');assert.equal(settings.audio_device,7);
  assert.equal(actions.filter(p=>p.action==='microphone').length,2,'Preference save must not start microphone.');
  await page.locator('#audio-device').selectOption('');
  await page.locator('#audio-form button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('#audio-form button[type="submit"]').disabled===false);
  assert.equal(actions.filter(p=>p.action==='settings').at(-1).values.audio_device,null);
  await page.locator('#refresh-devices').click();
  await page.waitForTimeout(150);
  assert.equal(await page.locator('#audio-device').inputValue(),'','Refreshing must preserve an explicitly saved System default.');
  await page.locator('.nav-item[data-page="companion"]').click();
  await page.locator('#ptt-toggle').click();
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='true');
  assert.equal(await page.locator('#ptt-toggle').getAttribute('aria-label'),'Turn off microphone');
  assert.match(await page.locator('#mic-state').textContent(),/Always listening/);
  state.microphone.transcribing=true;
  await page.waitForTimeout(700);
  assert.equal(await page.locator('#ptt-toggle').isEnabled(),true,'Always-listening off stays available during transcription.');
  await page.locator('#ptt-toggle').click();state.microphone.transcribing=false;
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='false');
  assert.deepEqual(actions.filter(p=>p.action==='microphone').map(p=>p.operation),['start','finish','start','stop']);
  for(const width of [1320,390]){
    await page.setViewportSize({width,height:900});
    const geometry=await page.evaluate(()=>{
      const mic=document.querySelector('#ptt-toggle').getBoundingClientRect(),send=document.querySelector('.send-button').getBoundingClientRect();
      return {after:mic.left>=send.right-1,inside:mic.right<=innerWidth&&mic.left>=0};
    });
    assert.deepEqual(geometry,{after:true,inside:true});
  }

  const beforeAlways=operations().length;
  await chat.focus();
  await page.keyboard.press('NumpadAdd');
  assert.equal(await chat.inputValue(),'+');
  assert.equal(operations().length,beforeAlways);
  await chat.fill('');
  state.settings.microphone_mode='push_to_talk';
  state.microphone.mode='push_to_talk';
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').title.includes('Hold numpad'));

  actions.length=0;
  await page.keyboard.press('Shift+Equal');
  assert.equal(await chat.inputValue(),'+');
  await chat.fill('');
  waitingForInput=true;
  await page.keyboard.down('NumpadAdd');
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='true');
  await page.waitForFunction(()=>document.querySelector('#mic-state').textContent==='Waiting for microphone audio…');
  assert.equal(await button.evaluate(node=>node.classList.contains('microphone-active')),false);
  waitingForInput=false;
  state.microphone.capturing=true;
  await page.waitForFunction(()=>document.querySelector('#ptt-toggle').classList.contains('microphone-active'));
  await page.keyboard.down('NumpadAdd'); // Playwright marks the second keydown as a repeat.
  await waitForOperation('hold_keepalive');
  assert.equal(operations().filter(op=>op==='hold_start').length,1);
  assert.equal(operations().includes('hold_finish'),false);
  assert.equal(await chat.inputValue(),'');
  await page.keyboard.up('NumpadAdd');
  await page.getByText('Heard: hello from held microphone',{exact:true}).waitFor();
  assert.equal(operations().filter(op=>op==='hold_finish').length,1);
  assert.equal(actions.find(p=>p.operation==='hold_start').hold_id,actions.find(p=>p.operation==='hold_finish').hold_id);
  await button.waitFor();
  await page.waitForFunction(()=>!document.querySelector('#ptt-toggle').disabled);

  // Release must reach the already-started capture without waiting for the Start response.
  actions.length=0;
  const delayedResponse=defer();startGate={after:delayedResponse};
  await page.keyboard.down('NumpadAdd');
  await waitForOperation('hold_start');
  await page.keyboard.up('NumpadAdd');
  await waitForOperation('hold_finish');
  assert.equal(state.microphone.active,false);
  delayedResponse.release();
  await page.waitForFunction(()=>!document.querySelector('#ptt-toggle').disabled);
  assert.equal(operations().filter(op=>op==='hold_finish').length,1);

  // A release arriving ahead of Start must make that late Start inert.
  actions.length=0;
  const delayedAdmission=defer();startGate={before:delayedAdmission};
  await page.keyboard.down('NumpadAdd');
  await waitForOperation('hold_start');
  await page.keyboard.up('NumpadAdd');
  await waitForOperation('hold_finish');
  delayedAdmission.release();
  await page.waitForFunction(()=>!document.querySelector('#ptt-toggle').disabled);
  assert.equal(state.microphone.active,false);

  for (const loseAccess of [
    () => page.evaluate(()=>window.dispatchEvent(new Event('blur'))),
    () => page.evaluate(()=>{
      Object.defineProperty(document,'hidden',{configurable:true,value:true});
      document.dispatchEvent(new Event('visibilitychange'));
      delete document.hidden;
    }),
    () => {state.session='paused';},
    () => {state.profile={id:'two',name:'Second profile'};},
    () => {state.microphone.mode='always_listening';},
    () => page.locator('.nav-item[data-page="settings"]').click(),
    () => {stateOffline=true;},
  ]) {
    actions.length=0;
    await page.keyboard.down('NumpadAdd');
    await page.waitForFunction(()=>document.querySelector('#ptt-toggle').getAttribute('aria-pressed')==='true');
    await loseAccess();
    await waitForOperation('hold_cancel');
    assert.equal(state.microphone.active,false);
    await page.keyboard.up('NumpadAdd');
    assert.equal(operations().includes('hold_finish'),false);
    stateOffline=false;
    state.session='stopped';
    state.profile=profile;
    state.microphone.mode='push_to_talk';
    if(await page.locator('#page-settings').isVisible()){
      const field=page.locator('#ai-model');
      await field.fill('editing');
      await field.focus();
      await page.keyboard.press('NumpadAdd');
      assert.equal(await field.inputValue(),'editing+');
      await page.locator('.nav-item[data-page="companion"]').click();
    }
    await page.waitForFunction(()=>!document.querySelector('#ptt-toggle').disabled
      && document.querySelector('#ptt-toggle').title.includes('Hold numpad')
      && document.querySelector('#session-state').textContent==='Session stopped'
      && document.querySelector('#profile-name').textContent==='Friend');
  }
  assert.deepEqual(errors,[]);
});
