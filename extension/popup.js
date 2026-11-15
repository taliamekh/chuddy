(function(){
  'use strict';
  const $=id=>document.getElementById(id);
  const C=globalThis.ChuddyBrowser;
  function message(value,error=false){$('message').textContent=String(value||'');$('message').style.color=error?'#b47176':'#9a9b70';}
  async function send(type,fields={}){const result=await chrome.runtime.sendMessage({type,...fields});if(result.error)throw new Error(result.error);return result;}
  function renderRules(rules,selected=[]){
    $('rules').textContent='';
    for(const row of rules){
      const label=document.createElement('label');label.className='rule';
      const checkbox=document.createElement('input');checkbox.type='checkbox';checkbox.value=C.ruleKey(row);checkbox.checked=selected.includes(checkbox.value);checkbox.disabled=!row.enabled;
      const content=document.createElement('span');const title=document.createElement('b');title.textContent=row.label+(row.enabled?'':' (disabled on desktop)');const pattern=document.createElement('small');pattern.textContent=row.pattern;content.append(title,pattern);label.append(checkbox,content);$('rules').append(label);
    }
    if(!rules.length){const note=document.createElement('p');note.className='note';note.textContent='No URL rules loaded. Pair the desktop or add URL rules there.';$('rules').append(note);}
  }
  async function refresh(updateRules=true){
    const state=await send('status');
    $('enabled').checked=Boolean(state.enabled);$('enabled').disabled=!state.paired;
    $('base').value=state.base||'http://127.0.0.1:8765';
    $('status').textContent=state.status||'Reporting is off';
    $('status-detail').textContent=state.host?'Focused tab: '+state.host:state.paired?'Paired · only selected URL rules':'Pair with your Chuddy desktop to begin.';
    $('status-dot').style.background=state.enabled?'#b0bb7e':'#c6b6ae';
    if(updateRules)renderRules(state.rules||[],state.selectedRules||[]);
    if(!state.paired)$('pairing').open=true;
    return state;
  }
  function run(task){Promise.resolve().then(task).catch(error=>message(error.message||'The request failed.',true));}
  $('pair-form').addEventListener('submit',event=>{event.preventDefault();run(async()=>{const button=event.submitter;button.disabled=true;try{await send('pair',{base:$('base').value,token:$('token').value});$('token').value='';await refresh();message('Paired. Select work rules, save, then enable reporting.');}finally{button.disabled=false;}});});
  $('enabled').addEventListener('change',()=>run(async()=>{try{await send('enabled',{enabled:$('enabled').checked});await refresh(false);message($('enabled').checked?'Reporting enabled for chosen work tabs.':'Reporting stopped.');}catch(error){$('enabled').checked=false;throw error;}}));
  $('save-rules').addEventListener('click',()=>run(async()=>{const selectedRules=[...document.querySelectorAll('#rules input:checked:not(:disabled)')].map(input=>input.value);await send('save_rules',{selectedRules});message(selectedRules.length+' work rules selected.');await refresh(false);}));
  $('refresh').addEventListener('click',()=>run(async()=>{await send('refresh_rules');await refresh();message('Desktop URL rules refreshed.');}));
  $('forget').addEventListener('click',()=>run(async()=>{await send('forget');await refresh();message('Reporting stopped and the saved token was removed.');}));
  run(()=>refresh());
})();
