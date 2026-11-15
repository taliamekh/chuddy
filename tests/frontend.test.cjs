'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const U = require('../web/ui-utils.js');
const B = require('../extension/common.js');
const R = require('../web/character-rig.js');
const root = path.resolve(__dirname,'..');

test('model, user and attribute content cannot become HTML', () => {
  const content = '<img src=x onerror="alert(1)"><script>bad()</script> & \'quote\'';
  const escaped = U.escapeHTML(content);
  assert.equal(escaped.includes('<'),false);
  assert.equal(escaped.includes('"'),false);
  assert.equal(escaped.includes("'"),false);
  assert.ok(escaped.includes('&lt;script&gt;'));
  assert.equal(U.escapeHTML(null),'');
});

test('deadline import preserves qualified real dates and trims only titles', () => {
  const date = '2026-11-07T23:59:00-05:00';
  assert.deepEqual(U.deadlineItems(JSON.stringify([{title:' Report ',due:date,ignored:'x'}])),[{title:'Report',due:date}]);
});

test('deadline import rejects an entire batch when one row lacks timezone', () => {
  assert.throws(() => U.deadlineItems(JSON.stringify([{title:'Good',due:'2026-11-07T23:59:00Z'},{title:'Bad',due:'2026-11-08T23:59:00'}])),/time zone/);
  assert.throws(() => U.deadlineItems('{"title":"Not an array"}'),/array/);
  assert.throws(() => U.deadlineItems(JSON.stringify([{title:'',due:'2026-11-07T23:59:00Z'}])),/title/);
  assert.throws(() => U.deadlineItems(' '.repeat(120001)),/120 KB/);
});

test('untrusted meter values stay finite and bounded', () => {
  assert.equal(U.clampPercent(Infinity),0);
  assert.equal(U.clampPercent(-12),0);
  assert.equal(U.clampPercent(140),100);
  assert.equal(U.clampPercent('35'),35);
});

test('extension desktop address is strictly loopback without credentials or paths', () => {
  assert.equal(B.safeBase('http://127.0.0.1:8765/'),'http://127.0.0.1:8765');
  assert.equal(B.safeBase('http://localhost:8765'),'http://localhost:8765');
  for (const candidate of ['https://example.com','http://127.0.0.1.evil.example:8765','http://user:password@127.0.0.1:8765','file:///a','http://127.0.0.1:8765/path','http://127.0.0.1:8765/?key=secret','http://192.168.1.2:8765']) assert.throws(() => B.safeBase(candidate));
});

test('extension strips query strings/fragments and rejects privileged pages', () => {
  assert.equal(B.stripURL('https://docs.google.com/document/d/example/edit?token=secret#heading'),'https://docs.google.com/document/d/example/edit');
  for(const url of ['chrome://settings','edge://extensions','file:///C:/report.txt','javascript:alert(1)','https://user:pass@example.com/'])assert.equal(B.stripURL(url),null);
});

const rule={kind:'url',pattern:'https://docs.google.com/document/*',label:'Docs',enabled:true};
const settings={enabled:true,selectedRules:[B.ruleKey(rule)]};
const tab={url:'https://docs.google.com/document/d/report/edit?secret=hello#draft',active:true,windowId:7};
const focused={id:7,focused:true};

test('extension sends only explicitly selected enabled matching URL rules', () => {
  const body=B.reportFor(tab,focused,settings,[rule],12345,'event-id');
  assert.deepEqual(body,{url:'https://docs.google.com/document/d/report/edit',active:true,focused:true,sent_at:12345,request_id:'event-id'});
  assert.equal(B.matchingURL([{...rule,enabled:false}],settings.selectedRules,tab.url),null);
  assert.equal(B.matchingURL([rule],[],tab.url),null);
  assert.equal(B.matchingURL([rule],settings.selectedRules,'https://example.com/'),null);
});

test('background tabs, unfocused windows, mismatched windows and disabled reporting clear without URL', () => {
  const cases=[
    [{...tab,active:false},focused,settings],
    [tab,{...focused,focused:false},settings],
    [tab,{...focused,id:8},settings],
    [tab,focused,{...settings,enabled:false}],
    [null,focused,settings],
    [tab,null,settings]
  ];
  for(const args of cases){const body=B.reportFor(...args,[rule],999,'clear');assert.equal(body.active,false);assert.equal(body.focused,false);assert.equal('url' in body,false);assert.equal(body.sent_at,999);}
});

test('two selected tabs with the same URL retain separate ephemeral identities', () => {
  const first=B.reportFor({...tab,id:12},focused,settings,[rule],12345,'one');
  const second=B.reportFor({...tab,id:13},focused,settings,[rule],12346,'two');
  assert.equal(first.url,second.url);
  assert.notEqual(first.tab_id,second.tab_id);
  const cleared=B.reportFor({...tab,id:12}, {...focused,focused:false},settings,[rule],12347,'clear');
  assert.equal('tab_id' in cleared,false);
  assert.equal('url' in cleared,false);
});

test('URL pattern dots and regex metacharacters are literals', () => {
  assert.equal(B.glob('https://docs.google.com/*','https://docs.google.com/document/a'),true);
  assert.equal(B.glob('https://docs.google.com/*','https://docsXgoogleXcom/document/a'),false);
  assert.equal(B.glob('https://example.com/(work)/*','https://example.com/(work)/a'),true);
  assert.equal(B.glob('https://example.com/(work)/*','https://example.com/work/a'),false);
});

