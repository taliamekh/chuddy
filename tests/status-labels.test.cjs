'use strict';
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const U = require('../web/ui-utils.js');
const root = path.resolve(__dirname, '..');
const enabled = {voice: true, voice_engine: 'auto'};
const localReady = {status: 'ready', engine: 'Chatterbox turbo · cuda', configured: true, reference_ready: true, metrics: {ok: true}};
const windowsReady = {status: 'generic fallback ready', engine: 'Windows generic fallback · not Patrick', configured: false, reference_ready: false};

for (const [source, expected] of [
  ['local model', 'Text generated locally'],
  ['local model pending', 'Text: local AI pending'],
  ['offline script', 'Text: scripted reply'],
  ['offline fallback · local model error', 'Text: scripted fallback · local AI error'],
  ['offline fallback · local model unavailable', 'Text: scripted fallback · local AI unavailable'],
  ['offline rule', 'Text: scripted rule'],
  ['offline rule fallback', 'Text: scripted rule fallback'],
  ['cached local model', 'Text: cached local AI reply'],
  ['local model · cached', 'Text: cached local AI reply'],
  ['cached script', 'Text: cached scripted reply'],
  ['offline script · cached', 'Text: cached scripted reply'],
  ['cached fallback', 'Text: cached fallback reply'],
  ['cached', 'Text: cached reply · original source unavailable'],
  ['cached response', 'Text: cached reply · original source unavailable'],
  ['', 'Text source unavailable'],
  ['future provider', 'Text source: future provider'],
]) {
  test('text provenance: ' + (source || 'missing'), () => assert.equal(U.textSourceLabel(source), expected));
}

test('user input is not presented as generated text', () => {
  assert.equal(U.textSourceLabel('you', 'user'), 'You');
  assert.equal(U.textSourceLabel('local transcription', 'user'), 'Input: local transcription');
});

test('disabled reply speech and the off engine override a previously loaded engine', () => {
  for (const settings of [{}, {voice: false}, {voice: true, voice_engine: 'off'}]) {
    const result = U.voiceLabels(localReady, settings);
    assert.equal(result.summary, 'Voice: off · text replies still available');
    assert.equal(result.enabled, false);
  }
});

test('Windows output remains a fallback even if character configuration/reference exist', () => {
  for (const voice of [windowsReady, {...windowsReady, configured: true, reference_ready: true}]) {
    const result = U.voiceLabels(voice, enabled);
    assert.equal(result.summary, 'Voice: Windows system speech — character voice not loaded');
    assert.ok(result.details.includes('Reported engine: Windows generic fallback · not Patrick'));
  }
});

test('a config, reference file or engine name alone never establishes loaded character speech', () => {
  for (const engine of ['not loaded', localReady.engine]) {
    for (const status of ['off', 'idle']) {
      const result = U.voiceLabels({engine, status, configured: true, reference_ready: true}, enabled);
      assert.equal(result.summary, 'Voice: character voice configured · not loaded');
    }
  }
  assert.equal(U.voiceLabels({...localReady, reference_ready: false}, enabled).summary,
    'Voice: local voice configured · character reference missing');
  assert.equal(U.voiceLabels({}, enabled).summary, 'Voice: character voice not loaded');
});

test('loading and generating character audio are distinct from loaded and playing', () => {
  for (const status of ['warming', 'loading', 'preparing']) {
    const result = U.voiceLabels({...localReady, status, metrics: null}, enabled);
    assert.equal(result.summary, 'Voice: loading local character voice · similarity not yet reviewed');
    assert.equal(result.summary.includes('playing'), false);
  }
  assert.equal(U.voiceLabels({...localReady, status: 'generating'}, enabled).summary,
    'Voice: generating local character audio · similarity not yet reviewed');
  assert.equal(U.voiceLabels({...windowsReady, status: 'generating'}, enabled).summary,
    'Voice: preparing Windows system speech — character voice not loaded');
});

test('successful local character output still requires a similarity listening review', () => {
  for (const voice of [localReady, {...localReady, metrics: null}, {...localReady, similarity: 'exact Patrick match'}]) {
    const result = U.voiceLabels(voice, enabled);
    assert.equal(result.summary, 'Voice: local character voice · similarity not yet reviewed');
    assert.match(result.referenceNote, /not yet been reviewed by listening/);
    assert.equal(result.summary.includes('Patrick'), false);
  }
});

test('playing reports the actual output engine and does not imply a character voice for unknown/cached audio', () => {
  assert.equal(U.voiceLabels({...windowsReady, speaking: true}, enabled).summary,
    'Voice: Windows system speech — character voice not loaded · playing');
  assert.equal(U.voiceLabels({...localReady, status: 'speaking', speaking: true}, enabled).summary,
    'Voice: local character voice · similarity not yet reviewed · playing');
  assert.equal(U.voiceLabels({status: 'speaking', speaking: true, engine: 'not loaded', configured: true, reference_ready: true}, enabled).summary,
    'Voice: playing audio · character voice not confirmed');
  assert.equal(U.voiceLabels({...localReady, speaking: 'false'}, enabled).summary.includes('playing'), false);
});

