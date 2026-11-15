'use strict';
// Headless Edge only; uses a disposable SQLite wallet, never the running app.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const http=require('node:http');
const {spawnSync}=require('node:child_process');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');
const python = process.env.CHUDDY_TEST_PYTHON || path.join(root, '.venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');

test('casino room against disposable authoritative wallet',async t=>{
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'chuddy-casino-'));
  const database=path.join(temp,'wallet.sqlite');
  const call=body=>{
    const p=spawnSync(python,['-m','tests.casino_api_fixture',database],{cwd:root,input:JSON.stringify(body),encoding:'utf8'});
    assert.equal(p.status,0,p.stderr);return JSON.parse(p.stdout);
  };
  let dropNext=false, gateNext=false, releaseGate=null, sawGate=null;
  const mutations=[];
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/api/state'){res.setHeader('Content-Type','application/json');const s=call({action:'state'}).data;res.end(JSON.stringify({...s,settings:{rules:[]},profiles:[s.profile],messages:[],scores:{burp:0,fart:0,total:0,recent:[]},deadlines:[],memory:[],logs:[],leaderboard:[],catalog:[],hardware:{mode:'simulator'},agent:{status:'idle'},audio:{recording:false},voice:{speaking:false},session:'stopped',earnings:{level:2,multiplier:2,total_earned:70,daily_remaining:430,rates:{burp:3,fart:5}}}));return;}
    if(req.url==='/api/action'){
      assert.equal(req.headers['x-chuddy-token'],'test-token');
      let body='';for await(const chunk of req)body+=chunk;
      const payload=JSON.parse(body);mutations.push(payload);
      const response=call(payload);
      if(gateNext){gateNext=false;await new Promise(resolve=>{releaseGate=resolve;if(sawGate)sawGate();});}
      if(dropNext){dropNext=false;res.writeHead(500,{'Content-Type':'application/json'});res.end(JSON.stringify({error:'Simulated lost receipt after commit.'}));return;}
      res.writeHead(response.status,{'Content-Type':'application/json'});res.end(JSON.stringify(response.data));return;
    }
    if(req.url==='/full'){
      res.setHeader('Content-Type','text/html; charset=utf-8');res.end(fs.readFileSync(path.join(root,'web/index.html'),'utf8').replace('<head>','<head><script>window.CHUDDY_TOKEN="test-token"</script>'));return;
    }
    if(req.url==='/reference.png'){res.setHeader('Content-Type','image/png');res.end(fs.readFileSync(path.join(root,'inputs/chuddy-reference.png')));return;}
    if(req.url==='/'){
      res.setHeader('Content-Type','text/html; charset=utf-8');
      res.end('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="/web/style.css"><link rel="stylesheet" href="/web/casino-room.css"><style>body{display:block;margin:0;padding:16px;background:#f8eedf}#room{max-width:1280px;margin:auto}</style><main id="room"></main><script>window.CHUDDY_TOKEN="test-token"</script><script src="/web/monies.js"></script><script src="/web/casino-room.js"></script><script>window.room=ChuddyCasinoRoom.mount(document.getElementById("room"),{animationMs:30});window.refresh=async()=>{const s=await(await fetch("/api/state")).json();room.update({...s,connected:true,earnings:{level:2,multiplier:2,total_earned:70,daily_remaining:430,passive:{status:"earning",rate_per_second:2,multiplier:2,boost_remaining_ms:59000}}});return s;};document.getElementById("room").addEventListener("chuddy:casino-wallet",()=>refresh());refresh();</script>');return;
    }
    const filename=path.resolve(root,'.'+req.url);
    if(filename.startsWith(root+path.sep+'web'+path.sep)&&fs.existsSync(filename)){
      res.setHeader('Content-Type',filename.endsWith('.js')?'text/javascript; charset=utf-8':filename.endsWith('.css')?'text/css; charset=utf-8':'image/png');res.end(fs.readFileSync(filename));return;
    }
    res.writeHead(404);res.end();
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  t.after(async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));fs.rmSync(temp,{recursive:true,force:true});});
  const page=await browser.newPage({viewport:{width:1380,height:1200}});
  await page.addInitScript(()=>{
    window.motionTrace=[];
    new MutationObserver(records=>{
      for(const record of records){
        if(record.type==='attributes'&&record.target.dataset.actor)motionTrace.push({actor:record.target.dataset.actor,pose:record.target.dataset.pose});
        for(const node of record.addedNodes||[])if(node.nodeType===1&&/^casino-flying-/.test(node.className)){
          const frames=node.getAnimations()[0]?.effect.getKeyframes()||[];
          motionTrace.push({flight:node.className,start:frames[0]?.transform,end:frames.at(-1)?.transform});
        }
      }
    }).observe(document,{subtree:true,childList:true,attributes:true,attributeFilter:['data-pose']});
  });
  page.setDefaultTimeout(15000);
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  await page.waitForFunction(()=>document.querySelector('[data-spin]').disabled===false);
  const settle=()=>page.waitForFunction(()=>!document.querySelector('.casino-tables-extension').classList.contains('casino-dealing'));
  const state=()=>call({action:'state'}).data;
  const qa=path.join(root,'.chuddy','casino-qa');fs.mkdirSync(qa,{recursive:true});
  const fund=async()=>{call({action:'deposit',amount:10000,request_id:'fund-'+Date.now()});await page.evaluate(()=>refresh());};
  await fund();
  await t.test('raster assets, earnings and no automatic wallet actions',async()=>{
    assert.equal(mutations.length,0);
    await page.waitForFunction(()=>[...document.images].every(img=>img.complete));
    assert.match(await page.locator('[data-earned-title]').textContent(),/2 monies\/s · 2× · 59s/);
    assert.equal(await page.locator('[data-game]').count(),4);
    assert.ok(await page.locator('.casino-patrick').evaluate(el=>getComputedStyle(el).backgroundImage.includes('gestures.png')));
  });
  await t.test('slots are authoritative and duplicate submit is ignored',async()=>{
    await page.evaluate(()=>{const form=document.querySelector('[data-form]');form.dispatchEvent(new Event('submit',{cancelable:true}));form.dispatchEvent(new Event('submit',{cancelable:true}));});
    await page.waitForFunction(()=>!document.querySelector('[data-spin]').disabled);
    assert.equal(mutations.filter(row=>row.action==='spin').length,1);
    assert.equal(await page.locator('[data-recent] li b').count(),1);
  });
  await t.test('blackjack lost reply retry preserves one reserve and resumes after reload',async()=>{
    await page.click('[data-game="blackjack"]');
    await page.waitForFunction(()=>!document.querySelector('[data-table-start]').disabled);
    const before=state().ledger_count;dropNext=true;
    await page.click('[data-table-start]');
    await page.waitForFunction(()=>!document.querySelector('[data-table-retry]').hidden&&!document.querySelector('[data-table-retry]').disabled);
    const after=state().ledger_count;
    assert.ok(after>=before+1);
    await page.reload();await page.click('[data-game="blackjack"]');
    await page.waitForFunction(()=>!document.querySelector('[data-table-retry]').hidden&&!document.querySelector('[data-table-retry]').disabled);
    await page.click('[data-table-retry]');await settle();
    await page.waitForFunction(()=>document.querySelector('[data-table-retry]').hidden);
    assert.equal(state().ledger_count,after);
    await page.screenshot({path:path.join(qa,'blackjack-active.png'),fullPage:true});
    await page.locator('.casino-table-room').screenshot({path:path.join(qa,'blackjack-room.png')});
    const starts=mutations.filter(row=>row.action==='casino_start'&&row.game==='blackjack');
    assert.equal(starts[0].request_id,starts[1].request_id);
    for(let n=0;n<3&&!state().casino.round.complete;n++){
      await page.click('[data-move="stand"]');await settle();
      await page.waitForFunction(()=>!document.querySelector('[data-move="stand"]')||!document.querySelector('[data-move="stand"]').disabled);
    }
    assert.equal(state().casino.round.complete,true);
    assert.equal(await page.locator('[data-table-status]').textContent(),state().casino.round.payout.toLocaleString('en-US')+' monies returned');
  });
  await t.test('you are Patrick and four distinct bots play proper poker',async()=>{
    await page.click('[data-game="poker"]');await page.locator('[data-table-stake]').fill('100');
    await page.click('[data-table-start]');await settle();
    await page.waitForFunction(()=>document.querySelectorAll('.casino-poker-seat').length===5);
    const players=await page.locator('.casino-poker-seat h3').allTextContents();
    assert.ok(players.some(name=>name.includes('You (Patrick)')));
    for(const name of ['SpongeBob','Squidward','Plankton','Mr. Krabs'])assert.ok(players.some(row=>row.includes(name)),name);
    assert.equal(players.filter(name=>name.includes('Patrick')).length,1);
    assert.equal(players.some(name=>name.includes('Gary')),false);
    assert.equal(await page.locator('[data-dealer] [data-actor="gary"]').count(),1);
    await page.screenshot({path:path.join(qa,'poker-active.png'),fullPage:true});
    await page.locator('.casino-table-room').screenshot({path:path.join(qa,'poker-room.png')});
    assert.equal(await page.locator('.casino-poker-bots .casino-card-back').count(),state().casino.round.view.players.slice(1).filter(player=>!player.folded).length*2);
    const animated=await page.locator('.casino-actor-art').evaluateAll(nodes=>nodes.some(el=>getComputedStyle(el).animationName==='casino-seated-breath'));
    assert.equal(animated,true);
    for(let n=0;n<35&&!state().casino.round.complete;n++){
      const legal=state().casino.round.view.legal_actions;
      await page.click('[data-move="'+(legal.check?'check':'call')+'"]');await settle();
      await page.waitForFunction(()=>[...document.querySelectorAll('[data-move]')].every(button=>!button.disabled));
    }
    assert.equal(state().casino.round.complete,true);
    assert.equal(state().casino.round.view.players.length,5);
    assert.equal(await page.locator('.casino-seat-folded [data-actor]').evaluateAll(nodes=>nodes.every(node=>node.dataset.pose==='hold')),true);
    const trace=await page.evaluate(()=>motionTrace);
    for(const name of ['gary','patrick','spongebob','squidward','plankton','krabs'])assert.ok(trace.some(row=>row.actor===name&&row.pose!=='hold'),'actor interaction '+name);
    assert.ok(trace.some(row=>row.flight==='casino-flying-card'&&row.start!==row.end),'cards travel between dealer and players');
    assert.ok(trace.some(row=>row.flight==='casino-flying-chips'&&row.start!==row.end),'chips travel to pot');
    await page.waitForFunction(()=>document.querySelector('.casino-sponge-canvas')?.parentElement.dataset.rasterPainted==='true');
  });
  await t.test('roulette uses backend pocket and stake-inclusive payout',async()=>{
    await page.click('[data-game="roulette"]');await page.click('[data-roulette-number="0"]');
    await page.locator('[data-table-stake]').fill('5');
    assert.equal(await page.locator('[data-bet-mat] b').textContent(),'5');
    await page.click('[data-table-start]');await settle();
    await page.waitForFunction(()=>document.querySelector('.casino-wheel-result').textContent!=='◉');
    const result=state().casino.round.view;
    assert.equal(result.selection.kind,'straight');assert.equal(result.selection.value,0);
    assert.equal(await page.locator('.casino-wheel-result').textContent(),String(result.number));
    assert.equal(await page.locator('.casino-wheel-pocket').count(),37);
    assert.equal(await page.locator('.casino-roulette-numbers button').count(),37);
    assert.deepEqual(await page.locator('.casino-roulette-numbers button').evaluateAll(nodes=>nodes.slice(0,13).map(node=>Number(node.textContent))),[0,3,6,9,12,15,18,21,24,27,30,33,36]);
    const positions=await page.evaluate(()=>({wheel:document.querySelector('.casino-roulette-pedestal').getBoundingClientRect().right,board:document.querySelector('.casino-roulette-layout').getBoundingClientRect().left}));
    assert.ok(positions.wheel<positions.board,'wide roulette layout has wheel beside betting board');
    await page.locator('.casino-table-room').screenshot({path:path.join(qa,'roulette-room.png')});
  });
  await t.test('Stop prevents late character/card actions after a committed request',async()=>{
    await page.click('[data-game="blackjack"]');await page.locator('[data-table-stake]').fill('10');
    const seen=new Promise(resolve=>{sawGate=resolve;});gateNext=true;
    await page.click('[data-table-start]');await seen;
    await page.evaluate(()=>{room.stopAnimation();window.stoppedFlights=motionTrace.filter(row=>row.flight).length;});
    releaseGate();sawGate=null;
    await page.waitForFunction(()=>!document.querySelector('[data-table-start]').textContent.includes('dealing'));
    assert.equal(await page.evaluate(()=>motionTrace.filter(row=>row.flight).length),await page.evaluate(()=>stoppedFlights));
    assert.equal(await page.locator('[data-table-fx]>*').count(),0);
    for(let n=0;n<3&&!state().casino.round.complete;n++){
      await page.click('[data-move="stand"]');await settle();
      await page.waitForFunction(()=>!document.querySelector('[data-move="stand"]')||!document.querySelector('[data-move="stand"]').disabled);
    }
  });
  await t.test('full app shell fits every game and all core controls without scrolling',async()=>{
    const full=await browser.newPage({viewport:{width:1205,height:714}});
    try{
      await full.emulateMedia({reducedMotion:'reduce'});
      await full.goto(`http://127.0.0.1:${server.address().port}/full#casino`);
      await full.waitForFunction(()=>!document.querySelector('[data-spin]').disabled);
      assert.equal(await full.locator('[data-casino-nav="admin"]').count(),0);
      assert.equal(await full.locator('#admin-console').isVisible(),false);
      await full.keyboard.press('Control+s');
      assert.equal(await full.locator('#admin-command').isVisible(),true);
      await full.keyboard.press('Escape');
      assert.equal(await full.locator('#admin-console').isVisible(),false);
      for(const viewport of [{width:1205,height:714},{width:980,height:640},{width:1920,height:1080},{width:390,height:760}]){
        await full.setViewportSize(viewport);
        for(const game of ['slots','blackjack','poker','roulette']){
          await full.click('button[data-game="'+game+'"]');
          await full.waitForTimeout(80);
          const report=await full.evaluate(()=>{
            const selectors=['.casino-game-picker','.casino-intro h1','.casino-slot-viewport','.casino-cabinet','.casino-shell-crown','.casino-machine-base','.casino-reels','[data-form]','.casino-table-heading','.casino-scene-viewport','[data-dealer]','[data-seat]','.casino-poker-board','.casino-dealer-hand','.casino-roulette-wheel','.casino-roulette-layout','[data-action-dock] button','[data-table-form]'];
            const failures=[];
            for(const selector of selectors)for(const node of document.querySelectorAll(selector)){
              if(!node.checkVisibility())continue;const b=node.getBoundingClientRect();
              if(b.width>0&&b.height>0&&(b.top<-.5||b.left<-.5||b.bottom>innerHeight+.5||b.right>innerWidth+.5))failures.push({selector,top:b.top,bottom:b.bottom,left:b.left,right:b.right});
              if(['.casino-cabinet','.casino-shell-crown','.casino-machine-base'].includes(selector)){
                const viewport=document.querySelector('.casino-slot-viewport').getBoundingClientRect();
                if(b.top<viewport.top-.5||b.bottom>viewport.bottom+.5)failures.push({selector,clipped:true,top:b.top,bottom:b.bottom,viewportTop:viewport.top,viewportBottom:viewport.bottom});
              }
            }
            return{overflowY:document.documentElement.scrollHeight>innerHeight+1,overflowX:document.documentElement.scrollWidth>innerWidth+1,failures};
          });
          assert.deepEqual(report,{overflowY:false,overflowX:false,failures:[]},game+' '+JSON.stringify(viewport));
          await full.screenshot({path:path.join(qa,'fit-'+game+'-'+viewport.width+'x'+viewport.height+'.png')});
        }
      }
      await full.setViewportSize({width:1205,height:714});
      await full.click('button[data-game="poker"]');await full.locator('[data-table-stake]').fill('100');await full.click('[data-table-start]');
      await full.waitForFunction(()=>document.querySelector('[data-move="fold"]')&&!document.querySelector('[data-move="fold"]').disabled);
      assert.equal(await full.locator('[data-table-form]').isVisible(),false);
      const actionGeometry=await full.evaluate(()=>({
        foldReady:Boolean(document.querySelector('[data-move="fold"]')?.checkVisibility()&&!document.querySelector('[data-move="fold"]').disabled),
        bounds:[...document.querySelectorAll('[data-action-dock] button,[data-action-dock] input')].filter(node=>node.checkVisibility()).map(node=>{
          const b=node.getBoundingClientRect();return{move:node.dataset.move||'raise input',left:b.left,top:b.top,right:b.right,bottom:b.bottom};
        })}));
      assert.equal(actionGeometry.foldReady,true,'Active poker Fold must remain ready during the atomic geometry snapshot.');
      for(const b of actionGeometry.bounds)assert.ok(b.left>=0&&b.top>=0&&b.right<=1205&&b.bottom<=714,b.move+' fits the active action dock');
      await full.screenshot({path:path.join(qa,'fit-poker-active-1205x714.png')});
      await full.click('[data-move="fold"]');await full.waitForFunction(()=>!document.querySelector('[data-table-start]').disabled);
      await full.click('[data-open-info]');assert.equal(await full.locator('[data-info-dialog]').isVisible(),true);
      assert.equal(await full.locator('[data-room-stop]').count(),0);
      await full.click('[data-close-info]');assert.equal(await full.locator('[data-info-dialog]').isVisible(),false);
    }finally{await full.close();}
  });
  await t.test('reduced motion, escaping, narrow widths, navigation and lifecycle',async()=>{
    await page.emulateMedia({reducedMotion:'reduce'});await page.evaluate(()=>refresh());
    assert.equal(await page.locator('.casino-portrait-art').first().evaluate(el=>getComputedStyle(el).animationName),'none');
    for(const width of [1380,880,390,320]){
      await page.setViewportSize({width,height:1200});
      for(const game of ['slots','blackjack','poker','roulette']){
        await page.click('[data-game="'+game+'"]');
        if(game==='poker'){
          assert.equal(await page.locator('[data-dealer-line]').isVisible(),false);
          assert.equal(await page.locator('[data-dealer-line]').textContent(),'');
          if(state().casino.round.game!=='poker'){
            assert.doesNotMatch(await page.locator('[data-table-status]').textContent(),/Round settled/);
          }
        }
        assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'horizontal overflow '+width+'/'+game);
        if(width===1380||width===390)await page.screenshot({path:path.join(qa,game+'-'+width+'.png'),fullPage:true});
      }
    }
    await page.evaluate(()=>{window.destinations=[];document.getElementById('room').addEventListener('chuddy:casino-navigate',event=>destinations.push(event.detail.destination));});
    await page.click('[data-open-info]');await page.click('[data-casino-nav="leaderboard"]');assert.equal(await page.locator('[data-info-dialog]').isVisible(),false);
    assert.deepEqual(await page.evaluate(()=>destinations),['leaderboard']);
    await page.evaluate(()=>room.update({profile:{id:'bad',name:'<img src=x onerror="alert(1)">'},balance:100,connected:true}));
    assert.equal(await page.locator('[data-profile] img').count(),0);
    await page.evaluate(()=>room.destroy());assert.equal(await page.locator('#room>*').count(),0);
  });
  assert.deepEqual(errors,[]);
});
