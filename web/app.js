/* Chuddy desktop interface. All effects go through the authenticated core. */
(function () {
  'use strict';
  const $ = id => document.getElementById(id);
  const U = window.ChuddyUI;
  const esc = U.escapeHTML;
  const list = value => Array.isArray(value) ? value : [];
  let state = null;
  let connected = false;
  let initialized = false;
  let polling = false;
  let messageKey = '';
  const casinoOpeningKey = 'chuddy-last-casino-opening';
  let lastCasinoOpeningId = null;
  try { lastCasinoOpeningId = sessionStorage.getItem(casinoOpeningKey); } catch (_) { /* Storage may be unavailable. */ }
  let pttRecording = false;
  let pttCapturing = false;
  let keyboardPTT = null;
  let manualPTTBusy = false;
  let localFriends = null;
  let leaderboardPeriod = 'all_time';
  let adminRequest = null;
  let reducedMotion = false;
  try { reducedMotion = localStorage.getItem('chuddy-reduced-motion') === 'true'; } catch (_) { /* Private storage may be unavailable. */ }
  const characterRig = new window.ChuddyRasterRig($('chuddy-canvas'));
  const casinoRoom = window.ChuddyCasinoRoom.mount($('casino-room'));
  const chudCursor = new window.ChudCursor({reducedMotion});
  const pageNames = {companion:'YOUR VERY SUPPORTIVE FRIEND',rules:'A VERY GOOD INTERRUPTION',deadlines:'ACTUAL DEADLINES. QUESTIONABLE ADVICE.',leaderboard:'LEADERBOARD',settings:'COMPANION STUDIO'};

  function text(id, value) { const node = $(id); if (node) node.textContent = String(value == null ? '' : value); }
  function hidden(id, value) { const node = $(id); if (node) node.hidden = Boolean(value); }
  function number(value) { return U.safeNumber(value).toLocaleString(); }
  function requestID() { return typeof crypto.randomUUID === 'function' ? crypto.randomUUID() : Date.now().toString(36) + '-' + Math.random().toString(36).slice(2); }
  function toast(message, kind = '') {
    const node = document.createElement('div');
    node.className = 'toast ' + kind;
    node.textContent = String(message);
    $('toast-region').appendChild(node);
    setTimeout(() => node.remove(), kind === 'error' ? 8000 : 4500);
  }

  async function api(path, payload, keepalive = false) {
    const options = {headers: {'X-Chuddy-Token': window.CHUDDY_TOKEN || ''}, cache: 'no-store'};
    if (payload !== undefined) { options.method = 'POST'; options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(payload); }
    if (keepalive) options.keepalive = true;
    const response = await fetch(path, options);
    const data = await response.json().catch(() => ({error: 'The local service returned an unreadable response.'}));
    if (!response.ok || data.error) { const error = new Error(data.error || 'The local companion request failed.'); error.status = response.status; throw error; }
    return data;
  }

  async function action(name, fields = {}, button, success) {
    if (button) button.disabled = true;
    try {
      const result = await api('/api/action', {action: name, ...fields});
      if (success) toast(typeof success === 'function' ? success(result) : success, 'success');
      await refresh();
      return result;
    } catch (error) {
      toast(error.message || 'The companion could not finish that request.', 'error');
      throw error;
    } finally {
      if (button) button.disabled = false;
      if (state && connected) { renderSession(); renderHardware(state.hardware || {}); }
    }
  }

  function safely(task) { Promise.resolve().then(task).catch(() => {}); }
  function navigate(page, updateHash = true) {
    if (page === 'arcade' || page === 'wallet') page = 'casino';
    if (page !== 'casino' && !pageNames[page]) page = 'companion';
    if (page !== 'companion') cancelKeyboardPTT();
    const leavingCasino = page !== 'casino' && !$('page-casino').hidden;
    const openingId = state?.casino_opening_id ?? state?.casino_interruption_id;
    if (leavingCasino && openingId && state?.desktop_interception?.covered && openingId === lastCasinoOpeningId) {
      safely(() => action('casino_leave', {opening_id: openingId}));
    }
    for (const section of document.querySelectorAll('.page')) section.hidden = section.id !== 'page-' + page;
    for (const item of document.querySelectorAll('.nav-item')) {
      const selected = item.dataset.page === page;
      item.classList.toggle('selected', selected);
      if (selected) item.setAttribute('aria-current', 'page'); else item.removeAttribute('aria-current');
    }
    text('breadcrumb', page === 'casino' ? 'CLAM CASINO' : pageNames[page]);
    if (updateHash && location.hash !== '#' + page) history.replaceState(null, '', '#' + page);
    if (page === 'settings') safely(() => loadDevices());
    window.scrollTo({top:0,behavior:'auto'});
  }

  function renderMessages(messages) {
    const signature = messages.map(row => [row.id,row.text,row.source].join(':')).join('|');
    if (signature === messageKey) return;
    messageKey = signature;
    const container = $('chat-messages');
    const nearBottom = container.scrollHeight - container.scrollTop - container.clientHeight < 90;
    const profileName = state && state.profile ? state.profile.name : 'You';
    container.innerHTML = messages.map(row => {
      const user = row.role === 'user';
      const time = new Date(row.time);
      const stamp = Number.isFinite(time.getTime()) ? time.toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}) : '';
      return '<article class="message ' + (user ? 'user' : 'assistant') + '"><span class="message-avatar" aria-hidden="true">' + (user ? esc(String(profileName || 'Y').charAt(0).toUpperCase()) : '✳') + '</span><div class="message-main"><div class="message-header"><b>' + esc(user ? profileName : 'Chuddy') + '</b><span class="message-time">' + esc(stamp) + '</span></div><div class="message-text">' + esc(row.text) + '</div><div class="message-source" title="' + esc('Backend text source: ' + (row.source || 'unavailable')) + '">' + esc(U.textSourceLabel(row.source, row.role)) + '</div></div></article>';
    }).join('');
    if (nearBottom || messages.length <= 2) container.scrollTop = container.scrollHeight;
    const last = [...messages].reverse().find(row => row.role === 'assistant');
    if (last) {
      text('chat-provenance', 'Last reply · ' + U.textSourceLabel(last.source));
      $('chat-provenance').title = 'Backend text source: ' + (last.source || 'unavailable');
    }
  }

  function renderAgent(agent, voice) {
    const status = U.shortStatus(agent.status || 'unavailable');
    const busy = /thinking|generating|working|running|loading|responding|tool|preparing|warming/i.test(status);
    const modelStatus = agent.model_status;
    let detail = '';
    if (modelStatus === 'disabled' || state.settings?.ai_enabled === false) {
      detail = 'Local AI is off';
    } else if (modelStatus === 'recovering' || modelStatus === 'waiting') {
      detail = agent.model_error || 'Retrying the local model…';
    } else if (modelStatus === 'checking' || modelStatus === 'warming') {
      detail = agent.model_error || 'Preparing the local brain…';
    } else if (agent.error && agent.ready) {
      detail = agent.action_outcome ? 'No action from local model' :
        /fallback/.test(agent.source || '') ? 'Last reply used a fallback' : 'Last turn reported an error';
    } else if (agent.error) {
      detail = 'Model unavailable · fallback labeled';
    } else if (busy) {
      detail = 'Thinking…';
    } else if (modelStatus === 'ready') {
      detail = 'Local model ready';
    }
    text('agent-badge', status.toUpperCase());
    text('model-status', (modelStatus ? U.shortStatus(modelStatus) : status).toUpperCase());
    text('local-label', agent.model ? 'Local brain: ' + agent.model : 'Local companion');
    text('local-detail', detail);
    $('local-dot').style.background = modelStatus ?
      modelStatus === 'ready' && connected ? '#adb981' : modelStatus === 'recovering' ? '#cf9b91' : '#bfb1b4' :
      agent.error ? '#cf9b91' : connected ? '#adb981' : '#bfb1b4';
    hidden('agent-draft', !busy && !agent.draft);
    text('draft-text', agent.draft || (status.includes('loading') ? 'Preparing the local brain…' : 'Ummm… Chuddy is thinking.'));
    hidden('turn-cancel', !busy);
    const lastAssistant = [...list(state.messages)].reverse().find(row => row.role === 'assistant');
    const visual=window.ChuddyRasterMath.visualState({agentStatus:status,agentError:agent.error,session:state.session,
      voiceSpeaking:voice.speaking,listening:pttCapturing,expressionOverride:$('expression-preview').value,
      lastText:lastAssistant&&lastAssistant.text,reducedMotion});
    characterRig.state(visual);
    const artPending=!window.ChuddyRasterMath.ARTWORK[visual.expression].ready;
    text('character-art-status',artPending?U.shortStatus(visual.expression)+' pose · art revision pending':'');
    hidden('character-art-status',!artPending);
    text('character-activity', voice.speaking ? 'Saying something very important' : busy ? 'Thinking. At his own pace.' : '');
    text('agent-error', agent.model_error || agent.error || '');
    const trace = list(agent.trace);
    $('agent-trace').innerHTML = trace.length ? trace.map((row, index) => {
      if (typeof row === 'string') return '<div class="trace-row">' + esc(row) + '</div>';
      const label = row.tool || row.name || row.type || row.action || 'Decision ' + (index + 1);
      const content = row.result !== undefined ? row.result : row.arguments !== undefined ? row.arguments : row;
      return '<div class="trace-row"><b>' + esc(label) + '</b><small>' + esc(typeof content === 'string' ? content : JSON.stringify(content)) + '</small></div>';
    }).join('') : 'No actions.';
    const metrics = agent.metrics || {};
    const entries = Object.entries(metrics).filter(([,value]) => value !== null && value !== undefined).slice(0, 8);
    text('agent-metrics', entries.map(([key,value]) => U.shortStatus(key) + ': ' + (typeof value === 'object' ? JSON.stringify(value) : value)).join('\n'));
    const voiceStatus = U.voiceLabels(voice, state.settings);
    text('voice-state', voiceStatus.summary);
    $('voice-state').title = voiceStatus.details;
    text('voice-details', voiceStatus.details);
    text('voice-reference-note', voiceStatus.referenceNote);
    hidden('mic-state', !pttRecording);
    $('voice-toggle').setAttribute('aria-pressed', String(voiceStatus.enabled));
    $('voice-toggle').title = voiceStatus.enabled ? 'Mute spoken replies' : 'Enable spoken replies';
    text('hardware-caption', String((state.hardware || {}).mode || 'SIMULATOR').toUpperCase() + ' · SPRAY ' + String((state.hardware || {}).spray || 'disabled').toUpperCase());
  }

  function renderDeadlines(rows) {
    text('deadline-count', rows.length);
    const dateOnly = row => /^\d{4}-\d{2}-\d{2}$/.test(row.calendar_date || '');
    const overdueDate = row => {
      if (!dateOnly(row)) return Date.parse(row.due) < Date.now();
      const parts = Object.fromEntries(new Intl.DateTimeFormat('en-CA',{timeZone:row.calendar_timezone || 'America/Toronto',year:'numeric',month:'2-digit',day:'2-digit'}).formatToParts(new Date()).map(part => [part.type,part.value]));
      return [parts.year,parts.month,parts.day].join('-') > row.calendar_date;
    };
    const deadlineDate = row => dateOnly(row) ? new Date(row.calendar_date + 'T12:00:00Z').toLocaleDateString([], {year:'numeric',month:'short',day:'numeric',timeZone:'UTC'}) + ' · all day' : U.eventDate(row.due);
    const upcoming = rows.find(row => !overdueDate(row)) || rows[0];
    text('next-deadline', upcoming ? upcoming.title : 'No upcoming deadline');
    text('next-due', upcoming ? deadlineDate(upcoming) + (overdueDate(upcoming) ? ' · overdue' : '') : '');
    $('deadlines-list').innerHTML = rows.length ? rows.map(row => {
      const onlyDate = dateOnly(row);
      const date = new Date(onlyDate ? row.calendar_date + 'T12:00:00Z' : row.due);
      const valid = Number.isFinite(date.getTime());
      const overdue = valid && overdueDate(row);
      return '<article class="deadline-item ' + (overdue ? 'overdue' : '') + '"><div class="deadline-date"><span>' + esc(valid ? date.toLocaleDateString([], {month:'short', ...(onlyDate ? {timeZone:'UTC'} : {})}) : '—') + '</span><b>' + esc(valid ? (onlyDate ? date.getUTCDate() : date.getDate()) : '?') + '</b></div><div class="deadline-info"><b>' + esc(row.title) + '</b><small>' + esc(deadlineDate(row)) + (overdue ? ' · Overdue' : '') + '</small></div><button class="delete-button" data-delete-deadline="' + esc(row.id) + '" aria-label="Delete deadline ' + esc(row.title) + '">Remove</button></article>';
    }).join('') : '<div class="empty-state">No deadlines.</div>';
  }

  function ranking(rows, current) {
    // An older friends service may still return its combined ranking order.
    const ranked = [...rows].sort((a, b) => U.safeNumber(b.fart) - U.safeNumber(a.fart)
      || String(a.name || '').localeCompare(String(b.name || ''), undefined, {sensitivity:'base'})
      || String(a.id).localeCompare(String(b.id)));
    return ranked.length ? ranked.map((row,index) => '<div class="ranking-row ' + (String(row.id) === String(current) ? 'current' : '') + '"><span class="rank">' + (index + 1).toString().padStart(2,'0') + '</span><span class="avatar">' + esc(String(row.name || '?').charAt(0).toUpperCase()) + '</span><div class="ranking-name">' + esc(row.name || 'Anonymous clam') + '</div><div class="ranking-score">' + number(row.fart) + '<small>FARTS</small></div></div>').join('') : '<div class="empty-state">No scores.</div>';
  }

  function renderScores(scores) {
    const recent = list(scores.recent_farts || scores.recent).filter(row => row.kind === 'fart');
    $('score-events').innerHTML = recent.length ? recent.map(row => '<div class="event-row"><span class="event-icon">≋</span><div><b>fart</b><small>' + esc(row.source || 'source unavailable') + (row.confidence != null ? ' · confidence ' + U.safeNumber(row.confidence).toFixed(2) : '') + '</small></div><time>' + esc(U.eventDate(row.time)) + '</time></div>').join('') : '<div class="empty-state">No farts logged.</div>';
    const profile = state.profile || {id:'',name:state.settings.name || 'You'};
    text('profile-name', profile.name);
    text('profile-initial', String(profile.name || 'You').charAt(0).toUpperCase());
    const periods = state.leaderboard_periods || {};
    const rows = list(periods[leaderboardPeriod] || (leaderboardPeriod === 'all_time' ? state.leaderboard : []));
    $('leaderboard-list').innerHTML = ranking(rows, profile.id);
    const own = rows.find(row => row.id === profile.id) || {};
    text('score-total', number(own.fart ?? (leaderboardPeriod === 'all_time' ? scores.fart : 0)));
    text('leaderboard-period-note','');
    for (const button of document.querySelectorAll('[data-leaderboard-period]')) button.setAttribute('aria-pressed', String(button.dataset.leaderboardPeriod === leaderboardPeriod));
    const profiles = list(state.profiles);
    const signature = profiles.map(row => row.id + ':' + row.name).join('|');
    if ($('profile-select').dataset.signature !== signature) {
      $('profile-select').innerHTML = profiles.map(row => '<option value="' + esc(row.id) + '">' + esc(row.name) + '</option>').join('');
      $('profile-select').dataset.signature = signature;
    }
    $('profile-select').value = String(profile.id);
    const remote = localFriends || state.friends || {};
    const friendsRows = list((remote.leaderboard_periods || {})[leaderboardPeriod] || (leaderboardPeriod === 'all_time' ? remote.leaderboard || state.friends_leaderboard : []));
    $('friends-ranking').innerHTML = friendsRows.length ? ranking(friendsRows, profile.id) : '<span class="muted">' + (leaderboardPeriod !== 'all_time' && remote.status ? 'Sync friends to update this period.' : 'No friends connected') + '</span>';
    text('friends-status', remote.status ? U.shortStatus(remote.status).toUpperCase() : state.settings.friends_url ? 'CONFIGURED · SYNC TO CONNECT' : 'NOT CONNECTED');
  }

  function renderSessionScoreboard(scores, earnings) {
    text('session-farts', scores ? number(scores.fart) : '—');
    text('session-boost', window.ChuddyMonies.boost(earnings?.passive));
  }

  function renderMemory(rows) {
    $('memory-list').innerHTML = rows.length ? rows.map(row => '<div class="memory-row"><div><b>' + esc(row.key || 'Saved preference') + '</b><p>' + esc(typeof row.value === 'string' ? row.value : JSON.stringify(row.value)) + '</p></div><button class="delete-button" data-delete-memory="' + esc(row.id) + '">Forget</button></div>').join('') : '<p class="muted">No saved memories.</p>';
  }

  function renderHardware(hw) {
    text('hardware-mode', U.shortStatus(hw.mode || 'simulator'));
    text('hardware-yaw', U.safeNumber(hw.yaw) + '°');
    text('hardware-phase', U.shortStatus(hw.phase || 'idle'));
    text('hardware-spray', U.shortStatus(hw.spray || 'disabled'));
    $('mini-character').style.transform = 'rotateY(' + Math.max(-180,Math.min(180,U.safeNumber(hw.yaw))) + 'deg)';
    const active = hw.phase && !/idle|ready|stopped/i.test(hw.phase);
    $('simulate-belly').disabled = Boolean(active);
    $('belly-button').disabled = Boolean(active);
    const bridge=state.pi_bridge||{};
    const connectedPi=bridge.connected===true;
    const piMicrophone=bridge.microphone===true;
    text('pi-bridge-status', connectedPi ? 'Connected · '+U.shortStatus(bridge.status||'reading Pi events') : 'Disconnected · '+U.shortStatus(bridge.status||'no Pi events collected'));
    if(bridge.error)text('pi-bridge-status','Pi connection: '+bridge.error);
    $('pi-connect').disabled=connectedPi;
    $('pi-disconnect').disabled=!connectedPi;
    $('pi-mic-start').disabled=!connectedPi;
    hidden('pi-mic-start',piMicrophone);
    hidden('pi-mic-stop',!piMicrophone);
    text('pi-mic-status',bridge.microphone_error?'Pi microphone: '+bridge.microphone_error:piMicrophone?'Pi microphone requested':'Pi microphone off');
    const recent=list(bridge.recent);
    text('pi-bridge-events',recent.length?recent.map(row=>typeof row==='string'?row:JSON.stringify(row)).join('\n'):'No events received.');
  }

  function renderAudio(audio) {
    const recording = Boolean(audio.recording);
    const percent = U.clampPercent(U.safeNumber(audio.peak) * 100);
    $('audio-meter').setAttribute('aria-valuenow', String(Math.round(percent)));
    $('audio-meter').firstElementChild.style.width = percent + '%';
    text('audio-status', U.shortStatus(audio.status || 'off'));
    text('classifier-status', typeof audio.classifier === 'string' ? audio.classifier : audio.classifier ? JSON.stringify(audio.classifier) : 'Classifier unavailable');
    text('mic-badge', recording ? '● RECORDING' : 'OFF');
    $('mic-badge').className = 'pill ' + (recording ? 'pink' : 'neutral');
    hidden('mic-start', recording);
    hidden('mic-stop', !recording);
  }

  function renderSession() {
    const status = typeof state.session === 'string' ? state.session : (state.session || {}).status || 'stopped';
    const running = status === 'running';
    const paused = status === 'paused';
    const caption = running ? 'Chudding active' : paused ? 'Session paused' : 'Session stopped';
    $('session-state').className = 'session-state ' + status;
    $('session-state').innerHTML = '<span class="status-dot"></span>' + esc(caption);
    $('session-start').innerHTML = (paused ? 'Resume session' : 'Start chudding') + ' <span>↗</span>';
    $('session-start').hidden = running;
    $('session-start').disabled = running || !connected;
    $('test-trigger').disabled = !running || U.safeNumber(state.cooldown_remaining) > 0;
    $('test-trigger').title = running ? state.cooldown_remaining > 0 ? 'Cooldown: ' + state.cooldown_remaining + ' seconds' : 'Trigger a labeled test work event' : 'Start a session first';
    const observed = state.work_detection || {};
    const off = state.monitoring && state.monitoring.desktop_enabled === false;
    const reasons = {excluded_gaming:'Gaming',model_gaming:'Gaming',excluded_social:'Social media',model_social:'Social media',excluded_streaming:'Streaming',excluded_shopping:'Shopping',model_shopping:'Shopping',model_streaming:'Streaming',disabled_rule:'Excluded',protected_app:'Protected app'};
    text('desktop-interception-status',off ? 'Work detection is off.' : !running ? paused ? 'Paused' : 'Stopped' : observed.is_work ? 'Work detected' : reasons[observed.reason] || 'Watching');
    const interruption = state.interruption;
    const reportedOpeningId = state.casino_opening_id ?? state.casino_interruption_id ??
      (['opened','preview'].includes(interruption?.phase) &&
       ['casino','game'].includes(interruption?.destination) ? interruption.id : null);
    if (running && reportedOpeningId && reportedOpeningId !== lastCasinoOpeningId) {
      // The same opening may remain in state after Back and across reloads.
      lastCasinoOpeningId = reportedOpeningId;
      try { sessionStorage.setItem(casinoOpeningKey, lastCasinoOpeningId); } catch (_) { /* In-memory tracking still applies. */ }
      navigate('casino');
    }
    const tabControl = state.browser_tab_control;
    if (running && interruption?.phase === 'error') text('desktop-interception-status', interruption.error_detail || interruption.error || 'Interruption failed.');
    else if (running && interruption?.phase === 'opening') text('desktop-interception-status','Opening destination…');
    else if (running && observed.is_work && tabControl && tabControl.available === false && tabControl.reason) text('desktop-interception-status',tabControl.reason);
  }

  function setInitialForms(settings) {
    const fields = {'cooldown':'cooldown','distraction-destination':'distraction_destination','ai-endpoint':'ai_endpoint','ai-model':'ai_model','voice-engine':'voice_engine','microphone-mode':'microphone_mode','audio-threshold':'audio_threshold','pi-url':'pi_url','friends-url':'friends_url','friends-room':'friends_room'};
    for (const [id,key] of Object.entries(fields)) if (settings[key] !== undefined && settings[key] !== null) $(id).value = settings[key];
    $('ai-enabled').checked = settings.ai_enabled !== false;
    $('ai-work-detection').checked = settings.ai_work_detection !== false;
    $('voice-enabled').checked = Boolean(settings.voice);
    $('beer-joke').checked = Boolean(settings.beer_joke);
    $('audio-device').dataset.desired = settings.audio_device == null ? '' : String(settings.audio_device);
    $('pi-token').placeholder = state.has_pi_token ? 'Saved · leave blank to keep' : 'Not configured';
    $('friends-token').placeholder = state.has_friends_token ? 'Saved · leave blank to keep' : 'Not configured';
    renderRules(list(settings.rules));
  }

  function renderRules(rules) {
    $('rules-list').innerHTML = '';
    rules.forEach(addRule);
  }

  function addRule(rule = {kind:'url',pattern:'https://example.com/*',label:'My work site',enabled:true}) {
    const row = document.createElement('div');
    row.className = 'rule-row';
    row.innerHTML = '<input class="rule-enabled" type="checkbox" aria-label="Enable rule" ' + (rule.enabled ? 'checked' : '') + '><select class="rule-kind" aria-label="Rule type"><option value="url">URL</option><option value="process">Process</option><option value="window">Window</option></select><div class="rule-fields"><input class="rule-label" aria-label="Rule label" maxlength="250" value="' + esc(rule.label) + '" placeholder="Friendly name"><input class="rule-pattern" aria-label="Rule pattern" maxlength="250" value="' + esc(rule.pattern) + '" placeholder="Pattern with * wildcards"></div><button class="rule-remove" aria-label="Remove this rule">×</button>';
    row.querySelector('.rule-kind').value = rule.kind;
    row.querySelector('.rule-remove').addEventListener('click', () => row.remove());
    $('rules-list').appendChild(row);
  }

  function gatherRules() {
    return [...document.querySelectorAll('.rule-row')].map(row => ({kind:row.querySelector('.rule-kind').value,pattern:row.querySelector('.rule-pattern').value.trim(),label:row.querySelector('.rule-label').value.trim(),enabled:row.querySelector('.rule-enabled').checked}));
  }

  async function loadDevices() {
    try {
      const result = await api('/api/devices');
      const devices = Array.isArray(result) ? result : list(result.devices);
      const selected = $('audio-device').value || $('audio-device').dataset.desired || '';
      $('audio-device').innerHTML = '<option value="">System default</option>' + devices.map(device => '<option value="' + esc(device.id) + '">' + esc(device.name) + '</option>').join('');
      $('audio-device').value = selected;
    } catch (error) { toast('Input devices unavailable: ' + error.message, 'error'); }
  }

  document.addEventListener('chuddy:casino-wallet', () => safely(refresh));
  document.addEventListener('chuddy:casino-navigate', event => {
    const destination = event.detail && event.detail.destination;
    if (destination === 'wallet' || destination === 'admin') openAdmin(); else navigate(destination);
  });
  document.addEventListener('chuddy:admin', openAdmin);

  function openAdmin() {
    cancelKeyboardPTT();
    hidden('admin-console',false);
    $('admin-command').focus();
  }

  function changeSession(operation, button, success) {
    if (operation === 'pause') { casinoRoom.stopAnimation(); chudCursor.update(null); setPTT(false); }
    return action('session',{operation},button,success);
  }

  function renderCalendars(info) {
    for (const provider of ['google','brightspace']) {
      const source = list(info && info.sources).find(row => row.provider === provider);
      text('calendar-' + provider + '-status', !source || !source.connected ? 'Not connected' : source.error || (source.status + ' · ' + number(source.count) + ' deadlines' + (source.last_sync ? ' · ' + U.eventDate(source.last_sync) : '')));
    }
  }

  async function refresh() {
    if (polling) return;
    polling = true;
    try {
      const next = await api('/api/state');
      state = next;
      state.settings = state.settings || {};
      connected = true;
      syncKeyboardPTT();
      chudCursor.options.reducedMotion = reducedMotion;
      chudCursor.update(state);
      hidden('connection-banner', true);
      if (!initialized) { setInitialForms(state.settings); initialized = true; }
      renderMessages(list(state.messages));
      renderAgent(state.agent || {}, state.voice || {});
      renderDeadlines(list(state.deadlines));
      renderScores(state.scores || {});
      renderSessionScoreboard(state.scores, state.earnings);
      renderMemory(list(state.memory));
      renderHardware(state.hardware || {});
      renderAudio(state.audio || {});
      renderSession();
      text('summary-balance', number(state.balance));
      renderCalendars(state.deadline_sync);
      casinoRoom.update({...state,connected:true,reducedMotion});
      text('monies-balance', number(state.balance) + ' monies');
      text('monies-rate', window.ChuddyMonies.status(state));
      text('browser-status', state.browser ? '● Extension active · ' + (state.browser.host || 'focused tab') : 'Not connected');
      text('real-clock', 'Real time: ' + new Date(state.real_time || Date.now()).toLocaleTimeString([], {hour:'numeric',minute:'2-digit'}));
      for (const row of list(state.catalog)) {
        const button = document.querySelector('[data-shop="' + CSS.escape(String(row.id)) + '"]');
        if (button) { button.title = String(row.description || 'Fictional item'); const span = button.querySelector('span'); if (span) span.textContent = '◒ ' + number(row.price); }
      }
      renderMicrophone(state.microphone);
    } catch (error) {
      connected = false;
      cancelKeyboardPTT();
      text('monies-rate', 'Disconnected');
      renderSessionScoreboard();
      $('ptt-toggle').disabled = true;
      chudCursor.update(null);
      casinoRoom.update({...state,connected:false,reducedMotion});
      hidden('connection-banner', false);
      $('session-start').disabled = true;
      text('local-label', 'Desktop connection lost');
      text('local-detail', 'Reconnecting to the local service');
      text('voice-state', 'Voice: connection lost · output status unavailable');
      text('voice-details', 'Desktop connection lost. Voice engine status cannot be refreshed.');
      $('voice-state').title = 'Desktop connection lost. Voice engine status cannot be refreshed.';
      $('local-dot').style.background = '#cf9b91';
    } finally { polling = false; }
  }

  function setPTT(active, mode = 'push_to_talk', transcribing = false, capturing = active) {
    pttRecording = active;
    pttCapturing = capturing;
    const button = $('ptt-toggle');
    button.setAttribute('aria-pressed', String(active));
    const label = transcribing && mode === 'push_to_talk' ? 'Transcribing voice message' : active ? mode === 'always_listening' ? 'Turn off microphone' : 'Finish and send voice message' : 'Turn on microphone';
    button.title = mode === 'push_to_talk' && !active && !transcribing ? label + ' · Hold numpad + to talk' : label;
    button.setAttribute('aria-label', label);
    button.disabled = !connected || manualPTTBusy || Boolean(keyboardPTT) || (transcribing && mode === 'push_to_talk');
    button.classList.toggle('microphone-active',capturing);
    text('mic-state',transcribing ? 'Transcribing locally…' : !capturing ? 'Waiting for microphone audio…' : mode === 'always_listening' ? 'Microphone on · Always listening' : 'Microphone on · Push to talk');
    hidden('mic-state', !active && !transcribing);
  }

  function microphoneMode() {
    return state?.microphone?.mode || state?.settings?.microphone_mode || 'push_to_talk';
  }

  function renderMicrophone(info) {
    const mode = info?.mode || microphoneMode();
    setPTT(Boolean(info?.active), mode, Boolean(info?.transcribing), Boolean(info?.capturing ?? info?.active));
    if (info && info.error) { text('mic-state',info.error); hidden('mic-state',false); }
  }

  function holdRequest(hold, operation, fields = {}) {
    return api('/api/action', {action:'microphone', operation, hold_id:hold.id, ...fields}, operation === 'hold_cancel');
  }

  async function retireKeyboardPTT(hold) {
    if (keyboardPTT !== hold) return;
    keyboardPTT = null;
    await refresh();
    if (connected) renderMicrophone(state?.microphone);
  }

  function cancelKeyboardPTT() {
    const hold = keyboardPTT;
    if (!hold || hold.cancelling) return;
    hold.cancelled = true;
    hold.cancelling = true;
    holdRequest(hold, 'hold_cancel')
      .then(() => retireKeyboardPTT(hold))
      .catch(() => {}) // The server expires the lease if cancellation cannot reach it.
      .finally(() => { hold.cancelling = false; });
  }

  function syncKeyboardPTT() {
    const hold = keyboardPTT;
    if (!hold) return;
    if (hold.cancelled || state.profile?.id !== hold.profile || state.session !== hold.session
        || microphoneMode() !== 'push_to_talk' || state.settings.audio_device !== hold.device
        || (hold.generation !== null && state.microphone?.generation > hold.generation)) {
      cancelKeyboardPTT();
      return;
    }
    if (hold.generation === null || hold.renewing) return;
    hold.renewing = true;
    holdRequest(hold, 'hold_keepalive').then(result => {
      if (result.cancelled && !hold.finishing && keyboardPTT === hold) cancelKeyboardPTT();
    }).catch(() => {
      if (keyboardPTT === hold) cancelKeyboardPTT();
    }).finally(() => { hold.renewing = false; });
  }

  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') cancelKeyboardPTT();
    if (event.code !== 'NumpadAdd' || event.defaultPrevented || event.isComposing
        || event.ctrlKey || event.altKey || event.metaKey || event.shiftKey
        || !connected || !document.hasFocus() || document.hidden || $('page-companion').hidden
        || !$('admin-console').hidden || microphoneMode() !== 'push_to_talk') return;
    const target = event.target;
    if (target !== $('chat-input') && (target.isContentEditable || target.closest?.('input,textarea,select'))) return;
    if (keyboardPTT) { event.preventDefault(); return; }
    if (event.repeat || manualPTTBusy || pttRecording || state.microphone?.transcribing
        || !Number.isInteger(state.microphone?.generation) || !state.profile?.id) return;
    event.preventDefault();
    const hold = keyboardPTT = {id:requestID(), generation:null, initialGeneration:state.microphone.generation,
                                profile:state.profile.id, session:state.session, device:state.settings.audio_device};
    renderMicrophone(state.microphone);
    holdRequest(hold, 'hold_start', {generation:hold.initialGeneration, profile_id:hold.profile}).then(result => {
      if (keyboardPTT !== hold || hold.cancelled) return;
      if (result.cancelled) {
        if (!hold.finishing) retireKeyboardPTT(hold);
        return;
      }
      hold.generation = result.generation;
      if (!hold.finishing) renderMicrophone(result);
    }).catch(error => {
      if (keyboardPTT !== hold || hold.cancelled) return;
      toast(error.message, 'error');
      cancelKeyboardPTT();
    });
  });

  document.addEventListener('keyup', event => {
    const hold = keyboardPTT;
    if (event.code !== 'NumpadAdd' || !hold || hold.cancelled || hold.finishing) return;
    event.preventDefault();
    hold.finishing = true;
    holdRequest(hold, 'hold_finish').then(result => {
      if (keyboardPTT !== hold || hold.cancelled) return;
      if (!result.cancelled) toast(result.text ? 'Heard: ' + result.text : 'No speech was transcribed.');
      return retireKeyboardPTT(hold);
    }).catch(error => {
      if (keyboardPTT !== hold || hold.cancelled) return;
      toast(error.message, 'error');
      cancelKeyboardPTT();
    });
  });

  window.addEventListener('blur', cancelKeyboardPTT);
  window.addEventListener('pagehide', cancelKeyboardPTT);
  document.addEventListener('visibilitychange', () => { if (document.hidden) cancelKeyboardPTT(); });

  document.addEventListener('click', event => {
    const period = event.target.closest('[data-leaderboard-period]');
    if (period) { leaderboardPeriod = period.dataset.leaderboardPeriod; if (state) renderScores(state.scores || {}); return; }
    const disconnect = event.target.closest('[data-calendar-disconnect]');
    if (disconnect) safely(() => action('deadline_disconnect',{provider:disconnect.dataset.calendarDisconnect,profile_id:state.profile.id},disconnect));
    const nav = event.target.closest('[data-page]');
    if (nav) { event.preventDefault(); navigate(nav.dataset.page); return; }
    const shop = event.target.closest('[data-shop]');
    if (shop) safely(() => action('shop',{item:shop.dataset.shop,request_id:requestID()},shop,result => 'Pretend purchase complete. ' + number(result.balance) + ' monies left.'));
    const sound = event.target.closest('[data-sound]');
    if (sound) safely(async () => {
      const result = await action('sound_test',{kind:sound.dataset.sound},sound);
      if (result.accepted) toast('Sound test queued.', 'success');
    });
    const score = event.target.closest('[data-score]');
    if (score) safely(() => action('score',{kind:score.dataset.score,request_id:requestID()},score,'Manual event added. The source label is preserved.'));
    const deadline = event.target.closest('[data-delete-deadline]');
    if (deadline) safely(() => action('deadline_delete',{id:deadline.dataset.deleteDeadline},deadline,'Deadline removed from Chuddy.'));
    const memory = event.target.closest('[data-delete-memory]');
    if (memory) safely(() => action('memory_delete',{id:memory.dataset.deleteMemory},memory,'Local preference forgotten.'));
    const fictional = event.target.closest('[data-fictional]');
    if (fictional) safely(async () => { navigate('companion'); await action('chat',{text:'For our fictional pretend-bank game, my imaginary pet is ' + fictional.dataset.fictional + '. This is a made-up answer.'},fictional); });
  });

  $('session-start').addEventListener('click', () => safely(() => action('session',{operation:state && state.session === 'paused' ? 'resume' : 'start'},$('session-start'),'Chudding started.')));
  $('test-trigger').addEventListener('click', () => safely(() => action('test_trigger',{},$('test-trigger'),'Test work observation sent to Chuddy.')));
  $('turn-cancel').addEventListener('click', () => safely(() => action('cancel_turn',{},$('turn-cancel'),'Current response cancelled.')));
  $('voice-toggle').addEventListener('click', () => safely(async () => {
    if (state && state.settings.voice_engine === 'off') { navigate('settings'); $('voice-engine').focus(); toast('Choose a voice engine in Companion studio to enable spoken replies.'); return; }
    const voice = !(state && state.settings.voice);
    await action('settings',{values:{voice}},$('voice-toggle'));
    $('voice-enabled').checked = voice;
    toast(voice ? 'Reply speech enabled. Check the voice engine status below the message box.' : 'Reply speech muted.');
  }));
  $('ptt-toggle').addEventListener('click', () => safely(async () => {
    if (manualPTTBusy || keyboardPTT) return;
    const mode = microphoneMode();
    const operation = pttRecording ? mode === 'always_listening' ? 'stop' : 'finish' : 'start';
    manualPTTBusy = true;
    $('ptt-toggle').disabled = true;
    try {
      const result = await action('microphone',{operation});
      if (operation === 'finish' && !result.cancelled) toast(result.text ? 'Heard: ' + result.text : 'No speech was transcribed.');
    } finally {
      manualPTTBusy = false;
      renderMicrophone(state?.microphone);
    }
  }));
  $('chat-form').addEventListener('submit', event => {
    event.preventDefault();
    safely(async () => { const input = $('chat-input'); const value = input.value.trim(); if (!value) return; input.value = ''; input.style.height=''; await action('chat',{text:value},event.submitter); $('chat-messages').scrollTop = $('chat-messages').scrollHeight; });
  });
  $('chat-input').addEventListener('keydown', event => { if (event.key === 'Enter' && !event.shiftKey && !event.isComposing) { event.preventDefault(); $('chat-form').requestSubmit(); } });
  $('chat-input').addEventListener('input', () => { $('chat-input').style.height = 'auto'; $('chat-input').style.height = Math.min(100,$('chat-input').scrollHeight) + 'px'; });
  $('add-rule').addEventListener('click', () => { addRule(); $('rules-list').lastElementChild.querySelector('.rule-label').focus(); });
  $('save-rules').addEventListener('click', () => safely(() => action('settings',{values:{rules:gatherRules()}},$('save-rules'),'Work rules saved.')));
  $('session-settings-form').addEventListener('submit', event => { event.preventDefault(); safely(() => action('settings',{values:{distraction_destination:$('distraction-destination').value,cooldown:Number($('cooldown').value),ai_work_detection:$('ai-work-detection').checked}},event.submitter,'Session choices saved.')); });
  for (const form of document.querySelectorAll('.calendar-connect-form')) form.addEventListener('submit', event => {
    event.preventDefault();
    safely(async () => { const input = form.elements.url; await action('deadline_connect',{provider:form.dataset.provider,url:input.value.trim(),profile_id:state.profile.id},event.submitter); input.value=''; });
  });
  $('deadline-refresh').addEventListener('click', () => safely(() => action('deadline_refresh',{profile_id:state.profile.id},$('deadline-refresh'))));
  $('admin-command').addEventListener('input', () => { adminRequest = null; text('admin-result',''); });
  $('admin-console').addEventListener('submit', event => {
    event.preventDefault();
    safely(async () => {
      const command = $('admin-command').value.trim();
      if (command.toLowerCase() === 'stop') {
        const result = await changeSession('pause',event.submitter);
        text('admin-result',result.session === 'paused' ? 'Chuddy paused.' : 'Session stopped.');
      } else {
        if (!/^monies [1-9]\d*$/.test(command)) { text('admin-result','Use stop or monies followed by a positive whole amount.'); return; }
        if (!adminRequest || adminRequest.command !== command || adminRequest.profile_id !== state.profile.id) adminRequest = {command,request_id:requestID(),profile_id:state.profile.id};
        const result = await action('admin_cheat',adminRequest,event.submitter);
        text('admin-result',number(result.amount) + ' monies added · balance ' + number(result.balance));
      }
      $('admin-command').value=''; adminRequest=null;
    });
  });
  $('profile-form').addEventListener('submit', event => { event.preventDefault(); safely(async () => { const result = await action('profile_add',{name:$('new-profile-name').value.trim()},event.submitter); const profile = result.profile || result; if (profile.id !== undefined) await action('profile_select',{id:profile.id}); $('profile-form').reset(); messageKey = ''; toast('Local profile added.','success'); }); });
  $('profile-select').addEventListener('change', () => safely(async () => { await action('profile_select',{id:$('profile-select').value}); messageKey = ''; localFriends = null; await refresh(); }));
  $('friends-sync').addEventListener('click', () => safely(async () => { const result = await action('friends_sync',{},$('friends-sync')); localFriends = result; renderScores(state.scores || {}); toast('Friends sync: ' + U.shortStatus(result.status || 'complete'),'success'); }));
  $('mic-start').addEventListener('click', () => safely(() => action('mic',{operation:'start'},$('mic-start'),'Microphone requested. Recording status is shown here.')));
  $('mic-stop').addEventListener('click', () => safely(() => action('mic',{operation:'stop'},$('mic-stop'),'Microphone stopped.')));
  $('model-form').addEventListener('submit', event => { event.preventDefault(); safely(() => action('settings',{values:{ai_enabled:$('ai-enabled').checked,ai_endpoint:$('ai-endpoint').value.trim(),ai_model:$('ai-model').value.trim()}},event.submitter,'Local model choices saved.')); });
  $('model-warm').addEventListener('click', () => safely(() => action('model_warm',{},$('model-warm'),'Preparing the local model. Readiness is shown above.')));
  $('voice-form').addEventListener('submit', event => { event.preventDefault(); safely(() => action('settings',{values:{voice:$('voice-enabled').checked,voice_engine:$('voice-engine').value,beer_joke:$('beer-joke').checked}},event.submitter,'Voice choices saved.')); });
  $('reduced-motion').checked = reducedMotion;
  $('reduced-motion').addEventListener('change', () => { reducedMotion=$('reduced-motion').checked; try { localStorage.setItem('chuddy-reduced-motion',String(reducedMotion)); } catch (_) { /* Local preference still applies this session. */ } chudCursor.options.reducedMotion=reducedMotion; if(state){renderAgent(state.agent||{},state.voice||{});chudCursor.update(state);} safely(() => action('settings',{values:{reduced_motion:reducedMotion}})); });
  $('expression-preview').addEventListener('change', () => { if(state)renderAgent(state.agent||{},state.voice||{}); });
  $('voice-test').addEventListener('click', () => safely(() => action('voice_test',{},$('voice-test'),'Voice test requested. Engine status is shown above.')));
  $('refresh-devices').addEventListener('click', () => safely(() => loadDevices()));
  $('audio-device').addEventListener('change', () => { $('audio-device').dataset.desired = $('audio-device').value; });
  $('audio-form').addEventListener('submit', event => { event.preventDefault(); safely(() => action('settings',{values:{audio_device:$('audio-device').value === '' ? null : Number($('audio-device').value),audio_threshold:Number($('audio-threshold').value),microphone_mode:$('microphone-mode').value}},event.submitter,'Audio settings saved. The microphone stays user-operated.')); });
  for (const id of ['belly-button','simulate-belly']) $(id).addEventListener('click', () => safely(() => action('belly',{},$(id))));
  $('pi-form').addEventListener('submit', event => { event.preventDefault(); safely(async () => { await action('settings',{values:{pi_url:$('pi-url').value.trim(),pi_token:$('pi-token').value.trim()}},event.submitter,'Pi connection saved. Physical outputs remain controlled by the Pi.'); $('pi-token').value=''; }); });
  $('pi-status').addEventListener('click', () => safely(async () => { const result = await action('pi_status',{},$('pi-status')); text('pi-result',JSON.stringify(result,null,2)); hidden('pi-result',false); }));
  $('pi-belly').addEventListener('click', () => safely(async () => { const result = await action('pi_belly',{},$('pi-belly')); text('pi-result',JSON.stringify(result,null,2)); hidden('pi-result',false); }));
  $('pi-connect').addEventListener('click', () => safely(() => action('pi_connect',{},$('pi-connect'),'Pi event connection requested. Outputs remain controlled by the Pi.')));
  $('pi-disconnect').addEventListener('click', () => safely(() => action('pi_disconnect',{},$('pi-disconnect'),'Pi event connection closed.')));
  $('pi-mic-start').addEventListener('click', () => safely(() => action('pi_mic',{operation:'start'},$('pi-mic-start'),'Pi microphone requested. Check its status above.')));
  $('pi-mic-stop').addEventListener('click', () => safely(() => action('pi_mic',{operation:'stop'},$('pi-mic-stop'),'Pi microphone stop requested.')));
  $('friends-form').addEventListener('submit', event => { event.preventDefault(); safely(async () => { await action('settings',{values:{friends_url:$('friends-url').value.trim(),friends_room:$('friends-room').value.trim(),friends_token:$('friends-token').value.trim()}},event.submitter,'Friends connection saved. Use Sync friends to share score records.'); $('friends-token').value=''; localFriends=null; }); });
  $('open-pairing').addEventListener('click', () => safely(async () => { if (!$('pairing-panel').hidden) { hidden('pairing-panel',true); $('pairing-token').value=''; return; } const result = await action('pairing',{},$('open-pairing')); $('pairing-token').value=result.token || result.browser_token || ''; $('pairing-address').value=result.url || location.origin; hidden('pairing-panel',false); }));
  $('copy-pairing').addEventListener('click', () => safely(async () => { try { await navigator.clipboard.writeText($('pairing-token').value); toast('Browser-only pairing token copied.'); } catch (_) { $('pairing-token').type='text'; $('pairing-token').select(); toast('Copy the selected token with Ctrl+C.'); } }));

  document.addEventListener('keydown', event => {
    if (event.ctrlKey && !event.altKey && event.key.toLowerCase() === 's') {
      event.preventDefault();
      if (!event.repeat) {
        if ($('admin-console').hidden) openAdmin();
        else hidden('admin-console',true);
      }
    }
    if (event.key === 'Escape' && !$('admin-console').hidden) { event.preventDefault(); hidden('admin-console',true); }
    if (event.ctrlKey && event.altKey && event.key === 'Escape') { event.preventDefault(); safely(() => action('session',{operation:'stop'})); setPTT(false); }
  });
  window.addEventListener('hashchange', () => navigate(location.hash.slice(1),false));
  window.addEventListener('beforeunload', () => { characterRig.destroy(); casinoRoom.destroy(); chudCursor.destroy(); });
  navigate(location.hash.slice(1) || 'companion',false);
  refresh();
  setInterval(refresh,650);
})();
