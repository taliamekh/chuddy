'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {position,idlePosition}=require('../web/chud-cursor.js');
const source=fs.readFileSync(path.join(__dirname,'../web/chud-cursor.js'),'utf8');

function harness(){
  let frames=new Map(),next=0,clock=0;
  const listeners=new Map();
  function element(tag){const classes=new Set();return {tag,hidden:false,children:[],dataset:{},style:{},
    attributes:{},removed:false,classes,
    setAttribute(name,value){this.attributes[name]=value;},
    appendChild(node){this.children.push(node);node.parentElement=this;},
    remove(){this.removed=true;},
    classList:{toggle(name,enabled){if(enabled)classes.add(name);else classes.delete(name);}}};}
  class Rig{
    constructor(canvas){this.canvas=canvas;this.states=[];this.destroyed=false;}
    state(value){this.states.push(value);}
    destroy(){this.destroyed=true;}
  }
  const document={hidden:false,body:element('body'),createElement:element,
    addEventListener(name,handler){listeners.set(name,handler);},
    removeEventListener(name){listeners.delete(name);}};
  const context={document,ChuddyRasterRig:Rig,innerWidth:1280,innerHeight:720,
    performance:{now:()=>clock},matchMedia:()=>({matches:false}),
    requestAnimationFrame(fn){const id=++next;frames.set(id,fn);return id;},
    cancelAnimationFrame(id){frames.delete(id);}};
  vm.runInNewContext(source,context);
  const cursor=new context.ChudCursor();
  return {context,cursor,document,listeners,frames,
    tick(now){clock=now;const batch=[...frames.values()];frames.clear();for(const fn of batch)fn(now);}};
}

test('idle Patrick never travels even after hours',()=>{
  for(const seconds of [0,.5,9,12,15,27,120,3600,86400])for(const reduced of [false,true]){
    assert.deepEqual(position(seconds,1280,720,null,reduced),position(0,1280,720,null));
    assert.deepEqual(idlePosition(seconds,reduced),{u:.88,v:.86,walking:false});
  }
  assert.deepEqual(position(0,1280,720,{x:1200,y:30}),position(0,1280,720,null));
  assert.notDeepEqual(position(0,1280,720,{x:1200,y:30}),position(1.3,1280,720,{x:1200,y:30}));
});

test('warning stays still and only its transition to approach starts travel',()=>{
  const h=harness();
  const warning={session:'running',interruption:{id:'same',phase:'warning'}};
  h.cursor.update({session:'running'});h.tick(0);
  const idle=h.cursor.node.style.transform;
  h.cursor.update(warning);h.tick(0);h.tick(8000);
  assert.equal(h.cursor.node.hidden,false);
  assert.equal(h.cursor.target,null);
  assert.equal(h.cursor.node.style.transform,idle);
  assert.equal(h.cursor.node.classes.has('is-clicking'),false);
  assert.equal(h.cursor.rig.states.at(-1).reducedMotion,true);
  h.cursor.update({...warning,interruption:{id:'same',phase:'approaching'}});h.tick(8000);
  const start=h.cursor.node.style.transform;
  assert.equal(start,idle,'Approach starts where the still warning was displayed');
  assert.equal(h.cursor.node.classes.has('is-clicking'),false,'Warning time cannot consume approach time');
  h.tick(9300);
  assert.notEqual(h.cursor.node.style.transform,start);
  h.cursor.update({...warning,interruption:{id:'same',phase:'clicking'}});h.tick(10600);
  assert.equal(h.cursor.node.classes.has('is-clicking'),true);
  h.cursor.update(warning);h.tick(10600);
  assert.equal(h.cursor.node.style.transform,idle);
  assert.equal(h.cursor.node.classes.has('is-clicking'),false);
  h.cursor.destroy();
});

test('route is bounded and reduced motion has no travel',()=>{
  for(const width of [40,800,1920])for(const height of [40,600,1080]){
    for(const seconds of [0,.5,1,9000])for(const target of [null,{x:-9999,y:-9999},{x:9999,y:9999}]){
      const point=position(seconds,width,height,target);
      assert.ok(point.x>=0&&point.x<=Math.max(0,width-112));
      assert.ok(point.y>=0&&point.y<=Math.max(0,height-168));
    }
  }
  assert.deepEqual(position(0,1280,720,{x:900,y:30},true),position(5,1280,720,{x:900,y:30},true));
});

test('cursor starts hidden and stopped/disconnected/native sessions stay hidden',()=>{
  const h=harness();
  assert.equal(h.cursor.node.hidden,true);
  for(const state of [null,{session:'stopped'},{session:'paused'},
      {session:'running',desktop_overlay:{status:'ready',enabled:true,visible:true}}]){
    h.cursor.update(state);assert.equal(h.cursor.node.hidden,true);assert.equal(h.frames.size,0);
  }
  assert.equal(h.cursor.node.attributes['aria-hidden'],'true');
  h.cursor.destroy();
});

