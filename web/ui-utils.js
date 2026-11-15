(function (root) {
  'use strict';
  function escapeHTML(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  }
  function clampPercent(value) {
    const n = Number(value);
    return Number.isFinite(n) ? Math.max(0, Math.min(100, n)) : 0;
  }
  function deadlineItems(text) {
    if (text.length > 120000) throw new Error('Choose a JSON file smaller than 120 KB.');
    const items = JSON.parse(text);
    if (!Array.isArray(items) || items.length > 200) throw new Error('Use an array of up to 200 deadlines.');
    for (const row of items) {
      if (!row || typeof row.title !== 'string' || !row.title.trim() || row.title.length > 160 || typeof row.due !== 'string' || !/(Z|[+-]\d{2}:\d{2})$/i.test(row.due) || !Number.isFinite(Date.parse(row.due))) {
        throw new Error('Each deadline needs a title and an ISO date with a time zone. Nothing was imported.');
      }
    }
    return items.map(row => ({title: row.title.trim(), due: row.due}));
  }
  function shortStatus(value) { return String(value || 'unavailable').replace(/[_-]/g, ' '); }
  // These labels describe text provenance only. Never infer it from the voice engine.
  function textSourceLabel(source, role = 'assistant') {
    const raw = String(source || '').trim();
    if (role === 'user') return raw && raw !== 'you' ? 'Input: ' + raw : 'You';
    const labels = {
      'local model': 'Text generated locally',
      'local model pending': 'Text: local AI pending',
      'offline script': 'Text: scripted reply',
      'offline fallback · local model error': 'Text: scripted fallback · local AI error',
      'offline fallback · local model unavailable': 'Text: scripted fallback · local AI unavailable',
      'offline rule': 'Text: scripted rule',
      'offline rule fallback': 'Text: scripted rule fallback',
      'cached local model': 'Text: cached local AI reply',
      'local model · cached': 'Text: cached local AI reply',
      'cached script': 'Text: cached scripted reply',
      'offline script · cached': 'Text: cached scripted reply',
      'cached fallback': 'Text: cached fallback reply',
      'cached': 'Text: cached reply · original source unavailable',
      'cached response': 'Text: cached reply · original source unavailable'
    };
    return Object.prototype.hasOwnProperty.call(labels, raw) ? labels[raw] : raw ? 'Text source: ' + raw : 'Text source unavailable';
  }
  function voiceLabels(voice = {}, settings = {}) {
    const engine = String(voice.engine || 'unavailable');
    const status = String(voice.status || 'unavailable');
    const selected = settings.voice_engine || 'auto';
    const enabled = settings.voice === true && selected !== 'off';
    const speaking = voice.speaking === true;
    const windows = /windows/i.test(engine);
    const local = /^chatterbox\b|^reference-conditioned local voice$/i.test(engine);
    const reference = voice.reference_ready === true;
    const configured = voice.configured === true;
    const loading = /^(warming|loading|preparing)$/i.test(status);
    const generating = status === 'generating';
    const failed = status === 'error' || Boolean(voice.error);
    // The adapter names Chatterbox before its first RPC succeeds. A name or
    // reference file alone is not evidence of a loaded, conditioned voice.
    const ready = local && (status === 'ready' || (voice.metrics || {}).ok === true);
    const character = ready && reference;
    let summary;
    if (speaking) {
      summary = windows ? 'Voice: Windows system speech — character voice not loaded · playing'
        : character ? 'Voice: local character voice · similarity not yet reviewed · playing'
        : 'Voice: playing audio · character voice not confirmed';
      if (!enabled) summary += ' · automatic replies off';
    } else if (!enabled) {
      summary = 'Voice: off · text replies still available';
    } else if (failed) {
      summary = 'Voice: unavailable · check voice settings';
    } else if (loading || generating) {
      const systemSpeech = selected === 'windows' || (!configured && windows);
      summary = systemSpeech ? 'Voice: preparing Windows system speech — character voice not loaded'
        : reference && (configured || local) ? 'Voice: ' + (loading ? 'loading local character voice' : 'generating local character audio') + ' · similarity not yet reviewed'
        : 'Voice: preparing audio · character voice not confirmed';
    } else if (windows) {
      summary = 'Voice: Windows system speech — character voice not loaded';
    } else if (character) {
      summary = 'Voice: local character voice · similarity not yet reviewed';
    } else if (configured || local) {
      summary = !reference ? 'Voice: local voice configured · character reference missing'
        : 'Voice: character voice configured · not loaded';
    } else if (selected === 'windows') {
      summary = 'Voice: Windows system speech selected · not loaded';
    } else {
      summary = 'Voice: character voice not loaded';
    }
    return {
      summary, enabled,
      details: 'Output status: ' + status + ' · Reported engine: ' + engine + ' · Automatic reply speech: ' + (enabled ? 'on' : 'off') + ' · Selected engine: ' + selected + (voice.error ? ' · Error: ' + voice.error : ''),
      referenceNote: reference
        ? 'A character reference is available. Similarity has not yet been reviewed by listening. A reference file alone does not confirm that the character voice is loaded.'
        : 'A character voice reference is not available. Windows system speech is a generic fallback. Character similarity has not yet been reviewed.'
    };
  }
  function eventDate(value) {
    const date = new Date(value);
    return Number.isFinite(date.getTime()) ? date.toLocaleString([], {month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}) : 'Date unavailable';
  }
  function safeNumber(value, fallback = 0) { const n = Number(value); return Number.isFinite(n) ? n : fallback; }
  const api = {escapeHTML, clampPercent, deadlineItems, shortStatus, textSourceLabel, voiceLabels, eventDate, safeNumber};
  root.ChuddyUI = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis === 'object' ? globalThis : this);
