/* Raster puppet for the Chuddy character.
 * State-specific artwork stays fixed while an anchored canvas mesh moves only
 * outer arms, belly and (during actual playback) a small source-mouth region.
 */
(function (root) {
  'use strict';
  const EXPRESSIONS = ['neutral','thinking','confused','happy','smug','weary'];
  const X_KNOTS = [0,.1,.18,.25,.31,.37,.41,.455,.5,.545,.59,.63,.69,.75,.82,.9,1];
  const Y_KNOTS = [0,.06,.12,.18,.23,.28,.33,.37,.395,.412,.43,.455,.48,.54,.61,.68,.75,.82,.89,.95,1];
  const POSES = Object.freeze({neutral:[0,0],thinking:[1,0],confused:[2,0],happy:[0,1],smug:[1,1],weary:[2,1]});
  const ARTWORK=Object.freeze({neutral:{ready:true},thinking:{ready:false,reason:'Cheek correction pending'},confused:{ready:false,reason:'Cheek correction pending'},happy:{ready:true},smug:{ready:true},weary:{ready:false,reason:'Cheek correction pending'}});
  // Source-cell pixels: midpoint of the purple upper eyelids, opaque sole line,
  // and mouth center. One uniform scale aligns eyes AND soles without stretching.
  // Pending cells are deliberately uncalibrated; pose() resolves them to neutral.
  const LANDMARKS=Object.freeze({neutral:{eye:[222.5,162.5],sole:592,mouth:[222,251]},happy:{eye:[222.5,157.5],sole:594,mouth:[222,248]},smug:{eye:[199.5,160],sole:593,mouth:[205,241]}});
  function placement(name){
    const marks=LANDMARKS[name]||LANDMARKS.neutral;
    const scale=(.89-.30)*627/(marks.sole-marks.eye[1]);
    return {scale,x:.5-scale*marks.eye[0]/418,y:.30-scale*marks.eye[1]/627};
  }
  const REGISTRATION=Object.freeze(Object.fromEntries(EXPRESSIONS.map(name=>{const p=placement(name);return [name,[p.x-.07,p.y-.07]];})));
  const MOUTHS=Object.freeze(Object.fromEntries(EXPRESSIONS.map(name=>{const p=placement(name),m=(LANDMARKS[name]||LANDMARKS.neutral).mouth;return [name,{x:p.x+p.scale*m[0]/418,y:p.y+p.scale*m[1]/627}];})));
  function smooth(a,b,value) { const t=Math.max(0,Math.min(1,(value-a)/(b-a))); return t*t*(3-2*t); }
  function band(value,a,b,c,d) { return smooth(a,b,value)*(1-smooth(c,d,value)); }
  function expression(value) { return EXPRESSIONS.includes(value)?value:'neutral'; }
  function pose(value) { const requested=expression(value);const name=ARTWORK[requested].ready?requested:'neutral';return {name,column:POSES[name][0],row:POSES[name][1]}; }
  function visualState(options={}){
    const busy=/thinking|generating|working|running|loading|responding|tool|preparing/i.test(String(options.agentStatus||''));
    const manual=options.expressionOverride&&options.expressionOverride!=='auto'?expression(options.expressionOverride):null;
    const selected=manual||(options.agentError?'confused':busy?'thinking':options.session==='paused'?'weary':/nailed|teamwork|basically finished|investing/i.test(String(options.lastText||''))?'smug':'neutral');
    const activity=options.voiceSpeaking===true?'speaking':options.session==='paused'?'paused':options.listening?'listening':options.agentError?'error':busy?'thinking':'idle';
    return {activity,expression:selected,reducedMotion:Boolean(options.reducedMotion)};
  }
  function amount(activity,reduced) { if(reduced||activity==='paused')return 0;return ({idle:.9,listening:.55,thinking:.7,speaking:.85,error:.3})[activity]||.9; }
  function warp(x,y,width,height,seconds,strength,speaking,mouth={x:.5,y:.42}) {
    if(!strength)return {x,y};
    const u=x/width,v=y/height;
    const outer=(1-smooth(.28,.39,u))+smooth(.61,.72,u);
    const arm=outer*band(v,.48,.54,.63,.75);
    const belly=band(u,.23,.35,.65,.77)*band(v,.48,.54,.67,.77);
    const breath=.5+.5*Math.sin(seconds*1.25-.6);
    const wave=Math.sin(seconds*.9);
    let dx=width*strength*(.0043*arm*wave+.0032*belly*breath*(u-.5)*2);
    let dy=height*strength*(.0024*arm*Math.sin(seconds*.9-.5)-.0018*belly*breath);
    // Eyes, brows and the central upper face are pixel-anchored. A small jaw
    // texture warp animates only when the service reports actual voice playback.
    if(speaking){
      const jaw=band(u,mouth.x-.09,mouth.x-.045,mouth.x+.045,mouth.x+.09)*band(v,mouth.y-.025,mouth.y-.008,mouth.y+.035,mouth.y+.06);
      dy+=height*.0028*strength*jaw*(.5+.5*Math.sin(seconds*12.5));
    }
    return {x:x+dx,y:y+dy};
  }
  function unmoved(source,target,left,top){return Math.abs(target.x-source.x-left)<.001&&Math.abs(target.y-source.y-top)<.001;}
  // Restore large, integer-aligned static patches after mesh rasterization. This
  // avoids fractional tile resampling changing otherwise anchored face pixels.
  function restoreAnchors(ctx,texture,left,top,speaking,mouth){
    const w=texture.width,h=texture.height;
    const copy=(x,y,width,height)=>{if(width<=0||height<=0)return;ctx.clearRect(left+x,top+y,width,height);ctx.drawImage(texture,x,y,width,height,left+x,top+y,width,height);};
    const faceBottom=Math.floor(h*.48),feetTop=Math.ceil(h*.82);
    if(!speaking)copy(0,0,w,faceBottom);
    else {
      const x0=Math.floor(w*(mouth.x-.10)),x1=Math.ceil(w*(mouth.x+.10));
      const y0=Math.floor(h*(mouth.y-.035)),y1=Math.min(faceBottom,Math.ceil(h*(mouth.y+.07)));
      copy(0,0,w,y0);copy(0,y0,x0,y1-y0);copy(x1,y0,w-x1,y1-y0);copy(0,y1,w,faceBottom-y1);
    }
    copy(0,feetTop,w,h-feetTop);
  }
  function triangle(ctx,texture,source,target){
    const [s0,s1,s2]=source,[d0,d1,d2]=target;
    const det=(s1.x-s0.x)*(s2.y-s0.y)-(s2.x-s0.x)*(s1.y-s0.y);
    if(!det)return;
    const a=((d1.x-d0.x)*(s2.y-s0.y)-(d2.x-d0.x)*(s1.y-s0.y))/det;
    const b=((d1.y-d0.y)*(s2.y-s0.y)-(d2.y-d0.y)*(s1.y-s0.y))/det;
    const c=((d2.x-d0.x)*(s1.x-s0.x)-(d1.x-d0.x)*(s2.x-s0.x))/det;
    const d=((d2.y-d0.y)*(s1.x-s0.x)-(d1.y-d0.y)*(s2.x-s0.x))/det;
    const e=d0.x-a*s0.x-c*s0.y,f=d0.y-b*s0.x-d*s0.y;
    const center={x:(d0.x+d1.x+d2.x)/3,y:(d0.y+d1.y+d2.y)/3};
    // Expand by distance to the nearest EDGE, not distance to each vertex.
    // Vertex-only padding left subpixel diagonal cracks in long thin cells.
    const edgeDistance=target.map((p,i)=>{const q=target[(i+1)%3];return Math.abs((q.x-p.x)*(p.y-center.y)-(p.x-center.x)*(q.y-p.y))/(Math.hypot(q.x-p.x,q.y-p.y)||1);});
    const overlap=1+.85/Math.max(.01,Math.min(...edgeDistance));
    ctx.save();ctx.beginPath();
    target.forEach((point,index)=>{const px=center.x+(point.x-center.x)*overlap,py=center.y+(point.y-center.y)*overlap;if(index===0)ctx.moveTo(px,py);else ctx.lineTo(px,py);});
    ctx.closePath();ctx.clip();ctx.setTransform(a,b,c,d,e,f);
    const minX=Math.max(0,Math.floor(Math.min(s0.x,s1.x,s2.x))-2),minY=Math.max(0,Math.floor(Math.min(s0.y,s1.y,s2.y))-2);
    const maxX=Math.min(texture.width,Math.ceil(Math.max(s0.x,s1.x,s2.x))+2),maxY=Math.min(texture.height,Math.ceil(Math.max(s0.y,s1.y,s2.y))+2);
    ctx.drawImage(texture,minX,minY,maxX-minX,maxY-minY,minX,minY,maxX-minX,maxY-minY);ctx.restore();
  }

  class ChuddyRasterRig {
    constructor(canvas,options={}) {
      this.canvas=canvas;
      this.container=canvas.parentElement;
      this.ctx=canvas.getContext('2d',{alpha:true,willReadFrequently:true});
      this.src=options.src||'/web/assets/chuddy/gestures.png';
      this.activity='idle';this.expression='neutral';this.reducedMotion=false;
      this.visible=true;this.image=null;this.texture=null;this.textureKey='';this.raf=0;this.ticking=false;this.lastFrame=null;this.elapsed=0;this.destroyed=false;
      this.query=root.matchMedia?root.matchMedia('(prefers-reduced-motion: reduce)'):null;
      this.onMotion=()=>this.updateLoop();
      if(this.query)this.query.addEventListener('change',this.onMotion);
      this.onVisibility=()=>this.updateLoop();
      document.addEventListener('visibilitychange',this.onVisibility);
      this.resize=typeof ResizeObserver==='function'?new ResizeObserver(()=>this.updateLoop()):null;
      if(this.resize)this.resize.observe(canvas);
      this.intersection=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{this.visible=Boolean(entries[0]&&entries[0].isIntersecting);this.updateLoop();}):null;
      if(this.intersection)this.intersection.observe(canvas);
      this.load();
    }
    load(){
      const image=new Image();image.decoding='async';
      image.onload=()=>{
        if(this.destroyed||!image.naturalWidth||!image.naturalHeight)return;
        this.image=image;this.texture=null;
        this.container.dataset.raster='loaded';
        const fallback=this.container.querySelector('.raster-frame-fallback');
        if(fallback)fallback.style.backgroundImage='url("'+this.src.replace(/["\\\n]/g,'')+'")';
        this.updateLoop();
      };
      image.onerror=()=>{if(this.destroyed)return;this.container.dataset.raster='unavailable';this.container.setAttribute('aria-label','Chuddy appearance reference; expression sheet is unavailable');};
      image.src=this.src;
    }
    state(next){
      const selected=expression(next.expression);
      const activity=['idle','listening','thinking','speaking','paused','error'].includes(next.activity)?next.activity:'idle';
      const reduced=Boolean(next.reducedMotion);
      if(selected===this.expression&&activity===this.activity&&reduced===this.reducedMotion)return;
      this.expression=selected;this.activity=activity;this.reducedMotion=reduced;
      this.container.dataset.expression=selected;this.container.dataset.activity=activity;
      this.container.dataset.artwork=ARTWORK[selected].ready?'ready':'revision-pending';
      this.container.setAttribute('aria-label','Chuddy · '+selected+(ARTWORK[selected].ready?'':' · art revision pending, neutral pose shown')+(activity==='speaking'?' · speaking':''));
      this.updateLoop();
    }
    updateLoop(){
      if(this.destroyed)return;
      const reduced=this.reducedMotion||Boolean(this.query&&this.query.matches);
      const mayDraw=this.visible&&!document.hidden;
      const animate=mayDraw&&this.image&&this.ctx&&amount(this.activity,reduced)>0;
      if(mayDraw)this.draw(this.elapsed);
      if(animate&&!this.ticking){this.ticking=true;this.lastFrame=null;this.raf=requestAnimationFrame(now=>this.tick(now));}
      else if(!animate&&this.ticking){this.ticking=false;this.lastFrame=null;cancelAnimationFrame(this.raf);}
    }
    tick(now){if(!this.ticking||this.destroyed)return;if(this.lastFrame===null)this.lastFrame=now;if(now-this.lastFrame>=1000/30){this.elapsed+=Math.min(100,now-this.lastFrame);this.draw(this.elapsed);this.lastFrame=now;}this.raf=requestAnimationFrame(time=>this.tick(time));}
    draw(now){
      if(!this.image)return;
      const bounds=this.canvas.getBoundingClientRect();
      if(bounds.width<1||bounds.height<1)return;
      const density=Math.min(root.devicePixelRatio||1,2,Math.sqrt(1500000/(bounds.width*bounds.height)));
      const cw=Math.max(1,Math.round(bounds.width*density)),ch=Math.max(1,Math.round(bounds.height*density));
      if(this.canvas.width!==cw||this.canvas.height!==ch){this.canvas.width=cw;this.canvas.height=ch;this.texture=null;}
      const frame=pose(this.expression),sw=this.image.naturalWidth/3,sh=this.image.naturalHeight/2;
      const ratio=sw/sh;
      const width=Math.floor(Math.min(cw,ch*ratio)),height=Math.floor(width/ratio);
      if(!width||!height)return;
      const left=Math.floor((cw-width)/2),top=Math.floor(ch-height);
      const registration=placement(frame.name);
      const fallback=this.container.querySelector('.raster-frame-fallback');
      if(fallback){fallback.style.width=(width*registration.scale/density)+'px';fallback.style.height=(height*registration.scale/density)+'px';fallback.style.left=((left+width*registration.x)/density)+'px';fallback.style.bottom=((ch-top-height*(registration.y+registration.scale))/density)+'px';fallback.style.transform='none';fallback.style.backgroundSize='300% 200%';fallback.style.backgroundPosition=(frame.column*50)+'% '+(frame.row*100)+'%';}
      if(!this.ctx)return;
      const key=frame.name+':'+width+':'+height;
      if(!this.texture||this.textureKey!==key){
        const texture=document.createElement('canvas');texture.width=width;texture.height=height;
        const context=texture.getContext('2d',{alpha:true,willReadFrequently:true});
        if(!context)return;
        context.imageSmoothingEnabled=true;context.imageSmoothingQuality='high';
        context.drawImage(this.image,frame.column*sw,frame.row*sh,sw,sh,
                          width*registration.x,height*registration.y,width*registration.scale,height*registration.scale);
        this.texture=texture;
        this.textureKey=key;
      }
      const ctx=this.ctx;ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,cw,ch);
      const strength=amount(this.activity,this.reducedMotion||Boolean(this.query&&this.query.matches));
      if(!strength){ctx.drawImage(this.texture,left,top);this.container.dataset.raster='painted';return;}
      const seconds=now/1000,speaking=this.activity==='speaking';
      for(let row=0;row<Y_KNOTS.length-1;row++){
        const y0=Y_KNOTS[row]*height,y1=Y_KNOTS[row+1]*height;
        for(let column=0;column<X_KNOTS.length-1;column++){
          const x0=X_KNOTS[column]*width,x1=X_KNOTS[column+1]*width;
          const s00={x:x0,y:y0},s10={x:x1,y:y0},s01={x:x0,y:y1},s11={x:x1,y:y1};
          const offset=point=>{const moved=warp(point.x,point.y,width,height,seconds,strength,speaking,MOUTHS[frame.name]);return {x:moved.x+left,y:moved.y+top};};
          const d00=offset(s00),d10=offset(s10),d01=offset(s01),d11=offset(s11);
          if(unmoved(s00,d00,left,top)&&unmoved(s10,d10,left,top)&&unmoved(s01,d01,left,top)&&unmoved(s11,d11,left,top)){ctx.drawImage(this.texture,x0,y0,x1-x0,y1-y0,left+x0,top+y0,x1-x0,y1-y0);continue;}
          triangle(ctx,this.texture,[s00,s10,s01],[d00,d10,d01]);triangle(ctx,this.texture,[s11,s01,s10],[d11,d01,d10]);
        }
      }
      restoreAnchors(ctx,this.texture,left,top,speaking,MOUTHS[frame.name]);
      this.container.dataset.raster='painted';
    }
    destroy(){this.destroyed=true;this.ticking=false;cancelAnimationFrame(this.raf);if(this.resize)this.resize.disconnect();if(this.intersection)this.intersection.disconnect();if(this.query)this.query.removeEventListener('change',this.onMotion);document.removeEventListener('visibilitychange',this.onVisibility);this.image=null;this.texture=null;}
  }
  const math={EXPRESSIONS,POSES,ARTWORK,REGISTRATION,LANDMARKS,MOUTHS,placement,pose,expression,visualState,smooth,band,amount,warp};
  root.ChuddyRasterMath=math;root.ChuddyRasterRig=ChuddyRasterRig;
  if(typeof module==='object'&&module.exports)module.exports=math;
})(typeof globalThis==='object'?globalThis:this);