test('explicit test playback remains visible when automatic reply speech is off', () => {
  assert.equal(U.voiceLabels({...windowsReady, speaking: true}, {voice: false}).summary,
    'Voice: Windows system speech — character voice not loaded · playing · automatic replies off');
});

test('errors never silently become a working Windows fallback', () => {
  const result = U.voiceLabels({...localReady, status: 'error', error: 'Worker timed out'}, enabled);
  assert.equal(result.summary, 'Voice: unavailable · check voice settings');
  assert.match(result.details, /Worker timed out/);
});

// Run the actual app with an in-memory DOM and API. No browser, device, server,
// voice output or microphone is opened by these regression tests.
async function mount(initial, clock = Date) {
  const html = fs.readFileSync(path.join(root, 'web/index.html'), 'utf8');
  const nodes = new Map();
  for (const [, id] of html.matchAll(/\bid="([^"]+)"/g)) nodes.set(id, {
    id, value: id === 'expression-preview' ? 'auto' : '', textContent: '', innerHTML: '', hidden: false,
    dataset: {}, style: {}, attributes: {}, listeners: {}, scrollHeight: 100, scrollTop: 0, clientHeight: 100,
    firstElementChild: {style: {}}, classList: {toggle() {}, add() {}, remove() {}},
    addEventListener(name, fn) { this.listeners[name] = fn; },
    setAttribute(name, value) { this.attributes[name] = value; }, removeAttribute(name) { delete this.attributes[name]; },
    appendChild() {}, focus() {},
  });
  let next = initial;
  let unavailable = false;
  let poll;
  const requests = [];
  const location = {hash: '#companion'};
  const context = vm.createContext({
    Date: clock,
    window: {
      ChuddyUI: U, CHUDDY_TOKEN: 'test-only', scrollTo() {}, addEventListener() {},
      ChuddyRasterRig: class {state() {} destroy() {}},
      ChuddyCasinoRoom: {mount: () => ({update() {},stopAnimation() {},destroy() {}})},
      ChudCursor: class {constructor(){this.options={};} update() {} destroy() {}},
      ChuddyRasterMath: {visualState: () => ({expression: 'neutral'}), ARTWORK: {neutral: {ready: true}}},
    },
    document: {getElementById: id => nodes.get(id), querySelectorAll: () => [], addEventListener() {}, createElement: () => ({remove() {}})},
    localStorage: {getItem: () => null}, location, history: {replaceState() {}},
    setInterval: fn => { poll = fn; }, setTimeout() {}, clearInterval() {},
    fetch: async (url, options) => {
      requests.push({url, options});
      if (unavailable) throw new Error('Disconnected');
      if (url === '/api/action' && options && options.body) {
        const payload = JSON.parse(options.body);
        if (payload.action === 'microphone') {
          next = {...next, microphone:{mode:'push_to_talk',active:payload.operation==='start',transcribing:false}};
        }
      }
      return {ok: true, json: async () => url === '/api/state' ? structuredClone(next) : {}};
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'web/monies.js'), 'utf8'), context);
  vm.runInContext(fs.readFileSync(path.join(root, 'web/app.js'), 'utf8'), context);
  const settle = async () => { await new Promise(resolve => setImmediate(resolve)); };
  await settle();
  assert.equal(nodes.get('connection-banner').hidden, true, 'App must finish rendering successfully.');
  return {
    nodes, requests, settle,
    async update(value) { next = value; await poll(); },
    async disconnect() { unavailable = true; await poll(); },
  };
}

test('actual app renders independent text/voice labels and updates voice without relabeling old replies', async () => {
  const data = {settings: enabled, voice: windowsReady, messages: [{id: 1, role: 'assistant', text: 'Hello', source: 'local model'}]};
  const original = structuredClone(data);
  const app = await mount(data);
  assert.match(app.nodes.get('chat-messages').innerHTML, />Text generated locally<\/div>/);
  assert.match(app.nodes.get('chat-messages').innerHTML, /title="Backend text source: local model"/);
  assert.equal(app.nodes.get('chat-provenance').textContent, 'Last reply · Text generated locally');
  assert.equal(app.nodes.get('voice-state').textContent, 'Voice: Windows system speech — character voice not loaded');
  const messages = app.nodes.get('chat-messages').innerHTML;
  for (const voice of [localReady, {...localReady, status: 'warming'}, {...localReady, speaking: true, status: 'speaking'}]) {
    await app.update({...data, voice});
    assert.equal(app.nodes.get('voice-state').textContent, U.voiceLabels(voice, enabled).summary);
    assert.equal(app.nodes.get('chat-messages').innerHTML, messages);
  }
  await app.update({...data, messages: [{...data.messages[0], source: 'offline fallback · local model unavailable'}]});
  assert.match(app.nodes.get('chat-messages').innerHTML, /Text: scripted fallback · local AI unavailable/);
  assert.deepEqual(data, original, 'Formatting must preserve raw backend provenance.');
  assert.ok(app.requests.every(request => request.url === '/api/state' && !request.options.method));
});

