/* No content scripts. No page contents/history. Only selected focused URLs. */
importScripts('common.js','commands.js');
const C=globalThis.ChuddyBrowser;
const defaults={base:'http://127.0.0.1:8765',token:'',enabled:false,selectedRules:[],rules:[],clientId:null};
let lastStatus={status:'Reporting is off',host:null,time:null};
let reporting=false;
let rerun=false;
let sequence=0;

async function settings(){
  const config={...defaults,...await chrome.storage.local.get(Object.keys(defaults))};
  if(!config.clientId){config.clientId=crypto.randomUUID();await chrome.storage.local.set({clientId:config.clientId});}
  return config;
}
async function desktop(config,path,body){
  const headers={'X-Chuddy-Token':config.token};
  const options={headers,cache:'no-store',signal:AbortSignal.timeout(4000)};
  if(body!==undefined){options.method='POST';headers['Content-Type']='application/json';options.body=JSON.stringify(body);}
  const response=await fetch(C.safeBase(config.base)+path,options);
  const data=await response.json();
  if(!response.ok||data.error)throw new Error(data.error||'Could not reach the desktop companion.');
  return data;
}
async function refreshRules(config){
  const result=await desktop(config,'/browser/rules');
  const rules=Array.isArray(result.rules)?result.rules.filter(row=>row&&row.kind==='url'&&typeof row.pattern==='string'):[];
  await chrome.storage.local.set({rules});
  return rules;
}
const executeCommand=globalThis.ChuddyTabCommands.executor(chrome,C.matchingURL,desktop,Date.now,settings,C.addressID);
async function report(){
  if(reporting){rerun=true;return;}
  reporting=true;
  try{
    const config=await settings();
    if(!config.token){lastStatus={status:'Pair with the desktop first',host:null,time:null};return;}
    const tabs=await chrome.tabs.query({active:true,lastFocusedWindow:true});
    const tab=tabs[0]||null;
    const focused=tab?await chrome.windows.get(tab.windowId):null;
    const body=C.reportFor(tab,focused,config,config.rules,Date.now(),crypto.randomUUID());
    body.client_id=config.clientId;
    if(body.active){
      body.close_version=2;
      body.address_id=await C.addressID(tab.url);
    }
    // Scope/data minimization is repeated here, not delegated to page content.
    const response=await desktop(config,'/browser/report',body);
    if(response.command)await executeCommand(response.command,await settings());
    const host=body.url?new URL(body.url).hostname:null;
    lastStatus={status:body.active?'Reporting selected work tab':config.enabled?'Ready · no selected focused work tab':'Reporting is off',host,time:Date.now()};
    chrome.action.setBadgeText({text:body.active?'✳':''});
    chrome.action.setBadgeBackgroundColor({color:'#d18789'});
    sequence++;
    if(sequence%12===0&&config.enabled)await refreshRules(config);
  }catch(error){lastStatus={status:'Desktop unavailable · '+String(error.message||error),host:null,time:Date.now()};chrome.action.setBadgeText({text:'!'});}
  finally{reporting=false;if(rerun){rerun=false;report();}}
}

async function clear(config){
  if(config.token)await desktop(config,'/browser/report',{active:false,focused:false,client_id:config.clientId,sent_at:Date.now(),request_id:crypto.randomUUID()}).catch(()=>{});
  lastStatus={status:'Reporting is off',host:null,time:Date.now()};
  await chrome.action.setBadgeText({text:''});
}

chrome.runtime.onInstalled.addListener(async details=>{
  if(details.reason==='install')await chrome.storage.local.set(defaults);
  await chrome.alarms.create('chuddy-focused-tab',{periodInMinutes:0.5});
});
chrome.runtime.onStartup.addListener(async()=>{await chrome.alarms.create('chuddy-focused-tab',{periodInMinutes:0.5});report();});
chrome.alarms.onAlarm.addListener(alarm=>{if(alarm.name==='chuddy-focused-tab')report();});
chrome.tabs.onActivated.addListener(()=>report());
chrome.tabs.onUpdated.addListener((id,changes,tab)=>{if(tab.active&&(changes.url||changes.status==='complete'))report();});
chrome.tabs.onRemoved.addListener(()=>report());
chrome.windows.onFocusChanged.addListener(()=>report());
// Frequent reports while the service worker is awake; alarms revive it if suspended.
setInterval(()=>report(),1000);

chrome.runtime.onMessage.addListener((message,sender,sendResponse)=>{
  if(sender.id!==chrome.runtime.id)return false;
  (async()=>{
    if(!message||typeof message.type!=='string')throw new Error('Invalid extension request.');
    const config=await settings();
    switch(message.type){
      case 'status': return {enabled:config.enabled,base:config.base,paired:Boolean(config.token),selectedRules:config.selectedRules,rules:config.rules,...lastStatus};
      case 'pair': {
        const base=C.safeBase(message.base);
        if(typeof message.token!=='string'||message.token.trim().length<20||message.token.length>500)throw new Error('Paste the browser-only token shown by the desktop.');
        const next={...config,base,token:message.token.trim(),enabled:false};
        const rules=await refreshRules(next);
        await clear(config);
        await chrome.storage.local.set({base,token:next.token,enabled:false,selectedRules:[]});
        return {paired:true,rules,status:'Paired. Choose work rules, then enable reporting.'};
      }
      case 'refresh_rules': return {rules:await refreshRules(config)};
      case 'save_rules': {
        if(!Array.isArray(message.selectedRules)||message.selectedRules.length>100||message.selectedRules.some(value=>typeof value!=='string'||value.length>500))throw new Error('Invalid rule selection.');
        const allowed=new Set(config.rules.map(C.ruleKey));
        await chrome.storage.local.set({selectedRules:message.selectedRules.filter(value=>allowed.has(value))});
        await report();
        return {ok:true};
      }
      case 'enabled': {
        if(typeof message.enabled!=='boolean')throw new Error('Choose reporting on or off.');
        if(message.enabled&&(!config.token||!config.selectedRules.length))throw new Error('Pair and choose at least one work rule first.');
        await chrome.storage.local.set({enabled:message.enabled});
        if(message.enabled){await refreshRules(config);await report();}else await clear(config);
        return {enabled:message.enabled};
      }
      case 'forget': await clear(config);await chrome.storage.local.set({...defaults});return {ok:true};
      default:throw new Error('Unknown extension action.');
    }
  })().then(sendResponse).catch(error=>sendResponse({error:String(error.message||error)}));
  return true;
});
