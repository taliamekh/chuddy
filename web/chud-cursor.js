/* App-window fallback to the native click-through desktop sprite. Visual only:
 * it never clicks DOM controls, moves the OS pointer or requests distractions.
 * Load character-rig.js first, call update(state) from the existing state poll.
 */
(function (root) {
  'use strict';
  const clamp=(n,a,b)=>Math.max(a,Math.min(b,n));
  function idlePosition(seconds,reduced=false){
    // Compatibility helper: idle and warning never wander or bob.
    return {u:.88,v:.86,walking:false};
  }
  function position(seconds,width,height,target,reduced=false){
    const w=Math.min(112,width),h=Math.min(168,height);
    let x,y;
    if(target){
      const p=reduced?1:clamp(seconds/.35,0,1),s=p*p*(3-2*p);
      const idle=idlePosition(0,reduced),startX=(width-w)*idle.u,startY=(height-h)*idle.v;
      x=startX+(target.x-w*.52-startX)*s;
      y=startY+(target.y-h*.30-startY)*s;
      if(!reduced&&p<1)y-=Math.abs(Math.sin(seconds*18))*9;
    }else{
      const idle=idlePosition(seconds,reduced);
      x=(width-w)*idle.u;
      y=(height-h)*idle.v;
    }
    return {x:clamp(x,0,Math.max(0,width-w)),y:clamp(y,0,Math.max(0,height-h))};
  }
  class ChudCursor{
    constructor(options={}){
      this.options=options;this.destroyed=false;this.visible=false;this.raf=0;this.began=0;this.id=null;
      this.target=null;this.reduced=false;this.generation=0;
      this.node=document.createElement('div');this.node.className='chud-cursor';
      this.node.hidden=true;this.node.setAttribute('aria-hidden','true');
      this.canvas=document.createElement('canvas');this.node.appendChild(this.canvas);
      const ring=document.createElement('i');ring.className='chud-cursor-click';this.node.appendChild(ring);
      (options.host||document.body).appendChild(this.node);
      this.rig=new root.ChuddyRasterRig(this.canvas,{src:options.src});
      this.motion=root.matchMedia?root.matchMedia('(prefers-reduced-motion: reduce)'):null;
      this.onVisibility=()=>{if(document.hidden)this.hide();};
      document.addEventListener('visibilitychange',this.onVisibility);
    }
    update(state){
      if(this.destroyed)return;
      if(!state||state.session!=='running'||document.hidden){this.hide();return;}
      const native=state.desktop_overlay;
      // An initialized native window may be hidden/disarmed after an interruption
      // or failed restart. Only verified rendering replaces the app fallback.
      if(native&&native.status==='ready'&&native.enabled===true&&native.visible===true){this.hide();return;}
      const interruption=state.interruption;
      if(interruption&&!['warning','approaching','clicking'].includes(interruption.phase)){this.hide();return;}
      if(!interruption&&this.options.roam===false){this.hide();return;}
      const moving=Boolean(interruption&&['approaching','clicking'].includes(interruption.phase));
      // Warning and approach can have one interruption ID. Start the route only
      // when approach begins; a long warning must not consume its travel time.
      const id=interruption?interruption.id+':'+(moving?'moving':'warning'):'roam';
      const viewport=root.innerWidth+','+root.innerHeight;
      if(viewport!==this.viewport&&this.visible)this.hide();
      this.viewport=viewport;
      const reduced=Boolean(this.options.reducedMotion||(this.motion&&this.motion.matches));
      if(reduced!==this.reduced&&this.visible)this.hide();
      this.reduced=reduced;
      if(id!==this.id||!this.visible){
        this.id=id;this.began=performance.now();
        // A theatrical close-button target, never a DOM or system click.
        this.target=moving?{x:root.innerWidth-36,y:24}:null;
      }
      this.node.dataset.phase=interruption?interruption.phase:'roaming';
      this.rig.state({expression:'neutral',activity:state.voice&&state.voice.speaking?'speaking':'idle',reducedMotion:this.reduced||!moving});
      if(!this.visible){
        this.visible=true;this.node.hidden=false;const generation=++this.generation;
        const tick=now=>{
          if(!this.visible||this.destroyed||generation!==this.generation)return;
          const elapsed=(now-this.began)/1000;
          const p=position(elapsed,root.innerWidth,root.innerHeight,this.target,this.reduced);
          this.node.style.transform='translate3d('+p.x+'px,'+p.y+'px,0)';
          this.node.classList.toggle('is-clicking',Boolean(this.target&&elapsed>=.35));
          if(!this.reduced)this.raf=requestAnimationFrame(tick);
        };
        this.raf=requestAnimationFrame(tick);
      }
    }
    hide(){this.visible=false;this.generation++;cancelAnimationFrame(this.raf);this.node.hidden=true;this.id=null;}
    destroy(){if(this.destroyed)return;this.hide();this.destroyed=true;document.removeEventListener('visibilitychange',this.onVisibility);this.rig.destroy();this.node.remove();}
  }
  root.ChudCursor=ChudCursor;
  if(typeof module==='object'&&module.exports)module.exports={position,idlePosition,ChudCursor};
})(typeof globalThis==='object'?globalThis:this);
