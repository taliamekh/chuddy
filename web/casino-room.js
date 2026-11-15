/* Self-contained Bikini Bottom room. No wallet arithmetic or random outcomes here. */
(function (root) {
  'use strict';
  const SYMBOLS = Object.freeze({
    clam: {icon:'🐚', label:'Clam'}, hat: {icon:'⚓', label:"Sailor's hat"},
    butt: {icon:'🩳', label:"Patrick's shorts"}, snack: {icon:'🍔', label:'Krabby snack'}
  });
  const number = value => Number.isSafeInteger(value) && value >= 0;
  const money = value => value.toLocaleString();
  const actionLabel=row=>({fold:'Folds',check:'Checks',call:'Calls '+money(row.amount||0),raise:'Raises to '+money(row.amount||0),'small blind':'Small blind '+money(row.amount||0),'big blind':'Big blind '+money(row.amount||0)}[row.action]||row.action);
  const validBet = value => /^\d+$/.test(String(value)) && Number.isSafeInteger(Number(value)) && Number(value) >= 1 && Number(value) <= 10000;
  const makeID = () => 'casino-' + root.crypto.randomUUID();
  function validResult(result, request) {
    return result && result.request_id === request.request_id && result.profile_id === request.profile_id &&
      result.bet === request.bet && number(result.balance) && number(result.payout) &&
      Array.isArray(result.symbols) && result.symbols.length === 3 && result.symbols.every(symbol => Object.hasOwn(SYMBOLS,symbol));
  }
  async function gameRequest(payload) {
    const response = await root.fetch('/api/action', {method:'POST', cache:'no-store',
      headers:{'Content-Type':'application/json','X-Chuddy-Token':root.CHUDDY_TOKEN || ''},
      body:JSON.stringify(payload)});
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('The local service reply was unreadable. Retry the same spin.'); }
    if (!response.ok || data.error) {
      const error = new Error(data.error || 'The local service could not finish the spin.');
      // Only a parsed 400 from the local action route is a definite rejection.
      error.definitive = response.status === 400;
      throw error;
    }
    return data;
  }
  const spinRequest = payload => gameRequest({action:'spin',...payload});
  const escape = value => String(value == null ? '' : value).replace(/\bclams\b/gi,'monies').replace(/[&<>"']/g,c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function cards(values,capacity=0,row='') {
    const visible=values||[];
    const faces=Array.from({length:capacity||visible.length},(_,index)=>{
      const value=visible[index];
      if(index>=visible.length)return '<span class="casino-card-slot" data-card-slot="'+index+'" data-card-value=""><i class="casino-card-placeholder" aria-hidden="true"></i></span>';
      let face;
      if (!value) face='<span class="casino-card casino-card-back" aria-label="Face down card">⚓</span>';
      else {
        if (!/^[2-9TJQKA][CDHS]$/.test(value)) return '';
        const suit = {C:'♣',D:'♦',H:'♥',S:'♠'}[value[1]], rank = value[0] === 'T' ? '10' : value[0];
        face='<span class="casino-card '+(/[DH]/.test(value[1]) ? 'casino-card-red' : '')+'" aria-label="'+escape(rank+' of '+({C:'clubs',D:'diamonds',H:'hearts',S:'spades'}[value[1]]))+'"><b>'+rank+'</b><i>'+suit+'</i></span>';
      }
      return capacity?'<span class="casino-card-slot" data-card-slot="'+index+'" data-card-value="'+(value||'back')+'">'+face+'</span>':face;
    });
    return '<div class="casino-cards"'+(row?' data-card-row="'+row+'"':'')+'>'+faces.join('')+'</div>';
  }
  function actor(name,initialPose='hold') {
    const frame = {'Mr. Krabs':'krabs',SpongeBob:'spongebob',Patrick:'patrick',Squidward:'squidward',Plankton:'plankton',Gary:'gary'}[name] || 'gary';
    return '<span class="casino-actor casino-actor-'+frame+'" data-actor="'+frame+'" data-pose="'+(initialPose==='react'?'react':'hold')+'" role="img" aria-label="'+escape(name)+' at the table"><i class="casino-actor-art casino-portrait-art" aria-hidden="true"></i>'+(frame==='spongebob'?'<canvas class="casino-sponge-canvas" width="240" height="240" aria-hidden="true"></canvas><span class="casino-sponge-grip" aria-hidden="true"><i></i><i></i></span>':'')+'<i class="casino-actor-hand-anchor" aria-hidden="true"></i></span>';
  }
  let spongeSource=null;
  function paintSponge(canvas) {
    if(!spongeSource){spongeSource=new root.Image();spongeSource.onload=()=>root.document.querySelectorAll('.casino-sponge-canvas').forEach(paintSponge);spongeSource.src='/web/assets/casino/table-portraits.png';}
    if(!spongeSource.complete||!spongeSource.naturalWidth||!canvas.isConnected)return;
    const ctx=canvas.getContext('2d'),size=canvas.width,sourceWidth=spongeSource.naturalWidth/2,sourceHeight=spongeSource.naturalHeight/2;
    const mode=canvas.parentElement.dataset.pose,cut=.76;
    const sample=(x,y,w,h)=>ctx.drawImage(spongeSource,sourceWidth+x*sourceWidth,y*sourceHeight,w*sourceWidth,h*sourceHeight,x*size,y*size,w*size,h*size);
    ctx.clearRect(0,0,size,size);
    // Runtime raster puppet: the original face is fixed; only source-arm pixels
    // below the shirt line pivot. No source image or character vectors are created.
    sample(0,0,1,cut);sample(.24,cut,.55,1-cut);
    for(const side of ['left','right']){
      const right=side==='right',jointX=right?.80:.24,jointY=.78;
      let degrees=mode==='place'?(right?48:-48):mode==='chips'?(right?62:0):mode==='react'?(right?-26:26):0;
      ctx.save();ctx.translate(jointX*size,jointY*size);ctx.rotate(degrees*Math.PI/180);ctx.translate(-jointX*size,-jointY*size);
      sample(right?.79:0,cut,right?.21:.24,1-cut);ctx.restore();
    }
    canvas.parentElement.dataset.rasterPainted='true';
  }
  function chips(value,label='Play chips') {
    if (!number(value) || value === 0) return '';
    return '<span class="casino-chip-stack" aria-label="'+escape(label)+' '+value+'"><i></i><i></i><i></i><b>'+money(value)+'</b></span>';
  }
  function createPlayback(host,options,isReduced) {
    // Keep cue metadata for staged result diagnostics without a visible toolbar.
    const caption=host.ownerDocument.createElement('span');caption.hidden=true;
    caption.setAttribute('data-playback-caption','');host.append(caption);
    const waits=new Set(),flights=new Set();let active=false;
    const ms=value=>options.animationMs===undefined?value:Math.max(1,options.animationMs);
    function refresh(){
      host.dataset.playbackActive=String(active);
      for(const flight of flights){if(active)flight.play();else flight.pause();}
    }
    function cancel(){
      active=false;
      for(const pending of [...waits])pending.finish(false);
      for(const flight of flights)flight.cancel();flights.clear();refresh();
    }
    function wait(value){
      if(!active||isReduced())return Promise.resolve(false);
      return new Promise(resolve=>{
        let remaining=Math.max(1,ms(value)),last=performance.now(),timer;
        const pending={finish(result){root.clearTimeout(timer);waits.delete(pending);resolve(result);}};waits.add(pending);
        const tick=()=>{const now=performance.now();remaining-=now-last;last=now;
          if(!active)return pending.finish(false);
          if(remaining<=0)return pending.finish(true);
          timer=root.setTimeout(tick,16);
        };timer=root.setTimeout(tick,16);
      });
    }
    return {
      ms,wait,
      begin(){cancel();active=true;refresh();},
      async cue(text,lead=450){caption.textContent=String(text).replace(/\bclams\b/gi,"monies");refresh();return wait(lead);},
      track(flight){flights.add(flight);refresh();return()=>flights.delete(flight);},
      end(){active=false;refresh();},
      cancel,
      destroy(){cancel();caption.remove();}
    };
  }
  function mountTables(host,options,emit,storage,isReduced,playback) {
    const request = options.gameRequest || gameRequest, memory = new Map();
    const names = {slots:'Clam-o-matic',blackjack:"Gary's Blackjack",poker:'Bikini Bottom Hold’em',roulette:'Reef Roulette'};
    const element = host.ownerDocument.createElement('div');
    element.className = 'casino-tables-extension';
    element.innerHTML = `<nav class="casino-game-picker" aria-label="Choose a casino game">${Object.entries(names).map(([game,name]) => '<button type="button" data-game="'+game+'" aria-pressed="'+(game==='slots')+'"><span>'+({slots:'🐚',blackjack:'♠',poker:'♥',roulette:'◉'}[game])+'</span>'+name+'</button>').join('')}</nav>
      <section class="casino-earned"><div><b data-earned-title>Monies · stopped</b><span data-earned-detail></span></div><button type="button" data-casino-nav="leaderboard">Leaderboard ↗</button></section>
      <section class="casino-table-room" data-table-room hidden>
        <div class="casino-table-heading"><div><span class="casino-kicker">GARY'S GOLDEN REEF TABLES</span><h2 data-table-title>Gary's Blackjack</h2></div><span class="casino-table-balance"><b data-table-balance>—</b> PLAY MONIES</span></div>
        <div class="casino-scene-viewport" data-scene-viewport><div class="casino-fit-canvas" data-scene-canvas>
          <div class="casino-dealer" data-dealer aria-label="Gary, dealer">${actor('Gary')}<p class="casino-dealer-speech" data-dealer-line hidden></p><span class="casino-shoe" data-dealer-deck aria-label="Card shoe">♠</span></div>
          <div class="casino-felt" data-table-view></div>
          <blockquote class="casino-player-speech" data-player-speech aria-live="polite" aria-atomic="true" hidden></blockquote>
        </div></div>
        <div class="casino-table-fx" data-table-fx aria-hidden="true"></div>
        <div class="casino-action-dock" data-action-dock></div>
        <div class="casino-table-status" data-table-status role="status" aria-live="polite" aria-atomic="true" hidden></div>
        <form data-table-form class="casino-table-start"><label><span data-table-stake-label>BASE STAKE</span><input data-table-stake type="number" min="2" max="10000" step="2" value="10" required></label><button data-table-start type="submit">Take a seat</button><button data-table-retry type="button" hidden>Retry the same request ↻</button><span data-table-error role="status" hidden></span></form>
      </section>
      <dialog class="casino-info-dialog" data-info-dialog aria-label="Casino rules, rewards and history"><header><h2>Clam Casino desk</h2><button type="button" data-close-info aria-label="Close casino information">× Close</button></header><div data-info-body><details class="casino-table-rules" open><summary>Table rules & play-money payouts</summary><p data-table-rules></p></details><section data-table-log-holder></section></div></dialog>`;
    host.querySelector('.casino-intro').after(element);
    const infoButton=host.ownerDocument.createElement('button');infoButton.type='button';infoButton.className='casino-back casino-info-button';infoButton.textContent='Rules / log';infoButton.setAttribute('data-open-info','');
    host.querySelector('.casino-topbar').append(infoButton);
    const get = selector => element.querySelector(selector);
    const stake = get('[data-table-stake]'), start = get('[data-table-start]');
    let profile = null, balance = null, connected = false, round = null, pending = null, busy = false, dead = false, version = 0, game = 'slots', signature = '', selected = {kind:'red'}, loaded = false;
    let animationEpoch=0, motionCancelled=false;
    const flights=new Set(),retiredRounds=new Set();
    let presentedRound=null,heldBalance=null;
    function fitScene(){
      if(dead)return;
      const viewport=get('[data-scene-viewport]'),canvas=get('[data-scene-canvas]');
      const box=viewport.getBoundingClientRect();if(!box.width||!box.height)return;
      const compact=box.width<660,poker=game==='poker';
      const width=poker?(compact?540:1120):(compact?520:1000);
      const height=poker?(compact?700:540):50+(game==='roulette'?(compact?690:455):460);
      const scale=Math.min(box.width/width,box.height/height,1.35);
      canvas.dataset.compact=String(compact);canvas.style.width=width+'px';canvas.style.height=height+'px';canvas.style.transform='translate(-50%,-50%) scale('+scale+')';
      placeSpeech(canvas,width,height,scale);
    }
    function placeSpeech(canvas,width,height,scale){
      const bubble=get('[data-player-speech]');if(bubble.hidden)return;
      const speaker=get('[data-seat="'+bubble.dataset.speaker+'"] [data-actor]');if(!speaker){bubble.hidden=true;return;}
      const origin=canvas.getBoundingClientRect();
      const rect=node=>{const b=node.getBoundingClientRect();return{left:(b.left-origin.left)/scale,top:(b.top-origin.top)/scale,right:(b.right-origin.left)/scale,bottom:(b.bottom-origin.top)/scale};};
      const compact=canvas.dataset.compact==='true';
      const anchor=rect(speaker),center=(anchor.left+anchor.right)/2;
      const facesRight=center<width/2;
      const gap=12,edge=7;
      let bw=compact?205:204;
      const preferred=compact?facesRight:!facesRight;
      const obstacles=[...canvas.querySelectorAll('[data-actor],.casino-seat-placard,.casino-poker-board,[data-pot],[data-muck],[data-dealer-deck]')]
        .filter(node=>node.checkVisibility()).map(rect).filter(box=>box.right>box.left&&box.bottom>box.top);
      const fits=(x,y,bh)=>x>=edge&&x+bw<=width-edge&&y>=edge&&y+bh<=height-edge&&
        !obstacles.some(o=>x<o.right+4&&x+bw>o.left-4&&y<o.bottom+4&&y+bh>o.top-4);
      bubble.style.width=bw+'px';
      bubble.style.maxHeight='';
      bubble.removeAttribute('tabindex');
      delete bubble.dataset.detached;
      const measured=rect(bubble);
      let bh=measured.bottom-measured.top;
      let chosen;
      for(const onRight of [preferred,!preferred]){
        const x=onRight?anchor.right+gap:anchor.left-gap-bw;
        // Keep the pointer near the face and move the bubble vertically only
        // as far as needed to clear the table, card shoe or another seat.
        const top=anchor.top+10;
        const offsets=[0,...Array.from({length:15},(_,i)=>[-(i+1)*8,(i+1)*8]).flat()];
        const y=offsets.map(offset=>top+offset).find(y=>fits(x,y,bh)&&y+bh>=anchor.top+8&&y<=anchor.bottom-12);
        if(y!==undefined){chosen={x,y,onRight};break;}
      }
      if(!chosen&&compact){
        // The compact scene leaves a full-width band between the board and
        // the lower seats, so a wrapped line needn't cover cards or faces.
        const y=rect(get('.casino-poker-board')).bottom+gap;
        const available=rect(get('[data-seat="1"] [data-actor]')).top-y-gap;
        bw=width-40;
        const x=(width-bw)/2;
        bubble.style.width=bw+'px';
        const wide=rect(bubble);
        bh=wide.bottom-wide.top;
        if(available>=36&&fits(x,y,Math.min(available,bh))){
          if(available<bh)bubble.style.maxHeight=available+'px';
          chosen={x,y,onRight:false};
          bubble.dataset.detached='true';
        }
      }
      if(!chosen){
        // Use the nearest clear rectangle. For exceptionally long saved
        // speech, make only the bubble scroll within that space.
        for(let h=Math.min(bh,height-2*edge);h>=36&&!chosen;h-=12){
          let best=Infinity;
          for(let x=edge;x<=width-bw-edge;x+=8)for(let y=edge;y<=height-h-edge;y+=8){
            const distance=Math.hypot(x+bw/2-center,y+h/2-anchor.top);
            if(distance<best&&fits(x,y,h)){best=distance;chosen={x,y,onRight:x>center};}
          }
          if(chosen&&h<bh)bubble.style.maxHeight=h+'px';
        }
        bubble.dataset.detached='true';
      }
      if(!chosen){bubble.hidden=true;return;}
      if(bubble.style.maxHeight)bubble.tabIndex=0;
      bubble.style.left=chosen.x+'px';bubble.style.top=chosen.y+'px';
      bubble.style.setProperty('--speech-tail',Math.min(bh-8,Math.max(8,anchor.top+24-chosen.y))+'px');
      bubble.dataset.side=chosen.onRight?'right':'left';
    }
    function speech(seat,text,label=''){
      const bubble=get('[data-player-speech]');bubble.replaceChildren();bubble.hidden=seat===null;
      if(seat===null)return;
      const title=host.ownerDocument.createElement('strong');title.textContent=label;bubble.append(title);
      if(text){const line=host.ownerDocument.createElement('span');line.textContent=text;bubble.append(line);}
      bubble.dataset.speaker=String(seat);fitScene();
    }
    const fitObserver=root.ResizeObserver?new root.ResizeObserver(fitScene):null;
    if(fitObserver)fitObserver.observe(get('[data-scene-viewport]'));
    function stopAnimation() {
      playback.cancel();
      speech(null,'');
      ++animationEpoch;
      element.classList.remove('casino-dealing');
      element.classList.remove('casino-wheel-landing');
      element.classList.remove('casino-sequence-running');
      for(const flight of flights)flight.cancel();flights.clear();
      get('[data-table-fx]').replaceChildren();
      presentedRound=null;signature='';
      heldBalance=null;
      element.querySelectorAll('[data-actor]').forEach(node=>{node.dataset.pose='hold';});
      element.querySelectorAll('.casino-poker-seat').forEach(seat=>seat.classList.remove('casino-bot-turn'));
    }
    function pose(name,value) {element.querySelectorAll('[data-actor="'+name+'"]').forEach(node=>{node.dataset.pose=value;const canvas=node.querySelector('canvas');if(canvas)paintSponge(canvas);});}
    const delay=ms=>playback.wait(ms);
    async function transfer(kind,from,to,value) {
      if(!from||!to||dead||motionCancelled||isReduced()||host.ownerDocument.hidden)return;
      const layer=get('[data-table-fx]'),bounds=layer.getBoundingClientRect(),a=from.getBoundingClientRect(),b=to.getBoundingClientRect();
      if(!a.width||!b.width)return;
      const prop=host.ownerDocument.createElement('span');prop.className='casino-flying-'+kind;
      prop.textContent=kind==='card'?'⚓':number(value)?money(value):'◉';layer.append(prop);
      const sx=a.left+a.width*.6-bounds.left,sy=a.top+a.height*.72-bounds.top;
      const tx=b.left+b.width*.5-bounds.left,ty=b.top+b.height*.5-bounds.top;
      const duration=playback.ms(kind==='card'?190:850,kind!=='card');
      if(kind==='card'&&to.hasAttribute('data-card-slot')){prop.style.width=b.width+'px';prop.style.height=b.height+'px';}
      const flight=prop.animate([{transform:'translate('+sx+'px,'+sy+'px) rotate(-12deg)',opacity:0},
        {transform:'translate('+((sx+tx)/2)+'px,'+(Math.min(sy,ty)-30)+'px) rotate(5deg)',opacity:1,offset:.5},
        {transform:'translate('+(to.hasAttribute('data-card-slot')?b.left-bounds.left:tx)+'px,'+(to.hasAttribute('data-card-slot')?b.top-bounds.top:ty)+'px) rotate(0deg)',opacity:1}],{duration,easing:'ease-in-out',fill:'both'});
      const untrack=playback.track(flight);
      flights.add(flight);try{await flight.finished;}catch(_){/* Stop/profile/destroy cancels visual work. */}finally{untrack();flights.delete(flight);prop.remove();}
    }
    async function animateTurns(before,submitted) {
      if(isReduced()||dead||!round||motionCancelled||host.ownerDocument.hidden)return;
      const epoch=animationEpoch,valid=()=>!dead&&epoch===animationEpoch&&!motionCancelled&&!isReduced();
      playback.begin();
      element.classList.add('casino-sequence-running');
      const dealer=()=>get('[data-dealer-deck]');
      const seat=index=>get('[data-seat="'+index+'"]');
      const seatActor=index=>get('[data-seat="'+index+'"] [data-actor]');
      const hand=index=>get('[data-seat="'+index+'"] .casino-cards')||seatActor(index);
      const getAllHands=()=>[...element.querySelectorAll('.casino-player-hand .casino-cards')];
      const pot=()=>get('[data-pot]')||get('[data-bet-mat]');
      const settleVisual=()=>{presentedRound=null;element.dataset.revealResult='true';signature='';render();};
      const repaint=()=>{signature='';render();};
      const cardSpot=(row,index)=>get('[data-card-row="'+row+'"] [data-card-slot="'+index+'"]');
      async function flip(row,index,value,commit){
        const slot=cardSpot(row,index);if(!slot||!valid()||slot.dataset.cardValue===value)return;
        async function turn(from,to){
          const animation=slot.animate([{transform:'rotateY('+from+'deg)'},{transform:'rotateY('+to+'deg)'}],{duration:playback.ms(95,false),easing:'ease-in-out',fill:'both'});
          const untrack=playback.track(animation);flights.add(animation);
          try{await animation.finished;}catch(_){/* Stop cancels flips with flights. */}
          finally{untrack();flights.delete(animation);animation.cancel();}
        }
        await turn(0,90);if(!valid())return;
        const face=host.ownerDocument.createElement('div');face.innerHTML=cards([value]);
        slot.replaceChildren(...face.firstElementChild.childNodes);slot.dataset.cardValue=value;commit();
        await turn(-90,0);
      }
      const publicHand=(hand,values)=>({...hand,cards:values,total:values.length===hand.cards.length?hand.total:'—',soft:values.length===hand.cards.length&&hand.soft,status:'active',outcome:null,payout:null});
      async function dealBoard(count){
        const first=presentedRound.view.community.length,last=Math.min(count,round.view.community.length);
        for(let index=first;index<last;index++){
          if(index===0||index===3||index===4){
            presentedRound.view.street=index===0?'flop':index===3?'turn':'river';
            presentedRound.view.players.forEach(player=>{player.street_bet=0;});repaint();
          }
          await playback.cue('Gary deals '+(index<3?'the flop ('+(index+1)+' of 3)':index===3?'the turn':'the river')+'.',0);if(!valid())return;
          pose('gary','place');await transfer('card',dealer(),cardSpot('community',index));if(!valid())return;
          presentedRound.view.community.push(null);repaint();await playback.wait(35,false);if(!valid())return;
        }
        for(let index=first;index<last;index++){await flip('community',index,round.view.community[index],()=>{presentedRound.view.community[index]=round.view.community[index];});if(!valid())return;}
        pose('gary','hold');
      }
      async function pokerAction(row){
        const boardLength={preflop:0,flop:3,turn:4,river:5}[row.street]||0;
        await dealBoard(boardLength);if(!valid())return;
        if(presentedRound.view.street!==row.street){
          presentedRound.view.street=row.street;
          presentedRound.view.players.forEach(player=>{player.street_bet=0;});repaint();
        }
        const target=seat(row.seat),who=seatActor(row.seat);if(!target||!who)return;
        const player=presentedRound.view.players[row.seat];
        const paid=row.chips?row.chips.street_bet-player.street_bet:row.action==='raise'?null:row.amount;
        const pushesChips=paid>0||(paid===null&&row.action==='raise');
        target.classList.add('casino-bot-turn');const key=who.dataset.actor;
        speech(row.seat,row.text,row.name+' · '+actionLabel(row));
        await playback.cue(row.name+' · '+row.action+(row.amount>0?' · '+money(row.amount)+' monies':'')+(row.text?' — '+row.text:''));if(!valid())return;
        if(row.action==='fold'){pose(key,'place');await transfer('card',who,get('[data-muck]'));}
        else if(pushesChips){pose(key,'chips');await transfer('chips',who,pot(),paid);}
        else {target.classList.add('casino-seat-check');await delay(900);}
        if(!valid())return;
        if(row.chips){Object.assign(player,row.chips);presentedRound.view.pot=row.chips.pot;delete player.pot;player.all_in=!player.folded&&player.stack===0;}
        if(row.action==='fold')player.folded=true;
        presentedRound.view.log.push(row);repaint();
        seat(row.seat).classList.add('casino-bot-turn');
        await delay(1800);if(!valid())return;
        speech(null,'');
        seat(row.seat).classList.remove('casino-bot-turn');
      }
      try {
        if(round.game==='roulette'){
          await playback.cue('Roulette · placing '+money(round.view.stake)+' monies on '+round.view.selection.kind+'.');if(!valid())return;
          pose('gary','chips');await transfer('chips',get('[data-bet-mat]'),pot(),round.view.stake);
          if(!valid())return;
          pose('gary','hold');
          await playback.cue('Roulette · watch the ball circle the wheel.');if(!valid())return;
          element.style.setProperty('--roulette-duration',playback.ms(6000)+'ms');
          element.dataset.revealResult='false';element.classList.add('casino-wheel-landing');await delay(6000);if(!valid())return;
          element.classList.remove('casino-wheel-landing');settleVisual();
          say(money(round.view.payout)+' monies returned');
          await playback.cue('Roulette · '+round.view.number+' '+round.view.color+'. '+money(round.view.payout)+' monies returned.');if(!valid())return;
          pose('gary',round.view.payout>0?'chips':'hold');await delay(2200);
          if(valid()&&round.view.payout>0)await transfer('chips',pot(),get('[data-bet-mat]'),round.view.payout);
          if(valid())pose('gary','hold');
          return;
        }
        const previousLength=before&&before.game==='poker'?(before.view.log||[]).length:0;
        const log=round.game==='poker'?(round.view.log||[]).slice(previousLength):[];
        if(submitted.action==='casino_start'&&round.game==='poker'){
          for(const row of log.filter(row=>row.action.includes('blind'))){await pokerAction(row);if(!valid())return;}
        }
        if(submitted.action==='casino_start'){
          pose('gary','place');
          // Logical seat order is arranged clockwise, starting left of the button.
          const targets=round.game==='poker'?[1,2,3,4,0]:[0,-1];
          for(let pass=0;pass<2&&valid();pass++){
            for(const index of targets){
              await playback.cue('Dealing card '+(pass+1)+' to '+(index===-1?'Gary':round.game==='poker'?round.view.players[index].name:'you (Patrick)')+'.',0);if(!valid())return;
              await transfer('card',dealer(),round.game==='poker'?cardSpot('seat-'+index,pass):index===-1?get('.casino-dealer-hand .casino-cards'):hand(index));
              if(!valid())return;
              if(presentedRound){
                if(round.game==='poker')presentedRound.view.players[index].cards=Array(pass+1).fill(null);
                else if(index===-1)presentedRound.view.dealer.cards=pass===0?[round.view.dealer.cards[0]]:[round.view.dealer.cards[0],null];
                else presentedRound.view.hands[0]=publicHand(round.view.hands[0],round.view.hands[0].cards.slice(0,pass+1));
                signature='';render();
              }
              await playback.wait(35,false);
            }
          }
          if(round.game==='poker'&&valid()){
            await playback.cue('Your cards · turn them over.',0);if(!valid())return;
            for(let index=0;index<2;index++){await flip('seat-0',index,round.view.players[0].cards[index],()=>{presentedRound.view.players[0].cards[index]=round.view.players[0].cards[index];});if(!valid())return;}
          }
          if(!valid())return;pose('gary','hold');
        }
        if(round.game==='blackjack'){
          await playback.cue(submitted.action==='casino_start'?'Blackjack · initial deal.':'Blackjack · you '+submitted.move+'.');if(!valid())return;
          if(submitted.move==='double'||submitted.move==='split'){pose('patrick','chips');await transfer('chips',seatActor(0),pot(),round.view.stake);}
          if(!valid())return;
          if(submitted.move==='split'){
            await playback.cue('You separate the pair into two hands.');if(!valid())return;
            pose('patrick','place');await transfer('card',hand(0),seatActor(0));if(!valid())return;
            presentedRound.view.hands=round.view.hands.map(item=>publicHand(item,item.cards.slice(0,1)));
            presentedRound.view.committed=round.view.committed;repaint();await delay(1000);
          }
          if(['hit','double','split'].includes(submitted.move)){
            const targets=submitted.move==='split'?[0,1]:[before.view.active_hand];
            for(const index of targets){
              await playback.cue('Gary deals a card to your hand '+(index+1)+'.');if(!valid())return;
              pose('gary','place');await transfer('card',dealer(),getAllHands()[index]);if(!valid())return;
              presentedRound.view.hands[index]=publicHand(round.view.hands[index],round.view.hands[index].cards.slice());
              presentedRound.view.committed=round.view.committed;repaint();await delay(1200);if(!valid())return;
            }
          }
          if(!valid())return;
          if(round.complete){
            presentedRound.view.active_hand=null;
            presentedRound.view.hands.forEach(item=>{item.status=item.total>21?'bust':'stood';});
            repaint();
            for(let index=1;index<round.view.dealer.cards.length;index++){
              await playback.cue(index===1?'Gary reveals the face-down card.':'Gary draws another card.');if(!valid())return;
              pose('gary','place');
              if(index>1){await transfer('card',dealer(),get('.casino-dealer-hand .casino-cards'));if(!valid())return;}
              presentedRound.view.dealer.cards=round.view.dealer.cards.slice(0,index+1);repaint();
              await delay(1200);if(!valid())return;
            }
          }
          if(!valid())return;
          settleVisual();pose('gary',round.complete&&round.view.payout>0?'chips':'hold');
          if(round.complete)say(money(round.view.payout)+' monies returned');
          if(round.complete&&round.view.payout>0){await playback.cue('Blackjack settled · '+money(round.view.payout)+' monies returned, including unused reserve.');if(!valid())return;await transfer('chips',pot(),seatActor(0),round.view.payout);}
          if(valid())pose('gary','hold');
          await delay(1800);
          return;
        }
        for(const row of log){
          if(!valid())return;
          if(submitted.action==='casino_start'&&row.action.includes('blind'))continue;
          await pokerAction(row);
        }
        if(valid())await dealBoard(round.view.community.length);
        if(!valid())return;
        if(round.complete){
          presentedRound.view.street=round.view.showdown?'showdown':'payouts';repaint();
          if(round.view.showdown)for(const [index,player] of round.view.players.entries())if(!player.folded){
            await playback.cue(player.name+' turns over their hand.',0);if(!valid())return;
            pose(seatActor(index).dataset.actor,'place');
            for(let card=0;card<player.cards.length;card++){await flip('seat-'+index,card,player.cards[card],()=>{presentedRound.view.players[index].cards[card]=player.cards[card];});if(!valid())return;}
            presentedRound.view.players[index].hand_name=player.hand_name;repaint();
            seat(index).classList.add('casino-bot-turn');
            speech(index,'',player.name+' · '+(player.hand_name||'Showdown'));
            await playback.cue(player.name+' reveals · '+(player.hand_name||'showdown')+'.',1800);if(!valid())return;
            speech(null,'');seat(index).classList.remove('casino-bot-turn');
          }
          const payouts=round.view.pots||[];
          const lastAward=new Map(payouts.flatMap(p=>(p.winners||[]).map(winner=>[winner,p])));
          presentedRound.view.street='payouts';
          presentedRound.view.pot=payouts.reduce((total,p)=>total+p.amount,0);
          repaint();
          for(const p of payouts){
            for(const winner of p.winners||[]){
              if(!valid())return;
              const player=round.view.players[winner],who=seatActor(winner);
              const result=p.refund?'Uncalled bet returned':p.winners.length===1?'Wins '+money(p.amount)+' monies':'Shares the pot';
              if(!who)continue;
              seat(winner).classList.add('casino-bot-turn');
              speech(winner,p.refund?money(p.amount)+' monies':'',player.name+' · '+result);
              await playback.cue(player.name+' · '+result);if(!valid())return;
              if(!p.refund)pose(who.dataset.actor,'react');
              await transfer('chips',pot(),who,p.winners.length===1?p.amount:undefined);
              await delay(1800);if(!valid())return;
              speech(null,'');pose(who.dataset.actor,'hold');seat(winner).classList.remove('casino-bot-turn');
              // The server's ending stack is shown after this seat's last
              // award, including any odd chip or uncalled bet it received.
              if(lastAward.get(winner)===p)Object.assign(presentedRound.view.players[winner],{stack:player.stack,all_in:player.all_in});
            }
            presentedRound.view.pot-=p.amount;
            repaint();
          }
        }
        if(valid())settleVisual();
      } finally {
        if(epoch===animationEpoch&&!dead){speech(null,'');playback.end();presentedRound=null;signature='';element.classList.remove('casino-sequence-running','casino-wheel-landing');element.querySelectorAll('.casino-bot-turn').forEach(node=>node.classList.remove('casino-bot-turn'));render();}
      }
    }
    const active = () => round && !round.complete;
    const say = message => { if (!dead) { const node=get('[data-table-status]'); node.textContent=message; node.hidden=!message; } };
    function save(value) {
      pending = value;
      if (!profile) return;
      if (value) memory.set(profile.id,value); else memory.delete(profile.id);
      try { if (storage) { if (value) storage.setItem('chuddy-table-pending-v1:'+profile.id,JSON.stringify(value)); else storage.removeItem('chuddy-table-pending-v1:'+profile.id); } } catch (_) { /* In-memory recovery remains. */ }
    }
    function load() {
      if (memory.has(profile.id)) return memory.get(profile.id);
      try { const value = storage && JSON.parse(storage.getItem('chuddy-table-pending-v1:'+profile.id) || 'null');
        if (value && value.profile_id === profile.id && ['casino_start','casino_act'].includes(value.action) && typeof value.request_id === 'string' && value.request_id.startsWith('casino-') && value.request_id.length <= 160) return value;
      } catch (_) { /* Corrupt local data does not trigger an action. */ }
      return null;
    }
    function select(next) {
      if (!names[next]) return;
      game = next; signature = '';
      host.dataset.roomGame=game;
      host.querySelector('.casino-floor').hidden = game !== 'slots';
      host.querySelector('.casino-recent').hidden = game !== 'slots';
      get('[data-table-room]').hidden = game === 'slots';
      element.querySelectorAll('[data-game]').forEach(button => button.setAttribute('aria-pressed',String(button.dataset.game === game)));
      get('[data-table-title]').textContent = names[game];
      stake.min = game === 'blackjack' ? '2' : game === 'poker' ? '20' : '1';
      stake.step = game === 'blackjack' ? '2' : '1';
      if (Number(stake.value) < Number(stake.min)) stake.value = game === 'poker' ? '100' : stake.min;
      get('[data-table-stake-label]').textContent = game === 'poker' ? 'TABLE BUY-IN' : game === 'roulette' ? 'STAKE' : 'BASE STAKE';
      if(pending)say('Request incomplete · Retry');
      else if(busy&&!isReduced()&&!motionCancelled)say(game==='roulette'?'Spinning…':'Playing…');
      else if(round&&round.game===game)say(round.complete?money(round.payout)+' monies returned':'Your turn');
      else say('');
      get('[data-table-rules]').textContent = game === 'blackjack' ? 'Six decks, fresh shoe each round. Gary stands on all 17, including soft 17. Natural blackjack pays 3:2; even base stakes only. Hit, stand, double on two cards, or split once on equal-value cards. Split aces get one card each; no double on split aces. No insurance or surrender. Four base stakes are reserved upfront for splits/doubles; unused monies return when every hand settles.' : game === 'poker' ? 'One hand of five-seat no-limit Texas Hold’em. Gary deals; you are Patrick. Buy-in is reserved once; your remaining chips and winnings return at hand end. SpongeBob, Squidward, Plankton and Mr. Krabs each receive the same fictional starting stack. Blinds, four betting rounds, side pots, short all-ins and split pots follow the actual rules. Raise amount means your TOTAL bet on this street. SpongeBob calls enthusiastically, Squidward is selective, Plankton bluffs aggressively, Mr. Krabs values his chips.' : 'European single-zero wheel: 0–36. Red/black, odd/even, low (1–18) and high (19–36) return 2× stake; dozen and column return 3×; straight number returns 36×. All returns include your stake. Zero loses outside bets. No la partage or en prison.';
      render();
      if (game !== 'slots' && connected && !loaded && !busy) sync();
    }
    function renderBlackjack(view) {
      const dealer=view.complete ? 'Gary · '+view.dealer.total :
        view.dealer.cards.includes(null) ? 'Gary · Hole card hidden' : 'Gary';
      const result=view.complete?(view.payout>view.reserve?'win':view.payout<view.reserve?'loss':'push'):'';
      const stateText={win:'Won',lose:'Lost',push:'Push',blackjack:'Blackjack',bust:'Bust',stood:'Stood',active:'In play'};
      const hands=view.hands.map((hand,index)=>{
        const activeClass=index===view.active_hand&&!view.complete?' casino-current-hand':'';
        const status=stateText[hand.outcome||hand.status]||hand.status;
        return `<section class="casino-player-hand${activeClass}"><h3>Your hand ${index+1} <span>${hand.total}${hand.soft?' SOFT':''}</span></h3>
          ${cards(hand.cards)}<p>${money(hand.bet)} staked · ${escape(status)}</p></section>`;
      }).join('');
      const moves=view.complete||busy?[]:view.actions.map(move=>{
        const label={hit:'Hit +',stand:'Stand',double:'Double ×2',split:'Split hand'}[move]||escape(move);
        return '<button type="button" data-move="'+move+'">'+label+'</button>';
      });
      return `<div class="casino-blackjack-scene">
        <div class="casino-blackjack-rail">
          <div class="casino-dealer-hand"><span>${dealer}</span>${cards(view.dealer.cards)}</div>
          <div class="casino-blackjack-lettering"><b>BLACKJACK PAYS 3 : 2</b><span>GARY STANDS ON ALL 17</span></div>
          <div class="casino-bet-mat" data-bet-mat>${chips(view.committed,'Committed stake')}</div>
        </div>
        <div class="casino-hand-seats" data-seat="0" data-result-pose="${result}">${hands}${actor('Patrick',result==='win'?'react':'hold')}</div>
      </div>`+(moves.length?'<div class="casino-table-actions">'+moves.join('')+'</div>':'');
    }
    function renderPoker(view) {
      // Even disabled action labels and raise bounds can disclose a bot's next
      // bet. Publish the human choices only after the saved sequence completes.
      const legal = busy ? {} : view.legal_actions || {};
      const latest=new Map((view.log||[]).map(row=>[row.seat,row]));
      const winners=new Set((view.pots||[]).filter(p=>!p.refund).flatMap(p=>p.winners||[]));
      const seats = view.players.map((player,index) => {
        const row=latest.get(index),boughtIn=view.street!=='TABLE PREVIEW';
        const action=player.hand_name||(row?.street===view.street&&!player.folded?actionLabel(row):'');
        const status=player.folded?'Folded':view.complete?(winners.has(index)?'Won chips':''):
          player.all_in?'All in':boughtIn&&player.street_bet?money(player.street_bet)+' on this street':'';
        const classes=['casino-poker-seat',player.folded?'casino-seat-folded':'',index===0?'casino-human-seat':''];
        return '<section data-seat="'+index+'" class="'+classes.join(' ')+'">'+actor(index ? player.name : 'Patrick')+
          '<div class="casino-seat-placard"><h3>'+escape(player.name)+' <span>'+(boughtIn?money(player.stack)+' chips':'')+'</span></h3>'+
          cards(player.folded?[]:player.cards,2,'seat-'+index)+(action?'<strong class="casino-seat-action" data-seat-action>'+escape(action)+'</strong>':'')+
          (status?'<p>'+escape(status)+'</p>':'')+'</div></section>';
      });
      const phase=view.complete?'HAND COMPLETE':view.street.toUpperCase();
      const board = '<section class="casino-poker-board"><span class="casino-board-phase">'+escape(phase)+(view.pot?' · POT '+money(view.pot)+' MONIES':'')+'</span>'+cards(view.community,5,'community')+'<div class="casino-poker-trays"><span class="casino-muck" data-muck aria-label="Folded card pile">⚓</span><div data-pot>'+chips(view.pot,'Pot')+'</div></div></section>';
      const moves = view.complete ? '' : '<div class="casino-table-actions">'+['fold','check','call','all_in'].filter(move => legal[move]).map(move => '<button type="button" data-move="'+move+'">'+({fold:'Fold',check:'Check',call:'Call '+money(legal.call_amount || 0),all_in:'All in'}[move])+'</button>').join('')+(legal.raise ? '<label>Total raise to<input data-raise type="number" min="'+legal.min_raise_to+'" max="'+legal.max_raise_to+'" step="1" value="'+legal.min_raise_to+'"></label><button type="button" data-move="raise">Raise to amount</button>' : '')+'</div>';
      return '<div class="casino-poker-scene"><div class="casino-oval-table" aria-hidden="true"></div><div class="casino-poker-bots">'+seats.slice(1).join('')+'</div>'+board+seats[0]+'</div>'+moves+'<ol class="casino-action-log">'+(view.log || []).slice(-8).map(row => '<li><b>'+escape(row.name)+'</b> '+escape(row.text || row.action)+(row.amount ? ' · '+money(row.amount) : '')+'</li>').join('')+'</ol>';
    }
    function renderRoulette(view) {
      const red = new Set([1,3,5,7,9,12,14,16,18,19,21,23,25,27,30,32,34,36]);
      const reveal=!busy||element.dataset.revealResult==='true'||isReduced()||motionCancelled;
      const numberResult = view&&reveal ? view.number : '◉';
      const order=[0,32,15,19,4,21,2,25,17,34,6,27,13,36,11,30,8,23,10,5,24,16,33,1,20,14,31,9,22,18,29,7,28,12,35,3,26];
      const step=360/37, landing=view&&reveal?-order.indexOf(view.number)*step:0;
      const gradient=order.map((n,index)=>(n===0?'#287b50':red.has(n)?'#b7444c':'#163b3b')+' '+(index*step)+'deg '+((index+1)*step)+'deg').join(',');
      const pockets=order.map((n,index)=>'<i class="casino-wheel-pocket" style="--pocket-angle:'+(index*step)+'deg">'+n+'</i>').join('');
      const wheel = '<div class="casino-roulette-assembly"><span class="casino-wheel-pointer" aria-hidden="true">▼</span><div class="casino-roulette-wheel" data-landed="'+Boolean(view&&reveal)+'" aria-label="Roulette '+(view&&reveal ? 'result '+view.number+' '+view.color : 'wheel')+'"><div class="casino-wheel-rotor" aria-hidden="true" style="--landing-angle:'+landing+'deg;background:conic-gradient(from '+(-step/2)+'deg,'+gradient+')">'+pockets+'</div><i class="casino-wheel-ball" aria-hidden="true"></i><div class="casino-wheel-result">'+numberResult+'</div><span>EUROPEAN • SINGLE ZERO</span></div></div>';
      const selectionButtons = ['red','black','odd','even','low','high'].map(kind => '<button type="button" data-roulette-kind="'+kind+'" aria-pressed="'+(selected.kind===kind)+'">'+({low:'1–18',high:'19–36'}[kind] || kind)+'</button>').join('');
      const tableNumbers=[0,...Array.from({length:3},(_,row)=>Array.from({length:12},(_,column)=>3*(column+1)-row)).flat()];
      const numberButtons = tableNumbers.map(n => '<button type="button" data-roulette-number="'+n+'" class="casino-number-'+(n===0?'green':red.has(n)?'red':'black')+'" aria-label="Number '+n+' '+(n===0?'green':red.has(n)?'red':'black')+'" aria-pressed="'+(selected.kind==='straight'&&selected.value===n)+'">'+n+'</button>').join('');
      const extra = ['dozen','column'].map(kind => '<div class="casino-roulette-extras">'+[1,2,3].map(value => '<button type="button" data-roulette-kind="'+kind+'" data-roulette-value="'+value+'" aria-pressed="'+(selected.kind===kind&&selected.value===value)+'">'+kind+' '+value+'</button>').join('')+'</div>').join('');
      return '<div class="casino-roulette-scene"><div class="casino-roulette-pedestal">'+wheel+'<div class="casino-roulette-tray" data-pot>'+chips(view&&reveal?view.payout:0,'Payout tray')+'</div></div><div class="casino-roulette-layout"><div class="casino-roulette-outside">'+selectionButtons+'</div><div class="casino-roulette-numbers">'+numberButtons+'</div>'+extra+'<div class="casino-bet-mat" data-bet-mat>'+chips(Number(stake.value)||0,'Selected stake')+'</div><p class="casino-roulette-selection">Next wager: '+escape(selected.kind)+(selected.value===undefined?'':' '+selected.value)+'</p></div></div>';
    }
    function render() {
      if (dead) return;
      const balancePending=busy&&heldBalance!==null&&!motionCancelled&&!isReduced()&&!host.ownerDocument.hidden;
      const shownBalance=balancePending?heldBalance:balance;
      get('[data-table-balance]').textContent = number(shownBalance) ? money(shownBalance) : '—';

      const reserve = Number(stake.value) * (game==='blackjack'?4:1);
      const valid = Number.isSafeInteger(Number(stake.value)) && Number(stake.value)>=Number(stake.min) && Number(stake.value)<=10000 && (game!=='blackjack'||Number(stake.value)%2===0);
      start.disabled = busy || Boolean(pending) || Boolean(active()) || !connected || !profile || !loaded || !valid || !number(balance) || balance < reserve;
      stake.disabled = busy || Boolean(pending) || Boolean(active());
      start.textContent = busy ? 'Playing…' : game==='roulette' ? 'Spin the wheel' : game==='poker' ? 'Buy in & deal' : 'Deal · '+money(reserve)+' monies';
      get('[data-table-retry]').hidden = !pending; get('[data-table-retry]').disabled = busy || !connected;
      const inputError=busy?'':!connected?'Wallet disconnected':!loaded?'Loading…':active()&&round.game!==game?'Finish '+names[round.game]+' first':!valid?'Stake: '+(game==='blackjack'?'even amount, 2–10,000':game==='poker'?'whole amount, 20–10,000':'whole amount, 1–10,000'):!active()&&number(balance)&&balance<reserve?'Not enough monies':'';
      const errorNode=get('[data-table-error]');errorNode.textContent=inputError;errorNode.hidden=!inputError;
      const displayed=presentedRound||round;
      const current = displayed && displayed.game===game ? displayed : null;
      const next = JSON.stringify([game,current,selected,busy,game==='roulette'?[stake.value,element.dataset.revealResult,motionCancelled,isReduced()]:null]);
      if (next!==signature) {
        signature=next;
        const view = current && current.view;
        const preview=game==='poker'?{players:['You (Patrick)','SpongeBob','Squidward','Plankton','Mr. Krabs'].map(name=>({name,stack:0,cards:[null,null],street_bet:0,style:'Take a seat to join the hand'})),community:[],street:'TABLE PREVIEW',pot:0,complete:false,legal_actions:{},log:[]}:
          {dealer:{cards:[null,null],total:null},hands:[{cards:[null,null],total:'—',bet:0,status:'Waiting for your deal'}],active_hand:0,complete:false,committed:0,actions:[]};
        get('[data-table-view]').classList.toggle('casino-table-preview',!view);
        const retained=new Map();
        if(game==='poker')for(const row of element.querySelectorAll('[data-card-row]'))for(const slot of row.children)retained.set(row.dataset.cardRow+':'+slot.dataset.cardSlot,slot);
        get('[data-table-view]').innerHTML = game==='roulette' ? renderRoulette(view) : game==='blackjack'?renderBlackjack(view||preview):game==='poker'?renderPoker(view||preview):'';
        if(game==='poker')for(const row of element.querySelectorAll('[data-card-row]'))for(const slot of [...row.children]){
          const old=retained.get(row.dataset.cardRow+':'+slot.dataset.cardSlot);
          if(old&&old.dataset.cardValue===slot.dataset.cardValue)slot.replaceWith(old);
        }
        const actions=get('[data-table-view]').querySelector('.casino-table-actions');get('[data-action-dock]').replaceChildren();if(actions)get('[data-action-dock]').append(actions);
        const log=get('[data-table-view]').querySelector('.casino-action-log');get('[data-table-log-holder]').replaceChildren();if(log)get('[data-table-log-holder]').append(log);
      }
      get('[data-table-view]').querySelectorAll('button,input').forEach(button => { button.disabled = busy || Boolean(pending) || !connected; });
      get('[data-action-dock]').querySelectorAll('button,input').forEach(button=>{button.disabled=busy||Boolean(pending)||!connected;});
      get('[data-table-form]').hidden=busy||Boolean(active()&&!pending);
      element.querySelectorAll('.casino-sponge-canvas').forEach(paintSponge);
      host.dataset.tableBusy=String(busy);
      element.querySelectorAll('[data-game]').forEach(button=>{button.disabled=busy||host.dataset.slotBusy==='true';});
      element.classList.toggle('casino-dealing',busy && !isReduced() && !motionCancelled);
      fitScene();
    }
    function accept(data) {
      if (!data || !profile || data.profile_id!==profile.id || !Object.hasOwn(data,'round')) throw new Error('The table reply could not be verified.');
      loaded=true;
      // A replay of an old action must not roll the visible table back.
      if (round && !data.round) return false;
      if (data.round && retiredRounds.has(data.round.id)) return false;
      if (data.round && round && data.round.id===round.id && data.round.revision<round.revision) return false;
      if (data.round && round && data.round.id!==round.id) retiredRounds.add(round.id);
      round=data.round;
      return true;
    }
    async function sync() {
      const pid=profile && profile.id, turn=version;
      try { const data=await request({action:'casino_state'}); if(!dead&&turn===version&&profile&&pid===profile.id){const wasLoaded=loaded;accept(data);if(!wasLoaded&&!pending&&!busy)select(round&&!round.complete&&game==='slots'?round.game:game);else render();} }
      catch(error){if(!dead&&turn===version)say('Table connection: '+String(error.message));}
    }
    async function send(payload) {
      if(dead||busy||!connected||!profile)return;
      if(!pending)save({...payload,profile_id:profile.id,request_id:makeID()});
      const submitted={...pending}, turn=version, before=round?JSON.parse(JSON.stringify(round)):null;
      motionCancelled=false;host.classList.remove('casino-motion-stopped');
      stopAnimation();
      delete element.dataset.revealResult;
      heldBalance=balance;
      busy=true;render();emit('busy',{busy:true,profileId:profile.id});say('Checking…');
      try {
        const data=await request(submitted);
        if(dead||turn!==version||!profile||submitted.profile_id!==profile.id)return;
        if(data.request_id!==submitted.request_id)throw new Error('The request receipt could not be verified.');
        accept(data);save(null);
        if(before&&round&&before.id===round.id&&round.game!=='roulette')presentedRound=before;
        else if(round&&round.game!=='roulette'&&!isReduced()&&!motionCancelled){
          presentedRound=JSON.parse(JSON.stringify(round));
          Object.assign(presentedRound.view,{complete:false,payout:null});
          if(round.game==='poker'){
            presentedRound.view.players.forEach(player=>{Object.assign(player,{cards:[],stack:round.view.buy_in,street_bet:0,contributed:0,folded:false,all_in:false});delete player.hand_name;});
            Object.assign(presentedRound.view,{community:[],street:'preflop',pot:0,log:[],pots:[],showdown:false,legal_actions:{}});
          }
          else {
            presentedRound.view.hands.forEach(hand=>{Object.assign(hand,{cards:[],total:'—',soft:false,status:'active',outcome:null,payout:null});});
            presentedRound.view.dealer.cards=[];presentedRound.view.dealer.total=null;presentedRound.view.active_hand=0;presentedRound.view.actions=[];
          }
        }
        if(!isReduced()&&!motionCancelled)element.classList.add('casino-sequence-running');
        if(round){game=round.game;select(game);}
        await animateTurns(before,submitted);
        if(dead||turn!==version)return;
        presentedRound=null;signature='';render();
        say(round && round.complete ? money(round.payout)+' monies returned' : 'Your turn');
        emit('wallet',{profileId:profile.id,result:data,refreshRequired:true});emit('result',{profileId:profile.id,result:data,refreshRequired:true});
      } catch(error) {
        if(dead||turn!==version)return;
        if(error.definitive===true){save(null);say('Move declined: '+String(error.message));await sync();}
        else say(String(error.message || 'Connection interrupted')+' · Retry');
      } finally {if(!dead&&turn===version){busy=false;heldBalance=null;render();emit('busy',{busy:false,profileId:profile&&profile.id});}}
    }
    const onStart=event=>{event.preventDefault();if(!start.disabled)send({action:'casino_start',game,bet:Number(stake.value),...(game==='roulette'?{selection:{...selected}}:{})});};
    const onClick=event=>{
      const button=event.target.closest('button');if(!button||!element.contains(button)||button.disabled)return;
      if(button.dataset.game){select(button.dataset.game);return;}
      if(button.hasAttribute('data-close-info')){get('[data-info-dialog]').close();return;}
      if(button.hasAttribute('data-table-retry')){send(pending);return;}
      if(button.dataset.move && round && !round.complete && !pending){const amount=button.dataset.move==='raise'?Number(get('[data-raise]').value):undefined;send({action:'casino_act',round_id:round.id,revision:round.revision,move:button.dataset.move,...(amount===undefined?{}:{amount})});return;}
      if(button.dataset.rouletteKind){selected={kind:button.dataset.rouletteKind};if(button.dataset.rouletteValue)selected.value=Number(button.dataset.rouletteValue);signature='';render();}
      if(button.dataset.rouletteNumber!==undefined){selected={kind:'straight',value:Number(button.dataset.rouletteNumber)};signature='';render();}
    };
    element.addEventListener('click',onClick);get('[data-table-form]').addEventListener('submit',onStart);stake.addEventListener('input',render);
    const openInfo=()=>get('[data-info-dialog]').showModal();infoButton.addEventListener('click',openInfo);
    return {
      update(state) {
        if(dead)return;
        const next=state.profile && typeof state.profile.id==='string'?state.profile:null;
        if((next&&next.id)!==(profile&&profile.id)){
          if(busy)emit('busy',{busy:false,profileId:profile&&profile.id});
          ++version;stopAnimation();profile=next;round=null;retiredRounds.clear();busy=false;loaded=false;pending=profile?load():null;signature='';
          say(pending?'Request incomplete · Retry':'');
        }
        profile=next;connected=state.connected===true;
        let stale=false;
        if(state.casino && profile && state.casino.profile_id===profile.id){try{stale=accept(state.casino)===false;}catch(_){/* Wait for a matching state. */}}
        if(!stale)balance=number(state.balance)?state.balance:null;
        const earnings=state.earnings;
        if(earnings){get('[data-earned-title]').textContent=root.ChuddyMonies ? root.ChuddyMonies.status(state,connected) : 'Rate unavailable';get('[data-earned-detail]').textContent=money(number(earnings.daily_remaining)?earnings.daily_remaining:0)+' score-bonus monies left today';}
        render();
      },
      stopAnimation(){motionCancelled=true;stopAnimation();if(!dead)render();},
      destroy(){if(dead)return;dead=true;++version;stopAnimation();if(fitObserver)fitObserver.disconnect();infoButton.removeEventListener('click',openInfo);infoButton.remove();element.removeEventListener('click',onClick);get('[data-table-form]').removeEventListener('submit',onStart);stake.removeEventListener('input',render);element.remove();}
    };
  }
  function mount(host, options = {}) {
    if (!host || typeof host.querySelector !== 'function') throw new TypeError('A casino host element is required.');
    if (host.__chuddyCasino) return host.__chuddyCasino;
    const doc = host.ownerDocument;
    const key = 'chuddy-casino-pending-v1:';
    let storage = options.storage;
    if (storage === undefined) { try { storage = root.localStorage; } catch (_) { storage = null; } }
    const memory = new Map();
    const request = options.request || spinRequest;
    let profile = null, balance = null, connected = false, busy = false, destroyed = false, pending = null, heldBalance = null;
    let reduced = false, generation = 0, recent = [], tables = null;
    const media = root.matchMedia ? root.matchMedia('(prefers-reduced-motion: reduce)') : null;
    host.classList.add('casino-room','casino-fit');
    host.dataset.roomGame='slots';
    host.innerHTML = `
      <div class="casino-seascape" aria-hidden="true"><i></i><i></i><i></i><i></i></div>
      <header class="casino-topbar"><span class="casino-open">LOCAL • PLAY MONEY ONLY</span><button type="button" data-casino-nav="companion" class="casino-back">Back to Chuddy ↗</button></header>
      <div class="casino-intro"><h1>Clam<br><em>Casino</em></h1></div>
      <div class="casino-floor">
        <aside class="casino-host"><div class="casino-patrick" role="img" aria-label="Chuddy, the pink starfish host"></div><div class="casino-jellyfish" aria-hidden="true">♧</div></aside>
        <section class="casino-cabinet" aria-label="Clam-o-matic slot machine">
          <div class="casino-shell-crown" aria-hidden="true">🐚</div><div class="casino-cabinet-sign"><span aria-hidden="true">✦</span><div><h2>CLAM-O-MATIC</h2></div><span aria-hidden="true">✦</span></div>
          <div class="casino-machine-face"><div class="casino-reels" data-reels role="group" aria-label="Slot reels"><div data-reel><span aria-hidden="true">🐚</span><small>Clam</small></div><div data-reel><span aria-hidden="true">🩳</span><small>Patrick's shorts</small></div><div data-reel><span aria-hidden="true">🍔</span><small>Krabby snack</small></div></div>
          <div class="casino-result" data-result role="status" aria-live="polite" aria-atomic="true" hidden><b data-result-title></b><span data-result-detail hidden></span></div></div>
          <form class="casino-controls" data-form><label class="casino-stake-label">STAKE · PLAY MONIES<input data-bet type="number" min="1" max="10000" step="1" value="10" inputmode="numeric" required></label><div class="casino-chips" role="group" aria-label="Choose a stake"><button type="button" data-bet-chip="5">5</button><button type="button" data-bet-chip="10" aria-pressed="true">10</button><button type="button" data-bet-chip="25">25</button></div><button type="submit" class="casino-spin" data-spin disabled>SPIN THE MONIES <span aria-hidden="true">↻</span></button></form>
          <p class="casino-stake-help" data-stake-help>Connecting…</p><div class="casino-machine-base" aria-hidden="true"><i class="casino-coin-slot"></i><i class="casino-speaker-grille"></i><div class="casino-coin-tray"></div></div>
        </section>
        <aside class="casino-desk"><section class="casino-wallet"><span class="casino-kicker">YOUR PLAY MONIES</span><span class="casino-wallet-icon" aria-hidden="true">🐚</span><b data-balance>—</b><span>PLAY MONIES</span><p data-profile>Local profile</p></section>
        <details class="casino-paytable" open><summary>Mr. Krabs' payout board</summary><dl><div><dt>Three of a kind</dt><dd>5× stake</dd></div><div><dt>Any matching pair</dt><dd>2× stake</dd></div><div><dt>All different</dt><dd>0 monies</dd></div></dl></details></aside>
      </div>
      <section class="casino-recent" aria-label="Recent spins in this visit"><h2>The captain's log <span>THIS VISIT</span></h2><ol data-recent><li>No spins yet</li></ol></section>
      `;
    const get = selector => host.querySelector(selector);
    const bet = get('[data-bet]'), spin = get('[data-spin]'), reels = get('[data-reels]');
    // A physical reel strip, repeated far enough to coast onto any saved symbol.
    // These decorative cells never select an outcome or change wallet arithmetic.
    const symbolKeys=Object.keys(SYMBOLS),reelNodes=[...reels.querySelectorAll('[data-reel]')];
    reelNodes.forEach((node,index)=>{
      const strip=doc.createElement('div');strip.className='casino-reel-strip';strip.setAttribute('aria-hidden','true');
      for(let cell=0;cell<17;cell++){
        const key=symbolKeys[(cell+index)%symbolKeys.length],symbol=doc.createElement('span');
        symbol.dataset.symbol=key;symbol.textContent=SYMBOLS[key].icon;strip.append(symbol);
      }
      node.append(strip);
    });
    const title = get('[data-result-title]'), detail = get('[data-result-detail]');
    const emit = (name, value) => { if (!destroyed) host.dispatchEvent(new root.CustomEvent('chuddy:casino-' + name,{bubbles:true,detail:value})); };
    const say = (heading, message, tone = '') => {
      if (destroyed) return;
      title.textContent = heading; detail.textContent = message; detail.hidden=tone!=='error'||!message; get('[data-result]').hidden=!heading&&!message;
      get('[data-result]').dataset.tone = tone;get('[data-result]').setAttribute('aria-label',heading+' '+message);
    };
    function save(value) {
      pending = value;
      if (profile) { if (value) memory.set(profile.id,value); else memory.delete(profile.id); }
      try { if (storage && profile) { if (value) storage.setItem(key + profile.id,JSON.stringify(value)); else storage.removeItem(key + profile.id); } } catch (_) { /* In-memory retry safety remains active. */ }
    }
    function load() {
      if (memory.has(profile.id)) return memory.get(profile.id);
      try {
        const value = storage && JSON.parse(storage.getItem(key + profile.id) || 'null');
        if (value && value.profile_id === profile.id && validBet(value.bet) && typeof value.request_id === 'string' && value.request_id.startsWith('casino-') && value.request_id.length <= 160) return value;
      } catch (_) { /* A malformed local entry never sends a wallet request. */ }
      return null;
    }
    const isReduced = () => reduced || Boolean(media && media.matches);
    const playback=createPlayback(host,options,isReduced);
    function stopAnimation() {
      playback.cancel();
      heldBalance=null;
      host.classList.remove('casino-spinning');
      reelNodes.forEach(node=>{delete node.dataset.stopped;delete node.dataset.landing;});
      if(!destroyed)renderControls();
    }
    async function landReel(node,index,key,animate){
      if(animate&&host.classList.contains('casino-spinning')&&!host.classList.contains('casino-motion-stopped')){
        const strip=node.querySelector('.casino-reel-strip');
        const offset=new root.DOMMatrix(root.getComputedStyle(strip).transform).m42;
        const cellHeight=90,position=-offset/cellHeight;
        const target=(symbolKeys.indexOf(key)-index+4)%4;
        const landing=target+4*Math.ceil((position+4-target)/4);
        node.style.setProperty('--reel-from',offset+'px');
        node.style.setProperty('--reel-to',(-landing*cellHeight)+'px');
        node.style.setProperty('--reel-landing-time',playback.ms(550,false)+'ms');
        node.dataset.landing='true';
        await playback.wait(550,false);
      }
    }
    function renderControls() {
      if (destroyed) return;
      const balancePending=busy&&heldBalance!==null&&!isReduced()&&!host.classList.contains('casino-motion-stopped')&&!doc.hidden;
      const shownBalance=balancePending?heldBalance:balance;
      get('[data-balance]').textContent=number(shownBalance)?money(shownBalance):'—';

      const stake = pending ? pending.bet : Number(bet.value);
      const affordable = number(balance) && validBet(stake) && balance >= stake;
      bet.disabled = busy || Boolean(pending);
      host.querySelectorAll('[data-bet-chip]').forEach(button => {
        button.disabled = busy || Boolean(pending);
        button.setAttribute('aria-pressed',String(Number(button.dataset.betChip) === stake));
      });
      spin.disabled = busy || !connected || !profile || (!pending && !affordable);
      spin.textContent = busy ? 'CHECKING THE MONIES…' : pending ? 'RETRY THE SAME SPIN ↻' : 'SPIN THE MONIES ↻';
      get('[data-stake-help]').textContent = !connected ? 'Wallet disconnected' : pending ? 'Spin incomplete · Retry' : !validBet(bet.value) ? 'Stake: whole amount, 1–10,000' : !affordable ? 'Not enough monies' : '';
      get('[data-stake-help]').hidden=connected&&(busy||(!pending&&affordable));
      host.setAttribute('aria-busy',String(busy));
      host.dataset.slotBusy=String(busy);
      host.querySelectorAll('[data-game]').forEach(button=>{button.disabled=busy||host.dataset.tableBusy==='true';});
    }
    function renderHistory() {
      const list = get('[data-recent]'); list.replaceChildren();
      for (const row of recent) {
        const li = doc.createElement('li');
        const icons = doc.createElement('span'); icons.setAttribute('aria-label',row.symbols.map(s => SYMBOLS[s].label).join(', '));
        icons.textContent = row.symbols.map(s => SYMBOLS[s].icon).join(' ');
        const payout = doc.createElement('b'); payout.textContent = money(row.payout) + ' paid';
        const stake = doc.createElement('small'); stake.textContent = money(row.bet) + ' staked';
        li.append(icons, payout, stake); list.append(li);
      }
    }
    async function play(event) {
      event.preventDefault();
      if (busy || destroyed || !connected || !profile) return;
      host.classList.remove('casino-motion-stopped');
      if (!pending && (!validBet(bet.value) || !number(balance) || balance < Number(bet.value))) { renderControls(); return; }
      if (!pending) save({bet:Number(bet.value),request_id:makeID(),profile_id:profile.id});
      const submitted = {...pending};
      const turn = ++generation;
      heldBalance=balance;
      busy = true; renderControls();
      emit('busy',{busy:true,profileId:submitted.profile_id});
      if (!isReduced()) {playback.begin();host.classList.add('casino-spinning');}
      say('Spinning…','');
      try {
        const result = await request(submitted);
        if (!validResult(result,submitted)) throw new Error('Spin receipt could not be verified');
        // A profile can change during a slow request. Never display its money as another user's.
        if (destroyed || turn !== generation || !profile || profile.id !== submitted.profile_id) return;
        save(null);
        // A replay can return an old balance; the coordinator must refresh canonical state.
        emit('wallet',{profileId:submitted.profile_id,result,refreshRequired:true});
        emit('result',{profileId:submitted.profile_id,result,refreshRequired:true});
        const animate=!isReduced()&&!host.classList.contains('casino-motion-stopped')&&!doc.hidden;
        if(animate){await playback.cue('Slots · watch the three reels spin.');await playback.wait(2800);}
        if (destroyed || turn !== generation || !profile || profile.id !== submitted.profile_id) return;
        for(const [index,node] of reelNodes.entries()){
          if(animate&&!host.classList.contains('casino-motion-stopped')){await playback.cue('Slots · reel '+(index+1)+' stops.');await playback.wait(250);}
          if(destroyed||turn!==generation||!profile||profile.id!==submitted.profile_id)return;
          await landReel(node,index,result.symbols[index],animate);
          if(destroyed||turn!==generation||!profile||profile.id!==submitted.profile_id)return;
          const symbol = SYMBOLS[result.symbols[index]];
          node.dataset.stopped='true';
          delete node.dataset.landing;
          node.querySelector(':scope > span').textContent = symbol.icon; node.querySelector('small').textContent = symbol.label;
        }
        recent.unshift(result); recent = recent.slice(0,5); renderHistory();
        const won = result.payout > result.bet;
        say(money(result.payout)+' monies paid',money(result.bet)+' staked',won ? 'win' : 'loss');
        if(animate&&!host.classList.contains('casino-motion-stopped')){await playback.cue('Slots · '+money(result.payout)+' monies paid. Read the result before the next spin.');await playback.wait(1800);}
      } catch (error) {
        if (destroyed || turn !== generation || !profile || profile.id !== submitted.profile_id) return;
        if (error.definitive === true) { save(null); say('Spin declined',error.message,'error'); }
        else say('Spin incomplete · Retry',String(error.message || 'Connection interrupted'),'error');
      } finally {
        if (!destroyed && turn === generation) {
          heldBalance=null;
          stopAnimation(); busy = false; renderControls(); emit('busy',{busy:false,profileId:submitted.profile_id});
        }
      }
    }
    const form = get('[data-form]'); form.addEventListener('submit',play);
    const onInput = () => renderControls(); bet.addEventListener('input',onInput);
    const click = event => {
      const button = event.target.closest('button'); if (!button || !host.contains(button)) return;
      if (button.dataset.casinoNav) {const info=host.querySelector('[data-info-dialog]');if(info&&info.open)info.close();emit('navigate',{destination:button.dataset.casinoNav});}
      if (button.dataset.betChip && !busy && !pending) { bet.value = button.dataset.betChip; renderControls(); }
    };
    host.addEventListener('click',click);
    let wasReduced=false;
    const onMotion = () => {
      const nowReduced=isReduced();host.dataset.reducedMotion=String(nowReduced);
      if(nowReduced&&!wasReduced){stopAnimation();if(tables)tables.stopAnimation();}
      wasReduced=nowReduced;
    };
    if (media && media.addEventListener) media.addEventListener('change',onMotion);
    const onVisibility=()=>{host.dataset.motionPaused=String(doc.hidden);if(doc.hidden){stopAnimation();if(tables)tables.stopAnimation();}};
    doc.addEventListener('visibilitychange',onVisibility);
    const observer=root.IntersectionObserver?new root.IntersectionObserver(entries=>{host.dataset.motionPaused=String(doc.hidden||!entries[0].isIntersecting);if(!entries[0].isIntersecting){stopAnimation();if(tables)tables.stopAnimation();}}):null;
    if(observer)observer.observe(host);
    const api = {
      update(state) {
        if (destroyed) return;
        const next = state.profile && typeof state.profile.id === 'string' ? state.profile : null;
        if ((next && next.id) !== (profile && profile.id)) {
          ++generation; stopAnimation();
          if (busy) emit('busy',{busy:false,profileId:profile && profile.id});
          busy = false; profile = next; pending = profile ? load() : null; recent = [];
          get('[data-recent]').innerHTML = '<li>No spins yet</li>';
          if (pending) bet.value = String(pending.bet);
          say(pending ? 'Spin incomplete · Retry' : '','');
        } else { profile = next; }
        balance = number(state.balance) ? state.balance : null;
        connected = state.connected === true;
        reduced = state.reducedMotion === true; onMotion();
        get('[data-profile]').textContent = profile ? profile.name || 'Local profile' : 'Choose a local profile';
        renderControls();
        if (tables) tables.update(state);
      },
      stopAnimation(){host.classList.add('casino-motion-stopped');stopAnimation();if(tables)tables.stopAnimation();},
      destroy() {
        if (destroyed) return;
        // No late UI changes or follow-up actions; an already-submitted wallet transaction
        // remains recoverable by its saved ID when this profile revisits the room.
        emit('busy',{busy:false,profileId:profile && profile.id});
        if (tables) tables.destroy();
        destroyed = true; ++generation; stopAnimation();playback.destroy();
        if (media && media.removeEventListener) media.removeEventListener('change',onMotion);
        doc.removeEventListener('visibilitychange',onVisibility);if(observer)observer.disconnect();
        form.removeEventListener('submit',play); bet.removeEventListener('input',onInput); host.removeEventListener('click',click);
        host.replaceChildren(); host.classList.remove('casino-room'); host.removeAttribute('aria-busy'); delete host.__chuddyCasino;
      }
    };
    tables = mountTables(host,options,emit,storage,isReduced,playback);
    const infoBody=host.querySelector('[data-info-body]');
    for(const selector of ['.casino-earned','.casino-recent','.casino-paytable']){const node=host.querySelector(selector);if(node)infoBody.append(node);}
    const floor=host.querySelector('.casino-floor'),slotViewport=doc.createElement('div'),slotCanvas=doc.createElement('div');
    slotViewport.className='casino-slot-viewport';slotCanvas.className='casino-slot-canvas';floor.before(slotViewport);slotViewport.append(slotCanvas);slotCanvas.append(floor);
    const fitSlots=()=>{
      const box=slotViewport.getBoundingClientRect();if(!box.width||!box.height)return;
      const compact=box.width<660;
      const scale=Math.min(box.width/(compact?410:900),box.height/(compact?552:500),1.35);
      slotCanvas.dataset.compact=String(compact);
      slotCanvas.style.transform='translate(-50%,-50%) scale('+scale+')';
    };
    const slotObserver=root.ResizeObserver?new root.ResizeObserver(fitSlots):null;if(slotObserver)slotObserver.observe(slotViewport);
    const destroyTables=tables.destroy;tables.destroy=()=>{if(slotObserver)slotObserver.disconnect();destroyTables();};
    host.__chuddyCasino = api;
    api.update(options.state || {});
    return api;
  }
  const exported = Object.freeze({mount,SYMBOLS,validBet,validResult});
  if (typeof module !== 'undefined' && module.exports) module.exports = exported;
  root.ChuddyCasinoRoom = exported;
})(typeof window !== 'undefined' ? window : globalThis);