test('unknown backend text sources stay escaped in labels and raw provenance tooltips', async () => {
  const raw = '<img src=x onerror="bad()">';
  const app = await mount({settings: {}, messages: [{id: 1, role: 'assistant', text: 'Hello', source: raw}]});
  const rendered = app.nodes.get('chat-messages').innerHTML;
  assert.equal(rendered.includes('<img'), false);
  assert.ok(rendered.includes('Text source: &lt;img'));
  assert.ok(rendered.includes('Backend text source: &lt;img'));
});

test('mic recording keeps voice output visible in a separate status', async () => {
  const app = await mount({settings: enabled, voice: windowsReady});
  const before = app.nodes.get('voice-state').textContent;
  app.nodes.get('ptt-toggle').listeners.click();
  await app.settle();
  assert.equal(app.nodes.get('mic-state').hidden, false);
  assert.equal(app.nodes.get('voice-state').textContent, before);
  app.nodes.get('ptt-toggle').listeners.click();
  await app.settle();
  assert.equal(app.nodes.get('mic-state').hidden, true);
  assert.equal(app.nodes.get('voice-state').textContent, before);
});

test('off engine directs the speech toggle to settings without claiming speech is enabled', async () => {
  const app = await mount({settings: {voice: true, voice_engine: 'off'}, voice: localReady});
  assert.equal(app.nodes.get('voice-toggle').attributes['aria-pressed'], 'false');
  app.nodes.get('voice-toggle').listeners.click();
  await app.settle();
  assert.equal(app.nodes.get('breadcrumb').textContent, 'COMPANION STUDIO');
  assert.equal(app.requests.some(request => request.options.method === 'POST'), false);
});

test('lost desktop connection does not leave a stale ready voice status', async () => {
  const app = await mount({settings: enabled, voice: localReady});
  await app.disconnect();
  assert.equal(app.nodes.get('voice-state').textContent, 'Voice: connection lost · output status unavailable');
});

test('a failed distraction retains actual model provenance and is not a model outage', async () => {
  const data = {settings: {}, agent: {status: 'idle', ready: true,
    error: 'Observed work did not start a distraction: denied',
    action_outcome: {status: 'denied', destination: null}},
    messages: [{id: 1, role: 'assistant', text: "I couldn't start the break.", source: 'local model'}]};
  const app = await mount(data);
  assert.equal(app.nodes.get('local-detail').textContent, 'No action from local model');
  assert.equal(app.nodes.get('chat-provenance').textContent, 'Last reply · Text generated locally');
  await app.update({...data, agent: {status: 'idle', ready: false, error: 'Connection refused'}});
  assert.equal(app.nodes.get('local-detail').textContent, 'Model unavailable · fallback labeled');
});

test('model health stays separate from reply fallback and intentional AI-off', async () => {
  const data = {settings: {ai_enabled: true}, agent: {
    status: 'idle', model_status: 'ready', ready: true,
    error: 'Local reply failed the conversation check; scripted reply used.',
    source: 'scripted conversation fallback'
  }};
  const app = await mount(data);
  assert.equal(app.nodes.get('model-status').textContent, 'READY');
  assert.equal(app.nodes.get('local-detail').textContent, 'Last reply used a fallback');
  await app.update({...data, agent: {...data.agent, model_status: 'recovering', ready: false,
    model_error: 'Could not reach local Ollama. Retrying.'}});
  assert.equal(app.nodes.get('local-detail').textContent, 'Could not reach local Ollama. Retrying.');
  await app.update({settings: {ai_enabled: false}, agent: {...data.agent, model_status: 'disabled'}});
  assert.equal(app.nodes.get('local-detail').textContent, 'Local AI is off');
});

test('the upcoming card renders the saved time alongside the assignment joke', async () => {
  class Clock extends Date {
    static now() { return Date.parse('2026-11-15T20:19:00Z'); }
  }
  const saved = '2026-11-16T04:59:00+00:00';
  const app = await mount({
    settings: {}, agent: {status: 'idle'},
    messages: [{id: 1, role: 'assistant', text: "Well, I guess I missed Assignment #14. It's due tomorrow.",
      source: 'scripted assignment reply'}],
    deadlines: [
      {id: 1, title: 'Assignment #12 - Due', due: '2026-11-09T04:59:00+00:00'},
      {id: 2, title: 'Assignment #14 - Due', due: saved}
    ],
  }, Clock);
  assert.equal(app.nodes.get('next-deadline').textContent, 'Assignment #14 - Due');
  assert.equal(app.nodes.get('next-due').textContent, U.eventDate(saved));
  assert.match(app.nodes.get('chat-messages').innerHTML, /due tomorrow\./);
});
