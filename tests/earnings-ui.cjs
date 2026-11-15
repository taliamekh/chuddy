'use strict';
// Headless fixture only: no live controller, audio, model or desktop actions.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('casino wallet and Companion session bar show current server earnings',async t=>{
  const earnings={balance:100,total_earned:0,level:1,multiplier:1,next_level_at:10,rewarded_events:0,
    daily_earned:0,daily_cap:500,rates:{burp:3,fart:5},passive:{status:'stopped',rate_per_second:0,multiplier:1,boost_remaining_ms:0}};
  const state={settings:{rules:[]},profile:{id:'one',name:'Clam Friend'},profiles:[{id:'one',name:'Clam Friend'}],
    scores:{burp:0,fart:0,total:0,recent:[]},balance:100,earnings,messages:[],deadlines:[],memory:[],logs:[],
    hardware:{mode:'simulator'},agent:{status:'idle'},audio:{recording:false},voice:{speaking:false},
    session:'stopped',leaderboard:[],real_time:'2026-11-04T08:00:00Z',catalog:[]};
  const actions=[];
  let failedFinish=false;
  let offline=false;
  const reply=(res,status,data)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/api/state')return reply(res,offline ? 503 : 200,state);
    if(req.url==='/api/action'){
      let raw='';for await(const chunk of req)raw+=chunk;
      const p=JSON.parse(raw);actions.push(p);
      if(p.action==='session' && ['start','resume'].includes(p.operation)){
        state.session='running';
        earnings.passive={status:'earning',rate_per_second:1,multiplier:1,boost_remaining_ms:0};
        return reply(res,200,{session:state.session});
      }
      if(p.action==='clam_game_start')return reply(res,200,{round_id:'round-1',profile_id:'one',status:'active'});
      if(p.action==='clam_game_finish'){
        if(!failedFinish){failedFinish=true;return reply(res,500,{error:'Simulated lost reply'});}
        state.balance=110;earnings.balance=110;earnings.total_earned=10;earnings.daily_earned=10;
        return reply(res,200,{reward:10,balance:110});
      }
      return reply(res,200,{});
    }
    const name=req.url==='/'?'web/index.html':decodeURIComponent(req.url.slice(1)).split('?')[0];
    const file=path.resolve(root,name);
    if(!file.startsWith(root+path.sep)||!name.startsWith('web/')||!fs.existsSync(file)||!fs.statSync(file).isFile()){
      res.writeHead(404);return res.end();
    }
    const ext=path.extname(file);
    res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml'}[ext]||'application/octet-stream'});
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  t.after(async()=>{try{if(browser)await browser.close();}finally{await new Promise(resolve=>server.close(resolve));}});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1320,height:900},locale:'en-CA',reducedMotion:'reduce'});
  const errors=[];page.on('pageerror',error=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/#arcade`);
  await page.locator('#page-casino').waitFor({state:'visible'});
  assert.equal(await page.locator('.session-scoreboard').isVisible(),false);
  assert.equal(await page.locator('#page-arcade,#start-game,#deposit-form,[data-casino-nav="arcade"],[data-casino-nav="wallet"]').count(),0);
  assert.match(await page.locator('#monies-rate').textContent(),/Stopped · 0 monies\/s/);
  state.session='running';earnings.passive={status:'earning',rate_per_second:3,multiplier:3,boost_remaining_ms:59500};
  state.balance=113;state.casino={profile_id:'one',round:null,balance:113};
  await page.waitForFunction(()=>document.querySelector('[data-balance]').textContent==='113');
  await page.waitForFunction(()=>document.querySelector('#monies-rate').textContent==='3 monies/s · 3× · 60s');
  assert.equal(await page.locator('#monies-balance').textContent(),'113 monies');
  earnings.passive={status:'work_detected',rate_per_second:0,multiplier:3,boost_remaining_ms:59000};
  await page.waitForFunction(()=>document.querySelector('#monies-rate').textContent==='Work detected · 0 monies/s');
  state.session='paused';earnings.passive={status:'stopped',rate_per_second:0,multiplier:1,boost_remaining_ms:0};
  await page.waitForFunction(()=>document.querySelector('#monies-rate').textContent==='Paused · 0 monies/s');
  earnings.passive={status:'error',error:'Monies could not be saved. Retrying.'};
  await page.waitForFunction(()=>document.querySelector('#monies-rate').textContent.includes('could not be saved'));

  await page.goto(`http://127.0.0.1:${server.address().port}/#companion`);
  const scorebar=page.locator('.topbar .session-scoreboard');
  await scorebar.waitFor({state:'visible'});
  await page.waitForFunction(()=>document.querySelector('#session-farts').textContent==='0');
  assert.equal(await page.locator('#session-boost').textContent(),'—');
  state.session='running';
  state.scores={burp:7,fart:123456789,total:123456796,recent:[]};
  state.messages=[{id:1,role:'assistant',text:'I made a noise. Did you get my monies?',source:'offline script',time:'2026-11-15T13:45:00Z'}];
  earnings.passive={status:'earning',rate_per_second:5,multiplier:5,boost_remaining_ms:5398000};
  await page.waitForFunction(()=>document.querySelector('#session-boost').textContent==='5× · 1:29:58');
  assert.equal(await page.locator('#session-farts').textContent(),'123,456,789');
  assert.deepEqual(await scorebar.locator('dt').allTextContents(),['Farts','Monies boost']);
  assert.equal(await page.locator('.chat-card .session-scoreboard,#summary-total').count(),0);
  assert.equal(await page.locator('.chat-heading + #chat-messages').count(),1);
  earnings.passive.boost_remaining_ms-=1000;
  await page.waitForFunction(()=>document.querySelector('#session-boost').textContent==='5× · 1:29:57');
  const output = process.env.CHUDDY_AUDIO_SCREENSHOTS;
  if(output) fs.mkdirSync(output,{recursive:true});
  const sizes=[{width:1320,height:900},{width:390,height:844}];
  async function verifyHeader({width,height}, phase) {
    await page.setViewportSize({width,height});
    await page.locator('.chat-card').evaluate(card=>{
      const header=document.querySelector('.topbar');
      window.scrollBy({top:card.getBoundingClientRect().top - header.getBoundingClientRect().bottom - 12,behavior:'instant'});
    });
    const header=await page.locator('.topbar').boundingBox();
    const status=await page.locator('#session-state').boundingBox();
    const counter=await scorebar.boundingBox();
    const heading=await page.locator('.chat-heading').boundingBox();
    const start=await page.locator('#session-start').boundingBox();
    assert.ok(counter.x>=header.x && counter.x+counter.width<=header.x+header.width);
    assert.ok(counter.y>=header.y && counter.y+counter.height<=header.y+header.height);
    assert.ok(header.y+header.height<=heading.y);
    if(width===390) {
      assert.ok(status.y+status.height<=counter.y);
      if(start) assert.ok(start.y+start.height<=counter.y);
    } else {
      assert.ok(status.x+status.width<=counter.x);
      if(start) assert.ok(counter.x+counter.width<=start.x);
    }
    assert.equal(await scorebar.evaluate(node=>node.scrollWidth<=node.clientWidth
      && [...node.querySelectorAll('dd')].every(value=>value.scrollWidth<=value.clientWidth)),true);
    if(output) await page.screenshot({path:path.join(output,`session-scoreboard-${phase}-${width}.png`)});
  }
  for (const size of sizes) {
    await verifyHeader(size,'active');
    await page.locator('#chat-input').focus();
    await page.keyboard.type('Hi');
    assert.equal(await page.locator('#chat-input').inputValue(),'Hi');
    await page.locator('#chat-input').fill('');
  }
  const messages=state.messages;
  state.messages=Array.from({length:24},(_,index)=>({
    id:index+1,role:'assistant',source:'offline script',text:'Conversation message '+(index+1),
  }));
  const history=page.locator('#chat-messages');
  await page.waitForFunction(()=>document.querySelector('#chat-messages').scrollHeight>document.querySelector('#chat-messages').clientHeight);
  await history.evaluate(node=>node.scrollTo({top:0,behavior:'instant'}));
  assert.equal(await history.evaluate(node=>node.scrollTop),0);
  state.messages=messages;

  state.session='stopped';
  earnings.passive={status:'stopped',rate_per_second:0,multiplier:1,boost_remaining_ms:0};
  const start=page.getByRole('button',{name:/^Start chudding/});
  await start.waitFor();
  assert.equal(await page.locator('#session-boost').textContent(),'Inactive');
  assert.equal(await page.locator('#session-farts').textContent(),'123,456,789');
  for (const size of sizes) await verifyHeader(size,'stopped');

  offline=true;
  await page.waitForFunction(()=>document.querySelector('#session-farts').textContent==='—' && document.querySelector('#session-boost').textContent==='—');
  assert.equal(await start.isDisabled(),true);
  offline=false;
  await page.waitForFunction(()=>document.querySelector('#session-boost').textContent==='Inactive');
  await start.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent==='Chudding active');

  state.session='paused';
  earnings.passive={status:'stopped',rate_per_second:0,multiplier:1,boost_remaining_ms:0};
  const resume=page.getByRole('button',{name:/^Resume session/});
  await resume.waitFor();
  assert.equal(await page.locator('#session-boost').textContent(),'Inactive');
  await resume.focus();
  await page.keyboard.press('Enter');
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent==='Chudding active');
  assert.deepEqual(actions.filter(p=>p.action==='session').map(p=>p.operation),['start','resume']);
  assert.equal(actions.some(p=>p.action.startsWith('clam_game_')),false);
  assert.deepEqual(errors,[]);
});