test('initialized but hidden native overlay cannot suppress the fallback',()=>{
  const h=harness();
  for(const native of [
    {status:'ready'},
    {status:'ready',enabled:true,visible:false},
    {status:'ready',enabled:false,visible:false},
    {status:'unavailable',enabled:true,visible:true},
    {status:'starting',enabled:true,visible:false}
  ]){
    h.cursor.update({session:'running',desktop_overlay:native});
    assert.equal(h.cursor.node.hidden,false,JSON.stringify(native));
  }
  h.cursor.destroy();
});

test('fallback hands over only after native frames appear and returns after dismissal',()=>{
  const h=harness();
  const running={session:'running',desktop_overlay:{status:'ready',enabled:true,visible:false}};
  h.cursor.update(running);
  assert.equal(h.cursor.node.hidden,false);
  h.cursor.update({...running,desktop_overlay:{status:'ready',enabled:true,visible:true}});
  assert.equal(h.cursor.node.hidden,true);
  assert.equal(h.frames.size,0);
  // Completed performance stays hidden; Return to work clears that record.
  for(const phase of ['opened','preview','error']){
    h.cursor.update({...running,interruption:{id:'one',phase}});
    assert.equal(h.cursor.node.hidden,true,phase);
  }
  h.cursor.update({...running,interruption:null,desktop_overlay:{status:'ready',enabled:false,visible:false}});
  assert.equal(h.cursor.node.hidden,false);
  h.cursor.update({session:'stopped',desktop_overlay:{status:'ready',enabled:true,visible:true}});
  assert.equal(h.cursor.node.hidden,true);
  assert.equal(h.frames.size,0);
  h.cursor.destroy();
});

test('approach fallback stays visible until the native approach actually renders',()=>{
  const h=harness();
  const state={session:'running',interruption:{id:'walk',phase:'approaching'},desktop_overlay:{status:'ready',enabled:true,visible:false}};
  h.cursor.update(state);h.tick(1000);
  assert.equal(h.cursor.node.hidden,false);
  assert.match(h.cursor.node.style.transform,/translate3d/);
  h.cursor.update({...state,desktop_overlay:{status:'ready',enabled:true,visible:true}});
  assert.equal(h.cursor.node.hidden,true);
  assert.equal(h.frames.size,0);
  h.cursor.destroy();
});

test('fallback reuses the raster rig and cancels late animation after Stop',()=>{
  const h=harness();
  h.cursor.update({session:'running',voice:{speaking:true},interruption:{id:'one',phase:'approaching'}});
  assert.equal(h.cursor.node.hidden,false);
  assert.equal(h.cursor.rig.states.at(-1).activity,'speaking');
  const late=[...h.frames.values()][0];
  h.tick(500);
  assert.match(h.cursor.node.style.transform,/translate3d/);
  h.cursor.update({session:'stopped'});
  const stoppedTransform=h.cursor.node.style.transform;
  late(1200);
  assert.equal(h.frames.size,0);
  assert.equal(h.cursor.node.hidden,true);
  assert.equal(h.cursor.node.style.transform,stoppedTransform);
  h.cursor.destroy();
});

test('opened distractions hide the theatrical sprite, visibility loss cancels it',()=>{
  const h=harness();
  h.cursor.update({session:'running',interruption:{id:'one',phase:'opened'}});
  assert.equal(h.cursor.node.hidden,true);
  h.cursor.update({session:'running'});
  assert.equal(h.cursor.node.hidden,false);
  h.document.hidden=true;h.listeners.get('visibilitychange')();
  assert.equal(h.cursor.node.hidden,true);
  assert.equal(h.frames.size,0);
  h.cursor.destroy();
});

test('reduced motion draws once; destruction clears rig and listeners',()=>{
  const h=harness();
  h.cursor.options.reducedMotion=true;
  h.cursor.update({session:'running',interruption:{id:'one',phase:'clicking'}});
  h.tick(500);
  assert.equal(h.frames.size,0);
  h.cursor.options.reducedMotion=false;
  h.cursor.update({session:'running',interruption:{id:'one',phase:'clicking'}});
  assert.equal(h.frames.size,1);
  h.cursor.destroy();h.cursor.destroy();
  assert.equal(h.cursor.rig.destroyed,true);
  assert.equal(h.cursor.node.removed,true);
  assert.equal(h.listeners.size,0);
  assert.equal(h.frames.size,0);
});

test('CSS makes the character click-through and keeps reduced motion static',()=>{
  const css=fs.readFileSync(path.join(__dirname,'../web/chud-cursor.css'),'utf8');
  assert.match(css,/pointer-events:none!important/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);
});

test('static reduced-motion cursor is repositioned after a viewport resize',()=>{
  const h=harness();
  h.cursor.options.reducedMotion=true;
  h.cursor.update({session:'running'});h.tick(100);
  const before=h.cursor.node.style.transform;
  h.context.innerWidth=500;h.context.innerHeight=400;
  h.cursor.update({session:'running'});h.tick(200);
  assert.notEqual(h.cursor.node.style.transform,before);
  assert.equal(h.frames.size,0);
  h.cursor.destroy();
});
