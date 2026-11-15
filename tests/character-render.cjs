'use strict';
// Run with bundled Node packages on NODE_PATH; never launches the real app.
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const http=require('node:http');
const {chromium}=require('playwright');
const root=path.resolve(__dirname,'..');

test('headless raster rendering, accessibility preferences and lifecycle',async t=>{
  const server=http.createServer((req,res)=>{
    const file={'/rig.js':'web/character-rig.js','/sheet.png':'web/assets/chuddy/gestures.png'}[req.url];
    if(!file){res.writeHead(404);res.end();return;}
    res.setHeader('Content-Type',file.endsWith('.js')?'text/javascript; charset=utf-8':'image/png');
    res.end(fs.readFileSync(path.join(root,file)));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  let browser;
  t.after(async()=>{try{if(browser)await browser.close();}finally{await new Promise(resolve=>server.close(resolve));}});
  browser=await chromium.launch({channel:'msedge',headless:true});
  const page=await browser.newPage({viewport:{width:1400,height:900},deviceScaleFactor:1});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/rig.js`);
  await page.setContent(`<style>body{margin:0;background:#fff0f6;font:16px system-ui}.stage{position:relative;width:418px;height:627px}.stage canvas{position:absolute;width:100%;height:100%}.raster-frame-fallback{position:absolute;background-repeat:no-repeat}.stage[data-raster=painted] .raster-frame-fallback{opacity:0}</style><div class="stage"><div class="raster-frame-fallback"></div><canvas></canvas></div>`);
  await page.evaluate(()=>{
    window.framesQueued=new Map();window.frameID=0;
    window.requestAnimationFrame=callback=>{const id=++window.frameID;framesQueued.set(id,callback);return id;};
    window.cancelAnimationFrame=id=>framesQueued.delete(id);
    window.ResizeObserver=class{constructor(cb){window.resizeCallback=cb;}observe(){}disconnect(){}};
    window.IntersectionObserver=class{constructor(cb){window.intersectionCallback=cb;}observe(){}disconnect(){}};
  });
  await page.addScriptTag({url:`http://127.0.0.1:${server.address().port}/rig.js`});
  await page.evaluate(()=>{window.rig=new ChuddyRasterRig(document.querySelector('canvas'),{src:'/sheet.png'});});
  await page.waitForFunction(()=>window.rig.image,{},{polling:20});
  await t.test('ready poses keep actual face and feet pixels stable; voice moves only the mouth',async()=>{
    const results=await page.evaluate(()=>{
      const report=[];
      for(const expression of ['neutral','happy','smug'])for(const size of [[418,627],[320,480],[840,700]]){
        rig.container.style.width=size[0]+'px';rig.container.style.height=size[1]+'px';
        rig.state({expression,activity:'idle',reducedMotion:true});rig.draw(0);
        const w=rig.texture.width,h=rig.texture.height,left=Math.floor((rig.canvas.width-w)/2),top=rig.canvas.height-h;
        const pixels=()=>rig.ctx.getImageData(left,top,w,h).data;
        const still=pixels();let changedFace=0,changedFeet=0,changedBody=0,mouthChanges=0,leakedMouth=0,seamHoles=0;
        rig.state({expression,activity:'idle',reducedMotion:false});
        for(const time of [0,600,1800,4000]){
          rig.draw(time);const frame=pixels();
          for(let y=0;y<h;y++)for(let x=0;x<w;x++){
            const i=(y*w+x)*4;
            // A 7x7 opaque source neighborhood must not develop mesh cracks.
            if(y>h*.49&&y<h*.74&&x>w*.2&&x<w*.8&&frame[i+3]<248){
              let solid=true;
              for(let dy=-3;dy<=3;dy++)for(let dx=-3;dx<=3;dx++)if(still[((y+dy)*w+x+dx)*4+3]<250)solid=false;
              if(solid)seamHoles++;
            }
            if(![0,1,2,3].some(c=>frame[i+c]!==still[i+c]))continue;
            if(y<Math.floor(h*.48))changedFace++;
            else if(y>=Math.ceil(h*.82))changedFeet++;
            else changedBody++;
          }
        }
        rig.state({expression,activity:'speaking',reducedMotion:false});rig.draw(0);const a=pixels();rig.draw(375);const b=pixels();
        const m=ChuddyRasterMath.MOUTHS[expression];
        for(let y=0;y<Math.floor(h*.48);y++)for(let x=0;x<w;x++){
          const i=(y*w+x)*4;if(![0,1,2,3].some(c=>a[i+c]!==b[i+c]))continue;
          mouthChanges++;
          if(x<Math.floor(w*(m.x-.1))||x>=Math.ceil(w*(m.x+.1))||y<Math.floor(h*(m.y-.035))||y>=Math.ceil(h*(m.y+.07)))leakedMouth++;
        }
        // Actual opaque soles, independently measured from the rendered pixels.
        let sole=-1;
        for(let y=Math.floor(h*.82);y<h;y++)for(let x=0;x<w;x++)if(still[(y*w+x)*4+3]>=220)sole=y;
        report.push({expression,size,changedFace,changedFeet,changedBody,mouthChanges,leakedMouth,seamHoles,soleError:Math.abs(sole-h*.89)});
      }
      return report;
    });
    for(const r of results){const label=JSON.stringify(r);assert.equal(r.changedFace,0,label);assert.equal(r.changedFeet,0,label);assert.ok(r.changedBody>100,label);assert.ok(r.mouthChanges>5,label);assert.equal(r.leakedMouth,0,label);assert.equal(r.seamHoles,0,label);assert.ok(r.soleError<3,label);}
  });
  await t.test('pending expressions render the neutral texture',async()=>{
    const names=['thinking','confused','weary'];
    assert.deepEqual(await page.evaluate(names=>names.map(expression=>{rig.state({expression,activity:'thinking'});return [rig.textureKey.split(':')[0],rig.container.dataset.artwork,rig.container.getAttribute('aria-label')];}),names),names.map(name=>['neutral','revision-pending',`Chuddy · ${name} · art revision pending, neutral pose shown`]));
  });
  if(process.env.CHUDDY_REVIEW_DIR)await t.test('save review contact sheet from the actual renderer',async()=>{
    await page.evaluate(()=>{
      const contact=document.createElement('div');contact.id='contact';
      contact.style='width:900px;display:grid;grid-template-columns:repeat(3,1fr);gap:0;background:#fff0f6;color:#4c2941';
      for(const expression of ['neutral','happy','smug']){
        rig.container.style.width='418px';rig.container.style.height='627px';
        rig.state({expression,activity:'idle',reducedMotion:false});rig.draw(1800);
        const card=document.createElement('div');card.style='text-align:center;position:relative;padding:15px 0';
        const heading=document.createElement('div');heading.textContent=expression+' · reviewed raster pose';card.append(heading);
        const image=new Image();image.src=rig.canvas.toDataURL();image.style='display:block;width:300px;height:450px';card.append(image);
        for(const y of [.30,.89]){const guide=document.createElement('i');guide.style=`position:absolute;left:0;right:0;top:${34+450*y}px;border-top:1px dashed #73456066`;card.append(guide);}
        contact.append(card);
      }
      document.body.append(contact);
    });
    fs.mkdirSync(process.env.CHUDDY_REVIEW_DIR,{recursive:true});
    await page.locator('#contact').screenshot({path:path.join(process.env.CHUDDY_REVIEW_DIR,'registration.png')});
    await page.evaluate(()=>document.getElementById('contact').remove());
  });
  await t.test('offscreen/hidden stop drawing and resume without elapsed-time jumps',async()=>{
    const result=await page.evaluate(()=>{
      const step=now=>{const callbacks=[...framesQueued.values()];framesQueued.clear();callbacks.forEach(cb=>cb(now));};
      rig.state({expression:'neutral',activity:'idle'});step(100);step(140);
      intersectionCallback([{isIntersecting:false}]);const frozen=rig.elapsed;
      resizeCallback();step(10000);const stopped=framesQueued.size===0&&rig.elapsed===frozen;
      intersectionCallback([{isIntersecting:true}]);step(20000);const resumed=rig.elapsed===frozen;step(20040);
      Object.defineProperty(document,'hidden',{configurable:true,value:true});document.dispatchEvent(new Event('visibilitychange'));
      const hidden=framesQueued.size===0;
      Object.defineProperty(document,'hidden',{configurable:true,value:false});document.dispatchEvent(new Event('visibilitychange'));
      return {stopped,resumed,hidden,advance:rig.elapsed-frozen,queued:framesQueued.size};
    });
    assert.deepEqual(result,{stopped:true,resumed:true,hidden:true,advance:40,queued:1});
  });
  await t.test('OS reduced motion and local still mode stop RAF work',async()=>{
    await page.emulateMedia({reducedMotion:'reduce'});await page.waitForFunction(()=>rig.query.matches&&framesQueued.size===0,{},{polling:20});
    assert.equal(await page.evaluate(()=>rig.ticking),false);
    await page.emulateMedia({reducedMotion:'no-preference'});await page.waitForFunction(()=>rig.ticking,{},{polling:20});
    assert.equal(await page.evaluate(()=>{rig.state({activity:'speaking',reducedMotion:true});return framesQueued.size;}),0);
  });
  await t.test('DPR is capped and no-context fallback uses identical registration',async()=>{
    const result=await page.evaluate(()=>{
      Object.defineProperty(window,'devicePixelRatio',{configurable:true,value:3});
      rig.container.style.width='320px';rig.container.style.height='480px';rig.state({expression:'smug',activity:'paused'});rig.draw(0);
      const p=ChuddyRasterMath.placement('smug'),f=rig.container.querySelector('.raster-frame-fallback');
      const before=rig.canvas.width;rig.ctx=null;rig.updateLoop();
      return {before,queued:framesQueued.size,left:parseFloat(f.style.left),width:parseFloat(f.style.width),expectedLeft:p.x*320,expectedWidth:p.scale*320};
    });
    assert.equal(result.before,640);assert.equal(result.queued,0);assert.ok(Math.abs(result.left-result.expectedLeft)<.001);assert.ok(Math.abs(result.width-result.expectedWidth)<.001);
  });
  await t.test('destroy prevents late load/observer callbacks from reviving the rig',async()=>{
    assert.deepEqual(await page.evaluate(()=>{rig.destroy();intersectionCallback([{isIntersecting:true}]);resizeCallback();rig.updateLoop();return [framesQueued.size,rig.image,rig.texture];}),[0,null,null]);
    assert.deepEqual(await page.evaluate(()=>{
      const OriginalImage=window.Image;let pending;
      window.Image=class{constructor(){pending=this;}set src(value){} };
      const stage=document.createElement('div'),canvas=document.createElement('canvas');stage.append(canvas);document.body.append(stage);
      const late=new ChuddyRasterRig(canvas);late.destroy();
      pending.naturalWidth=1254;pending.naturalHeight=1254;pending.onload();pending.onerror();
      window.Image=OriginalImage;stage.remove();
      return [late.image,stage.dataset.raster||null,framesQueued.size];
    }),[null,null,0]);
  });
  assert.deepEqual(errors,[]);
});
