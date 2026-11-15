/* Exact, paired work-tab closure. No tab creation, activation or window commands. */
(function(root){
  'use strict';
  function executor(chrome, matching, send, now=Date.now, readSettings=null, addressID=null){
    const handled=new Set();
    async function focused(command,config){
      if(!config.enabled||command.client_id!==config.clientId||now()>=command.expires_at)return null;
      const tab=await chrome.tabs.get(command.tab_id);
      if(tab.windowId!==command.window_id || tab.pendingUrl)return null;
      const windowInfo=await chrome.windows.get(tab.windowId);
      if(!tab.active||!windowInfo.focused||windowInfo.id!==tab.windowId||matching(config.rules,config.selectedRules,tab.url)!==command.url)return null;
      if(addressID && (!command.address_id || await addressID(tab.url)!==command.address_id))return null;
      const latest=await chrome.tabs.get(tab.id);
      return latest.id===tab.id && latest.active && latest.windowId===tab.windowId
        && !latest.pendingUrl && latest.url===tab.url ? latest : null;
    }
    return async function execute(command,config){
      if(!command||command.action!=='close_tab'||typeof command.id!=='string'||!Number.isSafeInteger(command.tab_id)||handled.has(command.id))return false;
      handled.add(command.id);
      if(handled.size>256)handled.delete(handled.values().next().value);
      let closed=false;
      try{
        if(!await focused(command,config))return false;
        const response=await send(config,'/browser/report',{command_claim:command.id,client_id:config.clientId});
        const grant=response.command;
        if(!grant||grant.id!==command.id||grant.execute!==true)return false;
        const currentConfig=readSettings?await readSettings():config;
        const tab=await focused(grant,currentConfig);
        if(!tab||now()>=grant.expires_at)return false;
        // Chromium resolves remove only when this tab's contents are destroyed.
        // A beforeunload prompt may leave it pending; keep reporting while the
        // controller times out this command and never retry it on our own.
        let timer;
        try {
          await Promise.race([chrome.tabs.remove(tab.id), new Promise((_,reject)=>{
            timer=setTimeout(()=>reject(Error('The tab did not confirm it closed.')),3500);
          })]);
        } finally {
          clearTimeout(timer);
        }
        closed=true;
        return true;
      }catch(_){return false;}
      finally{
        const result={command_result:command.id,client_id:config.clientId,closed};
        await send(config,'/browser/report',result)
          .catch(()=>send(config,'/browser/report',result)).catch(()=>{});
      }
    };
  }
  root.ChuddyTabCommands={executor};
  if(typeof module==='object'&&module.exports)module.exports={executor};
})(typeof globalThis==='object'?globalThis:this);
