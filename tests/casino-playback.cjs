'use strict';
// Deterministic public-result playback; no live app, wallet, audio or desktop.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('casino public actions are readable, serial and cancellable',async t=>{
  const server=http.createServer((req,res)=>{
    if(req.url==='/'){
      res.setHeader('Content-Type','text/html; charset=utf-8');
      return res.end('<!doctype html><meta charset="utf-8"><link rel="stylesheet" href="/web/casino-room.css"><style>*,*::before,*::after{box-sizing:border-box}body{margin:0}#room{height:100dvh}</style><div id="room"></div><script src="/web/casino-room.js"></script>');
    }
    const file=path.resolve(root,'.'+req.url);
    if(!file.startsWith(path.join(root,'web')+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.setHeader('Content-Type',file.endsWith('.js')?'application/javascript; charset=utf-8':file.endsWith('.css')?'text/css; charset=utf-8':'image/png');res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  t.after(async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));});
  const page=await browser.newPage({viewport:{width:1205,height:714}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:'+server.address().port);
  await page.clock.install();
  await page.evaluate(()=>{
    const profile={id:'fixture',name:'Playback fixture'};
    const clone=x=>JSON.parse(JSON.stringify(x));
    const hand=(cards,total)=>({cards,total,soft:false,bet:10,status:'active',outcome:null});
    const bj={game:'blackjack',stake:10,reserve:40,committed:10,complete:false,payout:null,active_hand:0,
      hands:[hand(['8S','8H'],16)],dealer:{cards:['6C',null],total:null},actions:['hit','stand','double','split']};
    const players=['You (Patrick)','SpongeBob','Squidward','Plankton','Mr. Krabs'].map(name=>({name,stack:100,contributed:0,street_bet:0,folded:false,cards:[null,null]}));
    players[0].cards=['AS','KD'];
    const row=(seat,action,amount,stack,bet,pot,street='preflop')=>({seat,name:players[seat].name,action,amount,street,
      ...(seat?{text:'I '+action+(amount?' '+amount+' monies':'')+'.'}:{}),chips:{stack,street_bet:bet,contributed:100-stack,pot,folded:action==='fold'}});
    const log=[row(1,'small blind',1,99,1,1),row(2,'big blind',2,98,2,3),row(3,'call',2,98,2,5),row(4,'fold',0,100,0,5)];
    const poker={game:'poker',buy_in:100,reserve:100,complete:false,payout:null,players:clone(players),community:[],street:'preflop',pot:5,log,
      legal_actions:{fold:true,call:true,call_amount:2,raise:true,min_raise_to:4,max_raise_to:100},pots:[],showdown:false};
    for(const r of log)Object.assign(poker.players[r.seat],r.chips);
    const roulette={game:'roulette',stake:10,reserve:10,complete:true,number:7,color:'red',selection:{kind:'red'},payout:20,won:true};
    window.resetPlayback=(game,opts={})=>{
      window.room?.destroy?.();window.requests=[];window.trace=[];window.current=null;
      const host=document.getElementById('room');
      window.room=ChuddyCasinoRoom.mount(host,{...opts,request:async p=>{requests.push(p);return{...p,balance:10010,payout:20,symbols:['clam','clam','hat']};},gameRequest:async p=>{
        if(p.action==='casino_state')return{profile_id:profile.id,round:current};
        requests.push(p);
        let view;
        if(p.action==='casino_start'){
          view=clone({blackjack:bj,poker,roulette}[game]);
          if(opts.longSpeech&&game==='poker'){
            for(const row of view.log)row.text='We have plenty of time to look at the board, count the chips for this move, and figure out what to do when the next card gets here.';
          }
        }
        else if(game==='blackjack'){
          view=clone(current.view);
          if(p.move==='split'){view.hands=[hand(['8S','2D'],10),hand(['8H','3D'],11)];view.committed=20;view.actions=['hit','stand','double'];}
          else if(view.hands.length===2&&view.active_hand===0){view.hands[0].status='stood';view.active_hand=1;}
          else {view.complete=true;view.payout=20;view.dealer={cards:['6C','7D','5S'],total:18};view.hands.forEach(h=>{h.status='complete';h.outcome='lose';});view.actions=[];}
        }else if(opts.showdown&&current.revision>=2){
          view=clone(current.view);
          view.log.push(row(0,'raise',98,0,98,106,'flop'),row(1,'call',94,4,94,200,'flop'),
            row(2,'call',98,0,98,298,'flop'),row(3,'fold',0,98,0,298,'flop'));
          for(const r of view.log)Object.assign(view.players[r.seat],r.chips);
          Object.assign(view,{community:['2C','3D','4H','KH','9S'],street:'complete',pot:0,complete:true,payout:0,
            legal_actions:{},showdown:true,pots:[{amount:294,winners:[1]},{amount:4,winners:[2],refund:true}]});
          Object.assign(view.players[0],{hand_name:'One pair',all_in:true});
          Object.assign(view.players[1],{cards:['5H','6H'],hand_name:'Straight',stack:298});
          Object.assign(view.players[2],{cards:['QD','QH'],hand_name:'One pair',stack:4});
        }else{
          view=clone(current.view);view.community=['2C','3D','4H'];view.street='flop';view.pot=8;
          view.legal_actions={fold:true,call:true,call_amount:7,raise:true,min_raise_to:14,max_raise_to:98};
          view.log.push(row(0,'call',2,98,2,7),row(1,'call',1,98,2,8),row(2,'check',0,98,2,8),row(1,'check',0,98,0,8,'flop'),row(2,'check',0,98,0,8,'flop'),row(3,'check',0,98,0,8,'flop'));
          for(const r of view.log)Object.assign(view.players[r.seat],r.chips);
        }
        current={id:'fixture-round',revision:(current?.revision||0)+1,game,complete:view.complete,payout:view.payout,view};
        return{request_id:p.request_id,profile_id:profile.id,round:clone(current)};
      }});
      room.update({profile,balance:10000,connected:true,reducedMotion:opts.reducedMotion===true});
      const observer=new MutationObserver(records=>{
        for(const record of records){
          for(const node of record.addedNodes)if(node.nodeType===1&&node.classList.contains('casino-flying-card')){
            const animation=node.getAnimations()[0],matrix=new DOMMatrix(animation.effect.getKeyframes()[0].transform);
            const layer=host.querySelector('[data-table-fx]').getBoundingClientRect(),shoe=host.querySelector('[data-dealer-deck]').getBoundingClientRect();
            const x=matrix.m41+layer.left,y=matrix.m42+layer.top;
            const landing=new DOMMatrix(animation.effect.getKeyframes().at(-1).transform),box=node.getBoundingClientRect();
            const target=[...host.querySelectorAll('[data-card-slot]')].find(slot=>{const b=slot.getBoundingClientRect(),cx=landing.m41+layer.left+box.width/2,cy=landing.m42+layer.top+box.height/2;return cx>=b.left&&cx<=b.right&&cy>=b.top&&cy<=b.bottom;});
            trace.push({flight:true,count:host.querySelectorAll('.casino-flying-card').length,duration:animation.effect.getTiming().duration,fromShoe:x>=shoe.left&&x<=shoe.right&&y>=shoe.top&&y<=shoe.bottom,target:target?.parentElement.dataset.cardRow+':'+target?.dataset.cardSlot});
          }
          if(record.target.closest?.('[data-playback-caption]'))trace.push({caption:host.querySelector('[data-playback-caption]').textContent,cards:host.querySelectorAll('.casino-poker-board .casino-card:not(.casino-card-back)').length});
        }
      });observer.observe(host,{subtree:true,childList:true});
      const destroy=room.destroy;room.destroy=()=>{observer.disconnect();destroy();};
      if(game!=='slots')host.querySelector('[data-game="'+game+'"]').click();
    };
  });
  page.setDefaultTimeout(5000);
  const reset=async(game,opts)=>{await page.evaluate(([g,o])=>resetPlayback(g,o),[game,opts]);await page.clock.runFor(50);};
  const start=async game=>{await page.locator(game==='slots'?'[data-spin]':'[data-table-start]').click();await page.clock.runFor(50);};
  // Playwright's timer clock does not drive the compositor's flip timeline.
  const finishFlights=()=>page.evaluate(()=>{for(const el of document.querySelectorAll('.casino-flying-card,.casino-flying-chips,[data-card-slot]'))for(const animation of el.getAnimations())if(animation.playState==='running')animation.finish();});
  const drive=async(ms=1000)=>{await page.clock.runFor(ms);await finishFlights();await page.clock.runFor(20);};
  const drain=async()=>{for(let i=0;i<140;i++){if(await page.locator('#room').getAttribute('data-playback-active')!=='true')return;await drive();}assert.fail('playback did not finish');};
  const caption=()=>page.locator('[data-playback-caption]').textContent();
  const qa=path.join(root,'.chuddy','casino-qa','visuals-20261114');
  const desktop={width:1205,height:714};
  const views=[desktop,{width:1920,height:1080},{width:980,height:640},{width:390,height:760}];
  const capture=async(stage,viewport=desktop)=>{
    await page.setViewportSize(viewport);
    await page.clock.runFor(34);
    await page.waitForTimeout(55);
    const {width,height}=viewport;
    await page.screenshot({path:path.join(qa,stage+'-'+width+'x'+height+'.png')});
    const clipping=await page.evaluate(()=>{
      const within=(r,b)=>r.left>=b.left-.5&&r.right<=b.right+.5&&r.top>=b.top-.5&&r.bottom<=b.bottom+.5;
      const root={left:0,top:0,right:innerWidth,bottom:innerHeight};
      const scene=document.querySelector('[data-scene-viewport]').getBoundingClientRect();
      const issues=[];
      for(const node of document.querySelectorAll('[data-action-dock] button,[data-action-dock] input,[data-table-form],.casino-cabinet,.casino-wallet,.casino-player-hand,.casino-seat-placard,.casino-dealer-hand,.casino-roulette-layout,.casino-roulette-wheel')){
        if(!node.checkVisibility())continue;
        const tableArt=node.matches('.casino-player-hand,.casino-seat-placard,.casino-dealer-hand,.casino-roulette-layout,.casino-roulette-wheel');
        if(!within(node.getBoundingClientRect(),tableArt?scene:root))issues.push(node.className||node.dataset.move);
      }
      const overlap=(a,b)=>a.left<b.right-.5&&a.right>b.left+.5&&a.top<b.bottom-.5&&a.bottom>b.top+.5;
      const handPanels=[...document.querySelectorAll('.casino-player-hand')].map(n=>n.getBoundingClientRect());
      const dealerProps=[...document.querySelectorAll('.casino-blackjack-lettering,.casino-blackjack-scene [data-bet-mat],.casino-hand-seats>.casino-actor')];
      for(const prop of dealerProps)if(handPanels.some(hand=>overlap(hand,prop.getBoundingClientRect())))issues.push(prop.className+' covers a hand');
      if(document.documentElement.scrollWidth>innerWidth+1||document.documentElement.scrollHeight>innerHeight+1)issues.push('page scroll');
      return issues;
    });
    assert.deepEqual(clipping,[],stage+' at '+width+'×'+height);
  };
  const advanceCue=async()=>{
    const previous=await caption();
    for(let n=0;n<1200;n++){
      await page.clock.runFor(20);if(await caption()!==previous)return;
      await finishFlights();await page.clock.runFor(1);if(await caption()!==previous)return;
    }
    assert.fail('automatic playback did not reach another cue: '+previous);
  };
  await t.test('all games omit the pace toolbar, stop control and old wallet links',async()=>{
    for(const game of ['slots','blackjack','poker','roulette']){
      await reset(game);
      assert.equal(await page.locator('.casino-playback,[data-playback-speed],[data-playback-pause],[data-playback-next],[data-room-stop],[data-casino-nav="arcade"],[data-casino-nav="wallet"]').count(),0);
      assert.equal(await page.locator('[data-playback-caption]').isVisible(),false);
      const canvas=page.locator(game==='slots'?'.casino-slot-viewport':'[data-table-room]');
      const picker=await page.locator('.casino-game-picker').boundingBox(),area=await canvas.boundingBox();
      assert.ok(area.y-picker.y-picker.height<8,'no empty toolbar row above '+game);
    }
  });
  await t.test('empty chip trays omit zero badges but European zero remains selectable',async()=>{
    await reset('blackjack');assert.equal(await page.locator('[data-bet-mat] .casino-chip-stack').count(),0);
    await reset('roulette');assert.equal(await page.locator('[data-pot] .casino-chip-stack').count(),0);
    assert.equal(await page.locator('[data-roulette-number="0"]').textContent(),'0');
    await page.click('[data-roulette-number="0"]');
    assert.equal(await page.locator('[data-roulette-number="0"]').getAttribute('aria-pressed'),'true');
    assert.equal(await page.locator('[data-bet-mat] b').textContent(),'10');
  });
  await t.test('idle games have no helper paragraphs or visible admin discovery',async()=>{
    for(const game of ['slots','blackjack','poker','roulette']){
      await reset(game);
      assert.equal(await page.locator('[data-casino-nav="admin"],[data-reserve-help]').count(),0);
      assert.doesNotMatch(await page.locator('#room').innerText(),/admin|ctrl\+s|saved locally|unused reserve|final stack returns|choose a stake|choose your stake/i);
      assert.equal(await page.evaluate(()=>requests.length),0);
      assert.equal(await page.locator(game==='slots'?'[data-result]':'[data-table-status]').isVisible(),false);
    }
  });
  await t.test('stake errors stay visible without admin hints and blackjack shows its upfront cost',async()=>{
    await reset('slots');
    await page.locator('[data-bet]').fill('10001');
    assert.equal(await page.locator('[data-spin]').isDisabled(),true);
    assert.equal(await page.locator('[data-stake-help]').isVisible(),true);
    assert.match(await page.locator('[data-stake-help]').textContent(),/1–10,000/);
    await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:0,connected:true}));
    await page.locator('[data-bet]').fill('10');
    assert.equal(await page.locator('[data-stake-help]').textContent(),'Not enough monies');
    assert.doesNotMatch(await page.locator('#room').innerText(),/admin|ctrl\+s/i);
    await reset('blackjack');
    assert.equal(await page.locator('[data-table-start]').textContent(),'Deal · 40 monies');
    assert.equal(await page.locator('[data-table-error]').isVisible(),false);
    await page.locator('[data-table-stake]').fill('10000');
    assert.equal(await page.locator('[data-table-start]').isDisabled(),true);
    assert.equal(await page.locator('[data-table-error]').isVisible(),true);
    assert.equal(await page.locator('[data-table-error]').textContent(),'Not enough monies');
    await page.locator('[data-table-stake]').fill('3');
    assert.match(await page.locator('[data-table-error]').textContent(),/even amount/);
    await page.locator('[data-table-stake]').fill('10');
    assert.equal(await page.locator('[data-table-error]').isVisible(),false);
    assert.equal(await page.locator('[data-table-start]').isDisabled(),false);
  });
  await t.test('settled poker shows the real return once without settlement or reserve prose',async()=>{
    await reset('poker',{animationMs:1});await start('poker');await drain();
    await page.evaluate(()=>{
      current.complete=true;current.payout=210;current.view.complete=true;current.view.payout=210;
      room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10110,connected:true,casino:{profile_id:'fixture',round:current}});
    });
    await page.click('[data-game="blackjack"]');await page.click('[data-game="poker"]');
    assert.equal(await page.locator('[data-table-status]').textContent(),'210 monies returned');
    assert.equal(await page.locator('[data-table-status]').isVisible(),true);
    assert.equal(await page.locator('[data-table-error]').isVisible(),false);
    const visible=await page.locator('#room').innerText();
    assert.equal((visible.match(/210 monies returned/g)||[]).length,1);
    assert.doesNotMatch(visible,/Round settled|reserved for this hand|unused reserve|final stack returns|admin|ctrl\+s/i);
    assert.equal(await page.evaluate(()=>requests.length),1);
  });
  await t.test('default slots spin for seconds and stop reels individually',async()=>{
    await reset('slots');await start('slots');
    await page.clock.runFor(3000);assert.equal(await page.locator('[data-reel][data-stopped]').count(),0);
    await page.clock.runFor(1500);assert.equal(await page.locator('[data-reel][data-stopped]').count(),1);
    assert.equal(await page.locator('[data-spin]').isDisabled(),true);
    await drain();assert.equal(await page.locator('[data-spin]').isDisabled(),false);
    assert.equal(await page.evaluate(()=>requests.length),1);
  });
  await t.test('reel strips scroll through every option and automatically land on the saved result',async()=>{
    await reset('slots');await start('slots');
    const symbols=await page.evaluate(()=>[...document.querySelectorAll('[data-reel]')].map(reel=>{
      const strip=reel.querySelector('.casino-reel-strip'),animation=strip.getAnimations()[0];
      const duration=animation.effect.getTiming().duration,box=reel.getBoundingClientRect(),center=box.top+box.height/2;
      const seen=[0,.25,.5,.75].map(phase=>{
        animation.currentTime=duration*phase;
        return [...strip.children].find(cell=>{const r=cell.getBoundingClientRect();return r.top<=center&&r.bottom>center;})?.dataset.symbol;
      });
      return seen;
    }));
    for(const seen of symbols)assert.deepEqual([...new Set(seen)].sort(),['butt','clam','hat','snack']);
    assert.deepEqual(await page.evaluate(()=>[...document.querySelectorAll('.casino-reel-strip')].map(n=>n.getAnimations()[0].playState)),['running','running','running']);
    await page.clock.runFor(2000);assert.equal(await page.locator('[data-reel][data-stopped]').count(),0);
    await capture('slots-scrolling');
    await drain();
    assert.deepEqual(await page.locator('[data-reel] > span').allTextContents(),['🐚','🐚','⚓']);
    assert.equal(await page.locator('.casino-reel-strip:visible').count(),0);
    assert.equal(await page.locator('[data-result-detail]').isVisible(),false);
    for(const viewport of views)await capture('slots-settled',viewport);
    await page.setViewportSize(desktop);
  });
  await t.test('poker deals briskly clockwise into fixed card spots without premature folds',async()=>{
    await reset('poker');await start('poker');
    assert.equal(await page.locator('.casino-seat-folded').count(),0);
    await drain();
    const flights=await page.evaluate(()=>trace.filter(r=>r.flight));
    assert.equal(flights.length,11); // ten dealt cards, then one folded hand to the muck
    assert.ok(flights.every(r=>r.count===1));assert.ok(flights.every(r=>r.duration===190));
    assert.ok(flights.slice(0,10).every(r=>r.fromShoe),'dealt cards leave the separate shoe');
    assert.deepEqual(flights.slice(0,10).map(r=>r.target),[0,1].flatMap(pass=>[1,2,3,4,0].map(seat=>'seat-'+seat+':'+pass)));
    assert.equal(await page.locator('.casino-seat-folded').count(),1);
  });
  await t.test('poker deals the flop before flop betting and stages chips in order',async()=>{
    await page.locator('[data-move="call"]').click();await page.clock.runFor(50);await drain();
    const rows=await page.evaluate(()=>trace.filter(r=>r.caption));
    const flop=rows.findIndex(r=>r.caption==='Gary deals the flop (3 of 3).');
    const flopAction=rows.findIndex(r=>r.caption.startsWith('SpongeBob · check'));
    assert.ok(flop>=0&&flopAction>flop,JSON.stringify(rows));assert.equal(rows[flopAction].cards,3);
    assert.match(await page.locator('.casino-poker-board>span').first().textContent(),/FLOP · POT 8/);
  });
  await t.test('lifecycle cancellation cancels automatic flights without another wager',async()=>{
    await reset('blackjack',{animationMs:300});await start('blackjack');
    assert.match(await caption(),/Dealing card 1 to you/);
    await page.clock.runFor(310);
    assert.equal(await page.locator('.casino-flying-card').count(),1);
    await page.evaluate(()=>room.stopAnimation());await page.clock.runFor(10000);
    assert.equal(await page.locator('.casino-flying-card').count(),0);
    assert.equal(await page.locator('#room').getAttribute('data-playback-active'),'false');
    assert.equal(await page.locator('.casino-player-hand .casino-card').count(),2);
    assert.equal(await page.evaluate(()=>requests.length),1);
  });
  await t.test('blackjack splits and reveals dealer cards separately at default pace',async()=>{
    await reset('blackjack');await start('blackjack');await drain();
    assert.equal(await page.locator('.casino-dealer-hand .casino-card-back').count(),1);
    await capture('blackjack-player-turn');
    await page.locator('[data-move="split"]').click();await page.clock.runFor(50);await drain();
    assert.equal(await page.locator('.casino-player-hand').count(),2);
    assert.equal(await page.locator('.casino-player-hand .casino-card').count(),4);
    for(const viewport of views)await capture('blackjack-split',viewport);
    await page.setViewportSize(desktop);
    await page.locator('[data-move="stand"]').click();await page.clock.runFor(50);await drain();
    assert.equal(await page.locator('.casino-current-hand h3').textContent(),'Your hand 2 11');
    await page.locator('[data-move="stand"]').click();await page.clock.runFor(50);
    assert.equal(await page.locator('[data-action-dock] [data-move]').count(),0);
    await advanceCue();
    assert.match(await caption(),/reveals the face-down card/);
    assert.equal(await page.locator('.casino-dealer-hand .casino-card-back').count(),1);
    await advanceCue();
    assert.equal(await page.locator('.casino-dealer-hand .casino-card-back').count(),0);
    assert.doesNotMatch(await page.locator('.casino-dealer-hand').innerText(),/hidden/i);
    assert.equal(await page.locator('[data-result-pose="loss"]').count(),0);
    await capture('blackjack-dealer-reveal');
    await drain();
    const rows=await page.evaluate(()=>trace.filter(r=>r.caption).map(r=>r.caption));
    assert.ok(rows.includes('Gary deals a card to your hand 1.'));assert.ok(rows.includes('Gary deals a card to your hand 2.'));
    assert.ok(rows.includes('Gary reveals the face-down card.'));assert.ok(rows.includes('Gary draws another card.'));
    assert.equal(await page.locator('.casino-dealer-hand .casino-card').count(),3);
    assert.equal(await page.locator('[data-result-pose="loss"] [data-actor]').getAttribute('data-pose'),'hold');
    await capture('blackjack-settled',views[3]);await page.setViewportSize(desktop);
  });
  await t.test('roulette does not announce its settled payout in live status before landing',async()=>{
    await reset('roulette');await start('roulette');
    await drive(500);await drive(500);await page.clock.runFor(500);
    assert.equal(await page.locator('.casino-wheel-result').isVisible(),false);
    const status=await page.locator('[data-table-status]').textContent();
    assert.doesNotMatch(status,/Round settled|20 monies returned|20 monies paid/i,
      'The live table status must not announce the known final payout while the wheel is still rolling.');
    await drain();
    assert.equal(await page.locator('[data-table-status]').textContent(),'20 monies returned');
  });
  await t.test('canonical wallet polls do not reveal a roulette or slot return before staged playback ends',async()=>{
    for(const game of ['roulette','slots']){
      await reset(game);await start(game);
      // The known fixture stakes10 and returns20: canonical10010 is a10-clam
      // profit, not merely display of the reserved stake9990.
      await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10010,connected:true,
        ...(current?{casino:{profile_id:'fixture',round:current}}:{})}));
      const selector=game==='roulette'?'[data-table-balance]':'[data-balance]';
      const amount=Number((await page.locator(selector).textContent()).replace(/[^0-9]/g,''));
      assert.ok([10000,9990].includes(amount),game+' prematurely displayed the known return/profit instead of its pre-result or reserved balance: '+amount);
      await drain();
      assert.equal(Number((await page.locator(selector).textContent()).replace(/[^0-9]/g,'')),10010,
        game+' did not reconcile the latest canonical balance after playback.');
      assert.equal(await page.evaluate(()=>requests.length),1,'Playback/polling must not submit another wager.');
    }
  });
  await t.test('stopping or changing profile reconciles a pending presentation balance without late overwrites',async()=>{
    for(const operation of ['stop','profile']){
      await reset('roulette');await start('roulette');
      await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10010,connected:true,
        casino:{profile_id:'fixture',round:current}}));
      const amount=Number((await page.locator('[data-table-balance]').textContent()).replace(/[^0-9]/g,''));
      assert.ok([10000,9990].includes(amount),'Pending step playback exposed its10-clam profit.');
      if(operation==='stop')await page.evaluate(()=>room.stopAnimation());
      else await page.evaluate(()=>room.update({profile:{id:'second',name:'Second'},balance:77,connected:true,
        casino:{profile_id:'second',round:null}}));
      await page.clock.runFor(10000);
      assert.equal(Number((await page.locator('[data-table-balance]').textContent()).replace(/[^0-9]/g,'')),operation==='stop'?10010:77);
      assert.equal(await page.locator('#room').getAttribute('data-playback-active'),'false');
      assert.equal(await page.locator('.casino-flying-card,.casino-flying-chips').count(),0);
      assert.equal(await page.evaluate(()=>requests.length),1,'Cancellation must not issue a follow-up wager.');
    }
  });
  await t.test('roulette hides the result through its automatic six-second roll',async()=>{
    await reset('roulette');await start('roulette');await drive(500);await drive(500);
    await page.clock.runFor(500);assert.equal(await page.locator('.casino-wheel-rotor').evaluate(el=>getComputedStyle(el).animationDuration),'6s');
    assert.equal(await page.locator('.casino-wheel-rotor').evaluate(el=>el.style.getPropertyValue('--landing-angle')),'0deg');
    assert.equal(await page.locator('.casino-wheel-result').isVisible(),false);
    assert.equal(await page.locator('[data-pot] .casino-chip-stack').count(),0);
    await page.clock.runFor(4000);assert.equal(await page.locator('.casino-wheel-result').isVisible(),false);
    await capture('roulette-rolling');
    await drain();assert.equal(await page.locator('.casino-wheel-result').textContent(),'7');
    assert.equal(await page.locator('[data-pot] b').textContent(),'20');
    assert.doesNotMatch(await page.locator('#room').textContent(),/meow/i);
    assert.match(await page.locator('[data-actor="gary"] .casino-actor-art').evaluate(el=>getComputedStyle(el).backgroundImage),/action-gary-dealer-clean/);
    const pointer=await page.locator('.casino-wheel-pocket').evaluateAll(pockets=>
      pockets.reduce((top,pocket)=>pocket.getBoundingClientRect().top<top.getBoundingClientRect().top?pocket:top).textContent);
    assert.equal(pointer,'7','the pocket at the pointer matches the displayed result');
    for(const viewport of views)await capture('roulette-settled',viewport);
    await page.setViewportSize(desktop);
    await reset('poker');
    assert.match(await page.locator('[data-actor="plankton"] .casino-actor-art').evaluate(el=>getComputedStyle(el).backgroundImage),/action-plankton-gary/);
  });
  await t.test('automatic decisions retain default timing and brisk deals',async()=>{
    await reset('poker');await start('poker');await page.clock.runFor(800);
    assert.equal(await page.locator('.casino-flying-chips').evaluate(el=>el.getAnimations()[0].effect.getTiming().duration),850);
    await drain();assert.ok(await page.evaluate(()=>trace.filter(row=>row.flight).every(row=>row.duration===190)));
  });
  await t.test('human choices reveal no future bot bets during playback or polling',async()=>{
    await reset('poker');await start('poker');
    assert.equal(await page.locator('[data-action-dock] [data-move],[data-raise]').count(),0);
    await drain();assert.equal(await page.locator('[data-move="call"]').textContent(),'Call 2');
    await page.click('[data-move="call"]');await page.clock.runFor(50);
    const canonical=await page.evaluate(()=>JSON.parse(JSON.stringify(current)));
    assert.equal(canonical.view.legal_actions.call_amount,7);
    for(let action=0;action<4;action++){
      await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10000,connected:true,casino:{profile_id:'fixture',round:current}}));
      assert.equal(await page.locator('[data-action-dock] [data-move],[data-raise]').count(),0,'in-progress captions/polls must not expose even disabled future choices');
      await advanceCue();
    }
    await drain();
    assert.equal(await page.locator('[data-move="call"]').textContent(),'Call 7');
    assert.equal(await page.locator('[data-raise]').getAttribute('min'),'14');
    assert.equal(await page.locator('[data-move="call"]').isEnabled(),true);
    assert.equal(await page.evaluate(()=>requests.length),2);
  });
  await t.test('long speech clears every seat, board and Gary at desktop and compact sizes',async()=>{
    await reset('poker',{longSpeech:true});await start('poker');
    // Render the same decision at four sizes even on a slow headless runner.
    await page.clock.pauseAt(await page.evaluate(()=>Date.now()+100));
    const seen=new Set();
    try{for(let action=0;action<24&&seen.size<4;action++){
      const speaker=await page.locator('[data-player-speech]').getAttribute('data-speaker');
      if(await page.locator('[data-player-speech]').isVisible()&&!seen.has(speaker)){
        seen.add(speaker);
        for(const viewport of views){
          await capture('poker-long-speech-seat-'+speaker,viewport);
          const report=await page.evaluate(()=>{
            const bubble=document.querySelector('[data-player-speech]'),b=bubble.getBoundingClientRect();
            const obstacles=[...document.querySelectorAll('[data-actor],.casino-seat-placard,.casino-poker-board,[data-dealer-deck],[data-muck],[data-pot]')].filter(n=>n.checkVisibility());
            const overlap=(a,b)=>b.left<a.right-.1&&b.right>a.left+.1&&b.top<a.bottom-.1&&b.bottom>a.top+.1;
            const overlaps=obstacles.filter(node=>overlap(b,node.getBoundingClientRect())).map(n=>n.dataset.actor||n.className);
            const faces=obstacles.filter(node=>node.matches('[data-actor]'));
            const faceClashes=faces.flatMap(face=>obstacles.filter(node=>node!==face&&overlap(face.getBoundingClientRect(),node.getBoundingClientRect())).map(node=>face.dataset.actor+' / '+(node.dataset.actor||node.className)));
            const board=document.querySelector('.casino-poker-board').getBoundingClientRect();
            const panelsClear=[...document.querySelectorAll('.casino-seat-placard')].every(panel=>!overlap(board,panel.getBoundingClientRect()));
            const viewport=document.querySelector('[data-scene-viewport]').getBoundingClientRect(),gary=document.querySelector('[data-actor="gary"]').getBoundingClientRect();
            return{overlaps,faceClashes,panelsClear,visible:b.width>0,inside:b.left>=viewport.left-.5&&b.right<=viewport.right+.5&&b.top>=viewport.top-.5&&b.bottom<=viewport.bottom+.5,allTextFits:bubble.scrollHeight<=bubble.clientHeight+1,centered:Math.abs((gary.left+gary.right)/2-(viewport.left+viewport.right)/2)<.5};
          });
          assert.deepEqual(report,{overlaps:[],faceClashes:[],panelsClear:true,visible:true,inside:true,allTextFits:true,centered:true},'speaker '+speaker+' at '+viewport.width);
        }
        await page.setViewportSize({width:1205,height:714});await page.clock.runFor(100);
        assert.match(await page.locator('[data-player-speech] strong').textContent(),/SpongeBob|Squidward|Plankton|Mr. Krabs/);
      }
      if(seen.size===4)break;
      await advanceCue();
    }}finally{await page.clock.resume();}
    assert.equal(seen.size,4);assert.equal(await page.locator('[data-dealer-line]').isVisible(),false);
    assert.doesNotMatch(await page.locator('[data-dealer]').textContent(),/silent dealer|watching|preparing/i);
    assert.equal(await page.locator('.casino-oval-table>span').count(),0);
    assert.ok(await page.locator('[data-seat-action]').count()>0);
    await page.evaluate(()=>room.stopAnimation());await page.clock.runFor(200);
  });
  await t.test('poker keeps future dialogue, ranks and payout poses out of the staged scene',async()=>{
    await reset('poker',{showdown:true});await start('poker');await drain();
    for(const viewport of views)await capture('poker-player-turn',viewport);
    await page.setViewportSize(desktop);
    await page.click('[data-move="call"]');await page.clock.runFor(50);
    const human=page.locator('[data-player-speech]');
    assert.match(await human.innerText(),/You \(Patrick\) · Calls 2/);
    assert.doesNotMatch(await human.innerText(),/raise/i);
    await capture('poker-human-call',views[3]);await page.setViewportSize(desktop);
    await drain();
    assert.equal(await page.locator('[data-seat="4"] [data-actor]').getAttribute('data-pose'),'hold');
    assert.equal(await page.locator('.casino-poker-board .casino-card:not(.casino-card-back)').count(),3);
    await capture('poker-flop',views[2]);await page.setViewportSize(desktop);
    await page.locator('[data-raise]').fill('98');await page.click('[data-move="raise"]');await page.clock.runFor(50);
    assert.equal(await page.locator('[data-action-dock] [data-move],[data-table-form]:visible').count(),0);
    await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:9900,connected:true,
      casino:{profile_id:'fixture',round:current}}));
    assert.doesNotMatch(await page.locator('#room').innerText(),/Straight|One pair|Wins 294|Uncalled|monies returned/i);
    for(let i=0;i<16&&!/^SpongeBob turns over/.test(await caption());i++)await advanceCue();
    assert.equal(await caption(),'SpongeBob turns over their hand.');
    assert.equal(await page.locator('[data-card-row="seat-1"] .casino-card-back').count(),2);
    assert.doesNotMatch(await page.locator('#room').innerText(),/Straight|Wins 294|Uncalled/i);
    await advanceCue();
    assert.equal(await page.locator('[data-card-row="seat-1"] .casino-card:not(.casino-card-back)').count(),2);
    assert.equal(await page.locator('[data-card-row="seat-2"] .casino-card-back').count(),2);
    assert.match(await human.innerText(),/SpongeBob · Straight/);
    await capture('poker-showdown-reveal');
    for(let i=0;i<8&&!/SpongeBob · Wins/.test(await caption());i++)await advanceCue();
    assert.equal(await caption(),'SpongeBob · Wins 294 monies');
    assert.equal(await page.locator('[data-actor][data-pose="react"]').count(),0);
    await page.clock.runFor(500);
    assert.deepEqual(await page.locator('[data-actor][data-pose="react"]').evaluateAll(nodes=>nodes.map(node=>node.dataset.actor)),['spongebob']);
    await capture('poker-winner');
    await advanceCue();
    assert.equal(await caption(),'Squidward · Uncalled bet returned');
    assert.equal(await page.locator('.casino-poker-board [data-pot] b').textContent(),'4');
    assert.match(await page.locator('[data-seat="1"] h3').textContent(),/298 chips/);
    assert.match(await page.locator('[data-seat="2"] h3').textContent(),/0 chips/);
    await page.clock.runFor(500);
    assert.equal(await page.locator('[data-actor][data-pose="react"]').count(),0);
    await capture('poker-uncalled-return',views[3]);
    await drain();
    assert.equal(await page.locator('[data-table-status]').textContent(),'0 monies returned');
    assert.equal(await page.locator('[data-table-balance]').textContent(),'9,900');
    assert.equal(await page.evaluate(()=>requests.length),3);
    for(const viewport of views)await capture('poker-settled',viewport);
    await page.setViewportSize(desktop);
  });
  await t.test('SpongeBob and Patrick props clear name panels in every pose',async()=>{
    await reset('poker');
    for(const width of [1205,980,390]){
      await page.setViewportSize({width,height:714});await page.clock.runFor(100);await page.waitForTimeout(80);
      for(const pose of ['hold','place','chips','react']){
        const collisions=await page.evaluate(pose=>{
          const clashes=[];
          for(const name of ['spongebob','patrick']){
            const actor=document.querySelector('[data-actor="'+name+'"]');actor.dataset.pose=pose;
            const placard=actor.parentElement.querySelector('.casino-seat-placard').getBoundingClientRect();
            for(const prop of [actor,...actor.querySelectorAll('.casino-sponge-grip,.casino-actor-art')]){
              prop.style.transition='none';prop.style.animation='none';
              const b=prop.getBoundingClientRect();
              if(b.left<placard.right&&b.right>placard.left&&b.bottom>placard.top+.1&&b.top<placard.bottom)clashes.push(name+':'+prop.className);
            }
          }
          return clashes;
        },pose);
        assert.deepEqual(collisions,[],pose+' at '+width+' should leave space below actors and moving cards');
      }
    }
    await page.setViewportSize({width:1205,height:714});await page.clock.runFor(100);
  });
  await t.test('landed hole cards persist and turn face up in place; visible seats are clockwise',async()=>{
    await reset('poker');
    for(const width of [1205,390]){
      await page.setViewportSize({width,height:714});await page.clock.runFor(100);
      const turn=await page.evaluate(()=>{
        const table=document.querySelector('.casino-oval-table').getBoundingClientRect(),cx=table.x+table.width/2,cy=table.y+table.height/2;
        const angles=[0,1,2,3,4,0].map(seat=>{const b=document.querySelector('[data-seat="'+seat+'"] .casino-cards').getBoundingClientRect();return Math.atan2(b.y+b.height/2-cy,b.x+b.width/2-cx)*180/Math.PI;});
        return angles.slice(1).reduce((sum,angle,i)=>sum+(angle-angles[i]+360)%360,0);
      });
      assert.ok(Math.abs(turn-360)<.01,'clockwise visible seat loop at '+width);
    }
    await page.setViewportSize({width:1205,height:714});await page.clock.runFor(100);
    await start('poker');
    const nextAction=advanceCue;
    for(let n=0;n<12;n++)await nextAction(); // two blinds, then ten quick card deliveries
    assert.equal(await caption(),'Your cards · turn them over.');
    assert.equal(await page.locator('.casino-poker-seat .casino-card-back').count(),10);
    assert.ok(await page.locator('.casino-poker-seat .casino-card-back').evaluateAll(nodes=>nodes.every(node=>getComputedStyle(node).opacity==='1'&&getComputedStyle(node).animationName==='none')),'landed cards remain fully visible at an automatic action boundary');
    await page.evaluate(()=>{window.landedCards=[...document.querySelectorAll('[data-card-row="seat-0"] [data-card-slot]')];window.landedRects=landedCards.map(n=>{const r=n.getBoundingClientRect();return{x:r.x,y:r.y,width:r.width,height:r.height};});});
    const qa=path.join(root,'.chuddy','casino-qa');fs.mkdirSync(qa,{recursive:true});
    await page.screenshot({path:path.join(qa,'poker-dealt-face-down.png')});
    await nextAction();
    assert.equal(await page.locator('[data-card-row="seat-0"] .casino-card:not(.casino-card-back)').count(),2);
    assert.equal(await page.locator('.casino-poker-bots .casino-card-back').count(),8);
    const retained=await page.evaluate(()=>landedCards.map((node,index)=>{const now=document.querySelector('[data-card-row="seat-0"] [data-card-slot="'+index+'"]'),r=now.getBoundingClientRect();return{same:node===now,rect:{x:r.x,y:r.y,width:r.width,height:r.height}};}));
    assert.ok(retained.every(row=>row.same),'face-down nodes survive the flip and next action');
    assert.deepEqual(retained.map(row=>row.rect),await page.evaluate(()=>landedRects));
    await page.screenshot({path:path.join(qa,'poker-cards-flipped-in-place.png')});
    await page.evaluate(()=>room.stopAnimation());await page.clock.runFor(500);
  });
  await t.test('identical reduced-motion polls preserve raise input focus and value',async()=>{
    await reset('poker',{reducedMotion:true});await start('poker');
    const report=await page.evaluate(()=>{
      const input=document.querySelector('[data-raise]');input.focus();input.value='17';
      for(let n=0;n<3;n++)room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10000,connected:true,reducedMotion:true,
        casino:{profile_id:'fixture',round:JSON.parse(JSON.stringify(current))}});
      return{same:input===document.querySelector('[data-raise]'),connected:input.isConnected,value:input.value,focused:document.activeElement===input};
    });
    assert.deepEqual(report,{same:true,connected:true,value:'17',focused:true});
  });
  await t.test('old completed or empty polls cannot replace a newly accepted table',async()=>{
    await reset('poker',{reducedMotion:true});
    await page.evaluate(()=>{
      window.oldTable={id:'older-fixture-round',game:'roulette',revision:0,complete:true,payout:20,
        view:{game:'roulette',stake:10,complete:true,number:7,color:'red',selection:{kind:'red'},payout:20,won:true}};
      room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10000,connected:true,reducedMotion:true,
        casino:{profile_id:'fixture',round:oldTable}});
    });
    await start('poker');
    await page.evaluate(()=>{
      const base={profile:{id:'fixture',name:'Playback fixture'},connected:true,reducedMotion:true};
      room.update({...base,balance:10010,casino:{profile_id:'fixture',round:current}});
      room.update({...base,balance:9000,casino:{profile_id:'fixture',round:oldTable}});
      room.update({...base,balance:8000,casino:{profile_id:'fixture',round:null}});
    });
    assert.equal(await page.locator('[data-move="fold"]').isEnabled(),true);
    assert.equal(await page.locator('[data-table-balance]').textContent(),'10,010');
    assert.equal(await page.evaluate(()=>requests.length),1);
  });
  await t.test('profile changes and saved reduced motion invalidate automatic playback',async()=>{
    await reset('poker');await start('poker');
    await page.evaluate(()=>room.update({profile:{id:'second',name:'Second'},balance:100,connected:true}));await page.clock.runFor(10000);
    assert.equal(await page.locator('.casino-flying-card,.casino-flying-chips').count(),0);
    assert.equal(await page.locator('#room').getAttribute('data-playback-active'),'false');
    await reset('blackjack');await start('blackjack');
    await page.evaluate(()=>room.update({profile:{id:'fixture',name:'Playback fixture'},balance:10000,connected:true,reducedMotion:true,casino:{profile_id:'fixture',round:current}}));
    await page.clock.runFor(1000);
    assert.equal(await page.locator('.casino-flying-card').count(),0);
    assert.equal(await page.locator('[data-move="hit"]').isDisabled(),false);
    await page.evaluate(()=>room.destroy());await page.clock.runFor(10000);
    assert.equal(await page.locator('#room>*').count(),0);
  });
  assert.deepEqual(errors,[]);
});