test('extension manifest requests no page injection or external service hosts', () => {
  const manifest=JSON.parse(fs.readFileSync(path.join(root,'extension/manifest.json'),'utf8'));
  assert.equal(manifest.manifest_version,3);
  assert.equal(manifest.content_scripts,undefined);
  assert.equal(manifest.permissions.includes('history'),false);
  assert.deepEqual(manifest.host_permissions,['http://127.0.0.1/*','http://localhost/*']);
});

test('all literal DOM bindings resolve and every page navigation has a page', () => {
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
  const js=fs.readFileSync(path.join(root,'web/app.js'),'utf8');
  const ids=[...html.matchAll(/\bid="([^"]+)"/g)].map(match=>match[1]);
  assert.equal(new Set(ids).size,ids.length,'Duplicate HTML IDs break live updates.');
  for(const match of js.matchAll(/\$\('([^']+)'\)/g))assert.ok(ids.includes(match[1]),'Missing DOM target '+match[1]);
  for(const match of html.matchAll(/data-page="([^"]+)"/g))assert.ok(ids.includes('page-'+match[1]),'Missing navigation view '+match[1]);
  assert.equal(html.includes('id="session-stop"'),false);
  assert.equal(html.includes('id="interruption-overlay"'),false);
  assert.equal(html.includes('id="page-arcade"'),false);
  assert.ok(html.includes('id="admin-console"'));
  assert.ok(html.includes('id="mic-stop"'));
});

test('frontend contains no paid, remote font, script or image dependencies', () => {
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
  for(const match of html.matchAll(/(?:src|href)="([^"]+)"/g))assert.ok(!/^https?:\/\//.test(match[1]),'Remote resource '+match[1]);
});

test('browser JavaScript parses before native-window integration', () => {
  for(const name of ['web/ui-utils.js','web/character-rig.js','web/app.js','extension/common.js','extension/worker.js','extension/popup.js'])assert.doesNotThrow(()=>new vm.Script(fs.readFileSync(path.join(root,name),'utf8'),{filename:name}));
});

test('raster sheet defines six poses and preserves the generated RGBA source', () => {
  assert.deepEqual(R.EXPRESSIONS,['neutral','thinking','confused','happy','smug','weary']);
  assert.equal(new Set(R.EXPRESSIONS.map(value=>JSON.stringify(R.POSES[value]))).size,6);
  assert.deepEqual(R.pose('unknown'),{name:'neutral',column:0,row:0});
  const html=fs.readFileSync(path.join(root,'web/index.html'),'utf8');
  assert.equal(html.includes('/web/chuddy.svg'),false);
  assert.ok(html.includes('id="chuddy-canvas"'));
  const image=fs.readFileSync(path.join(root,'web/assets/chuddy/gestures.png'));
  assert.equal(image.readUInt32BE(16),1254);
  assert.equal(image.readUInt32BE(20),1254);
  assert.equal(image[25],6,'The reviewed PNG must retain its RGBA color type.');
});

test('uncorrected frown cheeks never appear as ready runtime expressions', () => {
  for(const name of ['thinking','confused','weary']){
    assert.equal(R.ARTWORK[name].ready,false);
    assert.equal(R.pose(name).name,'neutral');
    assert.equal(R.visualState({expressionOverride:name}).expression,name,'Logical expression must remain visible even when artwork falls back.');
  }
  for(const name of ['neutral','happy','smug']){assert.equal(R.ARTWORK[name].ready,true);assert.equal(R.pose(name).name,name);}
});

test('eyes and feet remain anchored while belly and outer arms move subtly', () => {
  for(const seconds of [0,.5,1,2,4,8]){
    for(const [x,y] of [[440,280],[560,280],[500,950],[500,850]])assert.deepEqual(R.warp(x,y,1000,1000,seconds,1,false),{x,y});
  }
  const arm=R.warp(180,550,1000,1000,1,1,false);
  const belly=R.warp(580,600,1000,1000,1,1,false);
  assert.ok(Math.abs(arm.x-180)>0.1);
  assert.ok(Math.abs(belly.y-600)>0.1);
  assert.ok(Math.abs(arm.x-180)<10);
  assert.ok(Math.abs(belly.y-600)<10);
});

test('thinking and visual previews cannot masquerade as voice playback', () => {
  assert.equal(R.visualState({agentStatus:'thinking',voiceSpeaking:false}).activity,'thinking');
  assert.equal(R.visualState({agentStatus:'ready',voiceSpeaking:'false',expressionOverride:'happy'}).activity,'idle');
  assert.equal(R.visualState({agentStatus:'ready',voiceSpeaking:true}).activity,'speaking');
  assert.equal(R.visualState({agentStatus:'loading',expressionOverride:'smug'}).expression,'smug');
  assert.equal(R.visualState({agentError:'unavailable'}).expression,'confused');
  assert.equal(R.visualState({session:'paused'}).expression,'weary');
});

test('reduced motion and pause disable every mesh displacement', () => {
  assert.equal(R.amount('speaking',true),0);
  assert.equal(R.amount('paused',false),0);
  assert.deepEqual(R.warp(180,550,1000,1000,1,R.amount('speaking',true),true),{x:180,y:550});
  assert.deepEqual(R.warp(500,420,1000,1000,1,0,true),{x:500,y:420});
});
