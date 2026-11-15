'use strict';
const test=require('node:test');
const assert=require('node:assert/strict');
const {executor}=require('../extension/commands.js');
const {matchingURL,ruleKey}=require('../extension/common.js');

function fixture(options={}){
  let time=1000;
  const rule={kind:'url',pattern:'https://docs.google.com/document/*',enabled:true};
  const config={enabled:true,clientId:'chrome-one',rules:[rule],selectedRules:[ruleKey(rule)]};
  const tab={id:31,windowId:7,active:true,url:'https://docs.google.com/document/report?secret=draft'};
  const command={id:'close-one',action:'close_tab',tab_id:31,window_id:7,url:'https://docs.google.com/document/report',client_id:'chrome-one',expires_at:9000};
  const trace=[];
  const chrome={tabs:{
    get:async id=>{trace.push(['get',id]);return {...tab};},
    query:async filter=>{trace.push(['query',filter]);return options.single?[tab]:[tab,{id:32,windowId:7}];},
    create:async values=>{trace.push(['create',values]);return {id:99,...values};},
    remove:async id=>{
      trace.push(['remove',id]);
      if(options.removeFails)throw Error('fake close failure');
      if(options.waitForClose)await options.waitForClose;
    }
  },windows:{get:async id=>({id,focused:options.focused!==false})}};
  let grant=true;
  const send=async (cfg,path,body)=>{
    trace.push(['send',body]);
    if(body.command_claim){
      if(options.expireAfterClaim)time=2000;
      if(options.disableAfterClaim)config.enabled=false;
      return {command:grant?{...command,execute:true,expires_at:1750}:null};
    }
    return {accepted:true};
  };
  return {tab,config,command,trace,run:executor(chrome,matchingURL,send,()=>time,async()=>({...config}),options.addressID),setTime:value=>time=value,setGrant:value=>grant=value};
}

test('closes only the exact active work tab and acknowledges once',async()=>{
  const f=fixture();
  assert.equal(await f.run(f.command,f.config),true);
  assert.deepEqual(f.trace.filter(row=>row[0]==='remove'),[['remove',31]]);
  const sends=f.trace.filter(row=>row[0]==='send').map(row=>row[1]);
  assert.equal(sends[0].command_claim,'close-one');
  assert.equal(sends[1].closed,true);
  assert.equal(await f.run(f.command,f.config),false);
  assert.equal(f.trace.filter(row=>row[0]==='remove').length,1);
});

test('closes a last tab with no replacement, activation or frame command',async()=>{
  const f=fixture({single:true});
  assert.equal(await f.run(f.command,f.config),true);
  assert.deepEqual(f.trace.filter(row=>['create','remove','query'].includes(row[0])),[['remove',31]]);
});

test('background, unfocused, changed, disabled, expired and wrong-instance commands never close tabs',async()=>{
  const cases=[
    f=>{f.tab.active=false;},
    f=>{f.tab.windowId=8;},
    f=>{f.tab.pendingUrl='https://example.org/';},
    f=>{f.tab.url='https://docs.google.com/document/different';},
    f=>{f.config.enabled=false;},
    f=>{f.config.selectedRules=[];},
    f=>{f.command.client_id='edge-two';},
    f=>{f.setTime(10000);}
  ];
  for(const change of cases){const f=fixture();change(f);assert.equal(await f.run(f.command,f.config),false);assert.equal(f.trace.some(row=>row[0]==='remove'),false);}
  const f=fixture({focused:false});assert.equal(await f.run(f.command,f.config),false);assert.equal(f.trace.some(row=>row[0]==='remove'),false);
});

test('denied or expired execution grants prevent late tab closure',async()=>{
  for(const options of [{},{expireAfterClaim:true},{disableAfterClaim:true}]){
    const f=fixture(options);if(!options.expireAfterClaim&&!options.disableAfterClaim)f.setGrant(false);
    assert.equal(await f.run(f.command,f.config),false);
    assert.equal(f.trace.some(row=>row[0]==='remove'),false);
    assert.equal(f.trace.at(-1)[1].closed,false);
  }
});

test('close errors acknowledge failure so casino does not claim completion',async()=>{
  const f=fixture({removeFails:true});
  assert.equal(await f.run(f.command,f.config),false);
  assert.equal(f.trace.at(-1)[1].closed,false);
});

test('no successful acknowledgement until the close actually confirms',async()=>{
  let confirm;
  const waitForClose=new Promise(resolve=>{confirm=resolve;});
  const f=fixture({waitForClose});
  const pending=f.run(f.command,f.config);
  await new Promise(resolve=>setImmediate(resolve));
  assert.deepEqual(f.trace.filter(row=>row[0]==='remove'),[['remove',31]]);
  assert.equal(f.trace.some(row=>row[0]==='send'&&row[1].command_result),false);
  confirm();
  assert.equal(await pending,true);
  assert.equal(f.trace.at(-1)[1].closed,true);
});

test('same tab and path with changed full address cannot use an old command',async()=>{
  const f=fixture({addressID:async value=>value});
  f.command.address_id=f.tab.url;
  f.tab.url='https://docs.google.com/document/report?secret=new';
  assert.equal(await f.run(f.command,f.config),false);
  assert.equal(f.trace.some(row=>row[0]==='remove'),false);
});

test('protected ChatGPT, Codex and Discord URLs cannot be reported or closed by a broad rule',async()=>{
  const f=fixture();
  f.config.rules[0].pattern='https://*';
  f.config.selectedRules=[ruleKey(f.config.rules[0])];
  for(const url of ['https://chatgpt.com/codex','https://chat.openai.com/','https://www.chatgpt.com./c/123',
    'https://discord.com/channels/example','https://www.discord.gg./fixture','https://discordapp.com/']){
    f.tab.url=url;
    assert.equal(matchingURL(f.config.rules,f.config.selectedRules,url),null);
    assert.equal(await f.run({...f.command,id:url,url},f.config),false);
  }
  assert.equal(f.trace.some(row=>row[0]==='remove'),false);
  assert.equal(matchingURL(f.config.rules,f.config.selectedRules,'https://chatgpt.com.example.test/'),'https://chatgpt.com.example.test/');
  assert.equal(matchingURL(f.config.rules,f.config.selectedRules,'https://discord.com.example.test/'),'https://discord.com.example.test/');
});
