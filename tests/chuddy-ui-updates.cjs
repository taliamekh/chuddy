"use strict";
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('updated shell supports cheats, period ranks, calendar setup and direct casino routing',async t=>{
  const profile={id:'one',name:'Friend'};
  const standings=fart=>[{id:'archive',name:'Archive',total:99,burp:99,fart:0},
    {...profile,total:fart+6,burp:6,fart}];
  const state={settings:{rules:[],distraction_destination:'casino'},profile,profiles:[profile],
    scores:{fart:12,burp:6,total:18,recent:[{kind:'burp',source:'archived',time:'2026-11-06T14:00:00Z'}],
      recent_farts:[{kind:'fart',source:'manual',time:'2026-11-05T14:00:00Z'}]},
    balance:100,messages:[],deadlines:[],memory:[],logs:[],
    hardware:{mode:'simulator'},agent:{status:'idle'},audio:{recording:false},voice:{speaking:false},
    session:'running',session_remaining:null,leaderboard:standings(12),
    leaderboard_periods:{today:standings(2),week:standings(7),all_time:standings(12),timezone:'America/Toronto'},
    friends:{status:'connected',leaderboard:standings(12),leaderboard_periods:{today:standings(2),week:standings(7)}},
    deadline_sync:{sources:[{provider:'google',connected:false},{provider:'brightspace',connected:false}]},
    catalog:[],desktop_overlay:{status:'unavailable',hotkey:false},
    desktop_interception:{pending_restorations:0,covered:false},casino:{profile_id:'one',round:null,balance:100}};
  const actions=[];let failCheat=true;let firstState=true;
  const send=(res,data,status=200)=>{res.writeHead(status,{'Content-Type':'application/json'});res.end(JSON.stringify(data));};
  const server=http.createServer(async(req,res)=>{
    if(req.url==='/api/state'){if(firstState){firstState=false;await new Promise(resolve=>setTimeout(resolve,750));}return send(res,state);}
    if(req.url==='/api/devices')return send(res,[]);
    if(req.url==='/api/action'){
      let raw='';for await(const chunk of req)raw+=chunk;
      const p=JSON.parse(raw);actions.push(p);
      if(p.action==='session'){state.session=p.operation==='pause'?'paused':['resume','start'].includes(p.operation)?'running':'stopped';return send(res,{session:state.session});}
      if(p.action==='admin_cheat'){
        if(failCheat){failCheat=false;return send(res,{error:'Temporary test failure'},503);}
        state.balance=1100;return send(res,{amount:1000,balance:1100});
      }
      if(p.action==='deadline_connect')state.deadline_sync.sources=[{provider:p.provider,connected:true,status:'connected',count:3,last_sync:'2026-11-06T14:00:00Z'}];
      return send(res,{});
    }
    const relative=req.url==='/'?'web/index.html':decodeURIComponent(req.url.slice(1)).split('?')[0];
    const file=path.resolve(root,relative);
    if(!relative.startsWith('web/')||!file.startsWith(root+path.sep)||!fs.existsSync(file)){res.writeHead(404);return res.end();}
    res.writeHead(200,{'Content-Type':{'.html':'text/html','.js':'application/javascript','.css':'text/css','.png':'image/png','.svg':'image/svg+xml'}[path.extname(file)]||'application/octet-stream'});
    res.end(fs.readFileSync(file));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  const browser=await chromium.launch({channel:'msedge',headless:true});
  t.after(async()=>{await browser.close();await new Promise(resolve=>server.close(resolve));});
  const page=await browser.newPage({viewport:{width:1320,height:900}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/`);
  assert.equal(await page.getByRole('navigation',{name:'Main navigation'}).getByRole('button',{name:'Leaderboard',exact:true}).count(),0);
  await page.locator('.profile-button').click();
  await page.locator('[data-leaderboard-period="today"]').click();
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent.includes('Chudding active'));
  assert.equal(await page.getByLabel('Current profile').inputValue(),'one');
  assert.equal(await page.getByRole('button',{name:'Add profile'}).isVisible(),true);
  assert.match(await page.locator('#score-events').innerText(),/fart\s+manual/i);
  assert.doesNotMatch(await page.locator('#page-leaderboard').innerText(),/burp|archived/i);
  assert.equal(await page.locator('#leaderboard-list .ranking-name').first().textContent(),'Friend');
  assert.equal(await page.locator('#friends-ranking .ranking-name').first().textContent(),'Friend');
  assert.match(await page.locator('#leaderboard-list .ranking-score').first().innerText(),/^2\s+FARTS$/);
  assert.equal(await page.locator('[data-sound="burp"],[data-score="burp"]').count(),0);
  if (process.env.CHUDDY_AUDIO_SCREENSHOTS) {
    fs.mkdirSync(process.env.CHUDDY_AUDIO_SCREENSHOTS,{recursive:true});
    await page.screenshot({path:path.join(process.env.CHUDDY_AUDIO_SCREENSHOTS,'fart-leaderboard.png')});
  }
  assert.equal(await page.locator('#session-stop,#interruption-overlay,#page-arcade,#deposit-form').count(),0);
  assert.equal(await page.locator('#session-start').isVisible(),false);
  assert.equal(await page.locator('#session-state').textContent(),'Chudding active');
  assert.equal(await page.locator('#session-pause').count(),0);
  state.session='stopped';
  await page.locator('#session-start').waitFor({state:'visible'});
  const startRGB=await page.locator('#session-start').evaluate(node=>getComputedStyle(node).backgroundColor.match(/\d+/g).map(Number));
  assert.ok(startRGB[1]>startRGB[0] && startRGB[1]>startRGB[2],'Start chudding must render green, including hover.');
  await page.locator('#session-start').click();
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent==='Chudding active');
  await page.keyboard.press('Control+Shift+KeyS');
  await page.locator('#admin-console').waitFor({state:'visible'});
  assert.equal(await page.locator('#admin-command').evaluate(input=>document.activeElement===input),true);
  await page.keyboard.down('Control');
  await page.keyboard.down('Shift');
  await page.keyboard.down('KeyS');
  await page.locator('#admin-console').waitFor({state:'hidden'});
  await page.keyboard.down('KeyS');
  assert.equal(await page.locator('#admin-console').isVisible(),false,'Holding the shortcut must not reopen the panel.');
  await page.keyboard.up('KeyS');
  await page.keyboard.up('Shift');
  await page.keyboard.up('Control');
  await page.keyboard.press('Control+Shift+KeyS');
  await page.locator('#admin-command').fill('stop');
  await page.locator('#admin-command').press('Enter');
  await page.getByRole('button',{name:/^Resume session/}).waitFor();
  await page.locator('#admin-result').getByText('Chuddy paused.',{exact:true}).waitFor();
  await page.locator('#admin-command').fill('stop');
  await page.locator('#admin-command').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#admin-command').value==='');
  assert.deepEqual(actions.filter(a=>a.action==='session').map(a=>a.operation),['start','pause','pause']);
  assert.equal(await page.locator('#session-state').textContent(),'Session paused');
  await page.keyboard.press('Control+s');
  assert.equal(await page.locator('#admin-console').isVisible(),false);
  await page.getByRole('button',{name:/^Resume session/}).click();
  await page.waitForFunction(()=>document.querySelector('#session-state').textContent==='Chudding active');
  await page.keyboard.press('Control+s');
  await page.locator('#admin-command').fill('monies -4');await page.locator('#admin-command').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#admin-result').textContent.includes('positive whole'));
  assert.equal(actions.filter(a=>a.action==='admin_cheat').length,0);
  await page.locator('#admin-command').fill('monies 1000');await page.locator('#admin-command').press('Enter');
  await page.getByText('Temporary test failure',{exact:true}).waitFor();
  await page.locator('#admin-command').press('Enter');
  await page.waitForFunction(()=>document.querySelector('#admin-result').textContent.includes('1,000 monies added'));
  const cheats=actions.filter(a=>a.action==='admin_cheat');
  assert.equal(cheats.length,2);assert.equal(cheats[0].request_id,cheats[1].request_id);assert.equal(cheats[0].profile_id,'one');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#admin-console').isVisible(),false);
  await page.locator('.nav-item[data-page="companion"]').click();
  assert.equal(await page.locator('#page-companion .summary-card').count(),1);
  assert.equal(await page.locator('[data-page="leaderboard"]:not(.profile-button)').count(),0);
  await page.locator('#page-companion .summary-card[data-page="deadlines"]').click();
  await page.locator('#page-deadlines').waitFor({state:'visible'});
  await page.locator('.profile-button').click();
  await page.locator('[data-leaderboard-period="today"]').click();assert.equal(await page.locator('#score-total').textContent(),'2');
  await page.locator('[data-leaderboard-period="week"]').click();assert.equal(await page.locator('#score-total').textContent(),'7');
  await page.locator('[data-leaderboard-period="all_time"]').click();assert.equal(await page.locator('#score-total').textContent(),'12');
  await page.locator('.nav-item[data-page="deadlines"]').click();
  assert.equal(await page.locator('#deadline-form,#deadline-file').count(),0);
  const feed='https://calendar.google.com/calendar/ical/private-test/basic.ics';
  await page.locator('[data-provider="google"] input').fill(feed);
  await page.locator('[data-provider="google"] button[type="submit"]').click();
  await page.waitForFunction(()=>document.querySelector('#calendar-google-status').textContent.includes('3 deadlines'));
  assert.equal(await page.locator('[data-provider="google"] input').inputValue(),'');
  assert.equal(actions.find(a=>a.action==='deadline_connect').url,feed);
  await page.clock.setFixedTime(new Date('2026-11-03T03:00:00Z'));
  state.deadlines=[{id:1,title:'Tokyo report [all day]',due:'2026-11-02T15:00:00Z',calendar_date:'2026-11-03',calendar_timezone:'Asia/Tokyo'}];
  await page.waitForFunction(()=>document.querySelector('.deadline-date b')?.textContent==='3');
  assert.match(await page.locator('.deadline-info small').textContent(),/Nov 3, 2026.*all day/);
  assert.equal(await page.locator('.deadline-item.overdue').count(),0);
  await page.clock.setFixedTime(new Date('2026-11-03T16:00:00Z'));
  await page.locator('.deadline-item.overdue').waitFor();
  state.interruption={id:'first',phase:'approaching',destination:'casino'};
  await page.waitForTimeout(700);
  assert.equal(await page.locator('#page-deadlines').isVisible(),true);
  state.interruption.phase='opened';
  state.casino_interruption_active=true;
  await page.waitForFunction(()=>location.hash==='#casino');
  assert.equal(await page.locator('.workspace > .topbar').isVisible(),false);
  const casinoBounds=await page.locator('#casino-room').boundingBox();
  assert.equal(casinoBounds.y,0);
  assert.equal(casinoBounds.height,900);
  assert.equal(await page.locator('#interruption-overlay,.casino-playback,[data-pause],[data-next-action],[data-speed]').count(),0);
  assert.equal(await page.locator('[data-casino-nav="admin"]').count(),0);
  const back=page.getByRole('button',{name:/^Back to Chuddy/});
  assert.equal(await back.isVisible(),true);
  assert.equal(await page.getByRole('button',{name:/Admin commands/i}).count(),0);
  assert.doesNotMatch(await page.locator('#page-casino').innerText(),/Ctrl\+S|admin commands|monies \d/i);
  await page.keyboard.press('Escape');
  assert.equal(actions.some(a=>a.action==='dismiss'),false);
  await page.keyboard.press('Control+s');
  assert.equal(await page.locator('#admin-console').isVisible(),true);
  await page.keyboard.press('Escape');
  await back.click();
  assert.equal(await page.locator('#session-state').textContent(),'Chudding active');
  await page.locator('.nav-item[data-page="casino"]').click();
  await page.keyboard.press('Control+s');
  await page.locator('#admin-command').fill('stop');
  await page.locator('#admin-command').press('Enter');
  await page.locator('#admin-result').getByText('Chuddy paused.',{exact:true}).waitFor();
  await back.click();
  await page.getByRole('button',{name:/^Resume session/}).waitFor();
  await page.keyboard.press('Escape');
  for(const selector of ['#breadcrumb','.character-card .card-top > .pill','.character-caption','.character-footer','#voice-state'])
    assert.equal(await page.locator(selector).isVisible(),false,selector+' is removed from the companion view');
  assert.equal(await page.locator('#page-companion .trace-card,.app-footer').count(),0);
  assert.equal(await page.locator('#chat-form button:last-child').getAttribute('id'),'ptt-toggle');
  await page.screenshot({path:path.join(root,'test-results/chuddy-interface-cleanup.png')});
  assert.deepEqual(errors,[]);

  await t.test('Patrick sidebar mark preserves Home and adjacent navigation at regular and compact widths',async()=>{
    const screenshotDir=path.join(root,'test-results','sidebar-only');
    await fs.promises.mkdir(screenshotDir,{recursive:true});
    const layouts=[
      {width:1440,wordmark:true,screenshot:'sidebar-desktop.png'},
      {width:821,wordmark:true},
      {width:820,wordmark:false},
      {width:390,wordmark:false,screenshot:'sidebar-compact.png'},
    ];
    for(const {width,wordmark,screenshot} of layouts){
      const shell=await browser.newPage({viewport:{width,height:900},deviceScaleFactor:1});
      try{
        const pageErrors=[];
        shell.on('pageerror',error=>pageErrors.push(error.message));
        await shell.goto(`http://127.0.0.1:${server.address().port}/#rules`);
        const brand=shell.getByRole('link',{name:'Chuddy home',exact:true});
        const mark=brand.locator('img.brand-mark');
        const word=brand.locator('.brand-wordmark');
        const nav=shell.getByRole('navigation',{name:'Main navigation'});
        const companion=nav.getByRole('button',{name:'Companion',exact:true});
        const rules=nav.getByRole('button',{name:'Work detection',exact:true});
        assert.equal(await brand.getAttribute('href'),'#companion');
        assert.equal(await mark.getAttribute('alt'),'');
        await mark.evaluate(img=>img.decode());
        const markUrl=await mark.evaluate(img=>img.currentSrc);
        const faviconUrls=await shell.locator('link[rel~="icon"]').evaluateAll(links=>links.map(link=>link.href));
        assert.ok(!faviconUrls.includes(markUrl),'The sidebar mark is confined to the in-app Home link');
        assert.equal(await word.textContent(),'chuddy');
        assert.equal(await word.isVisible(),wordmark);
        const [iconBox,sidebarBox,navBox]=await Promise.all([
          mark.boundingBox(),shell.locator('.sidebar').boundingBox(),nav.boundingBox(),
        ]);
        assert.ok(iconBox.width>=30&&iconBox.height>=34,`Readable mark at ${width}px`);
        assert.ok(iconBox.x>=sidebarBox.x&&iconBox.x+iconBox.width<=sidebarBox.x+sidebarBox.width);
        assert.ok(iconBox.y+iconBox.height<navBox.y,`Room above navigation at ${width}px`);
        if(wordmark){
          const wordBox=await word.boundingBox();
          assert.ok(wordBox.x>=sidebarBox.x&&wordBox.x+wordBox.width<iconBox.x,`Mark follows the wordmark at ${width}px`);
        }else{
          const iconCenter=iconBox.x+iconBox.width/2;
          const sidebarCenter=sidebarBox.x+sidebarBox.width/2;
          assert.ok(Math.abs(iconCenter-sidebarCenter)<=1,`Centered compact mark at ${width}px`);
        }

        await shell.keyboard.press('Tab');
        assert.equal(await brand.evaluate(link=>link.matches(':focus-visible')),true);
        await shell.keyboard.press('Enter');
        await shell.waitForURL('**/#companion');
        await shell.locator('#page-companion').waitFor();
        await shell.keyboard.press('Tab');
        assert.equal(await companion.evaluate(button=>button.matches(':focus-visible')),true);
        await rules.click();
        await shell.locator('#page-rules').waitFor();
        await mark.click();
        await shell.waitForURL('**/#companion');
        await shell.locator('#page-companion').waitFor();
        if(screenshot){
          await shell.mouse.move(Math.min(width,420)-1,384);
          await shell.screenshot({
            path:path.join(screenshotDir,screenshot),
            clip:{x:0,y:0,width:Math.min(width,420),height:385},
          });
        }
        assert.deepEqual(pageErrors,[]);
      }finally{
        await shell.close();
      }
    }
  });
});
