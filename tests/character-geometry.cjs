'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const R = require('../web/character-rig.js');

test('reviewed source bytes remain unchanged', () => {
  const bytes=fs.readFileSync(path.join(__dirname,'../web/assets/chuddy/gestures.png'));
  assert.equal(crypto.createHash('sha256').update(bytes).digest('hex'),'57981ec32b50b6fb70e79d3b327428ced905fac080ca8fcc59a6879bb670521b');
});

test('ready pose registration aligns eye and sole landmarks with uniform scale', () => {
  for(const name of ['neutral','happy','smug']){
    const p=R.placement(name),m=R.LANDMARKS[name];
    assert.ok(Math.abs(p.x+p.scale*m.eye[0]/418-.5)<1e-12,name+' horizontal eye registration');
    assert.ok(Math.abs(p.y+p.scale*m.eye[1]/627-.30)<1e-12,name+' eye line');
    assert.ok(Math.abs(p.y+p.scale*m.sole/627-.89)<1e-12,name+' sole line');
    assert.ok(p.scale>.8&&p.scale<.9);
    assert.ok(p.x>0&&p.y>0&&p.x+p.scale<1&&p.y+p.scale<1,'Full cell must fit without cropping');
  }
});

test('entire non-speaking face and feet remain fixed across the motion cycle', () => {
  for(let t=0;t<=15;t+=.17)for(let x=0;x<=1000;x+=20){
    for(let y=0;y<=480;y+=20)assert.deepEqual(R.warp(x,y,1000,1000,t,1,false),{x,y});
    for(let y=820;y<=1000;y+=20)assert.deepEqual(R.warp(x,y,1000,1000,t,1,true),{x,y});
  }
});

test('voice-only jaw displacement is bounded to each registered mouth', () => {
  for(const name of ['neutral','happy','smug']){
    const m=R.MOUTHS[name];let moving=0;
    for(let y=0;y<=1000;y+=10)for(let x=0;x<=1000;x+=10){
      const idle=R.warp(x,y,1000,1000,.6,1,false,m),talk=R.warp(x,y,1000,1000,.6,1,true,m);
      if(talk.y!==idle.y){moving++;assert.ok(Math.abs(x/1000-m.x)<.091);assert.ok(y/1000>m.y-.026&&y/1000<m.y+.061);assert.ok(talk.y-idle.y<2.81);}
      assert.equal(talk.x,idle.x);
    }
    assert.ok(moving>10);
  }
});

test('deformation stays small and cannot invert the texture locally', () => {
  for(const name of ['neutral','happy','smug'])for(let t=0;t<8;t+=.4)for(let y=0;y<1000;y+=20)for(let x=0;x<1000;x+=20){
    const p=R.warp(x,y,1000,1000,t,1,true,R.MOUTHS[name]);
    const q=R.warp(x+1,y,1000,1000,t,1,true,R.MOUTHS[name]);
    const r=R.warp(x,y+1,1000,1000,t,1,true,R.MOUTHS[name]);
    assert.ok(Math.hypot(p.x-x,p.y-y)<6);
    assert.ok((q.x-p.x)*(r.y-p.y)-(q.y-p.y)*(r.x-p.x)>.8,'Local mesh folding');
  }
});
