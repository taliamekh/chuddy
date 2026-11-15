/* Pure matching and validation shared by the worker and its contract tests. */
(function (root) {
  'use strict';
  const protectedSites = ['chatgpt.com', 'chat.openai.com', 'discord.com', 'discordapp.com', 'discord.gg'];
  function safeBase(value) {
    const parsed = new URL(String(value));
    if (parsed.protocol !== 'http:' || !['127.0.0.1','localhost'].includes(parsed.hostname) || parsed.username || parsed.password || !['','/'].includes(parsed.pathname) || parsed.search || parsed.hash) throw new Error('Use a local desktop address, such as http://127.0.0.1:8765.');
    return parsed.origin;
  }
  function stripURL(value) {
    try { const parsed = new URL(value); if (!['https:','http:'].includes(parsed.protocol) || parsed.username || parsed.password) return null; parsed.search=''; parsed.hash=''; return parsed.href; }
    catch (_) { return null; }
  }
  function glob(pattern, value) {
    if (typeof pattern !== 'string' || pattern.length > 250) return false;
    const expression = pattern.replace(/[.+^${}()|[\]\\]/g,'\\$&').replace(/\*/g,'.*').replace(/\?/g,'.');
    return new RegExp('^'+expression+'$','i').test(value);
  }
  function ruleKey(rule) { return String(rule.kind) + ':' + String(rule.pattern); }
  function matchingURL(rules, selected, value) {
    const clean = stripURL(value);
    if (!clean) return null;
    const host = new URL(clean).hostname.replace(/\.$/, '');
    if (protectedSites.some(site => host === site || host.endsWith('.' + site))) return null;
    if ((rules || []).some(rule => rule && !rule.enabled && rule.kind === 'url' && glob(rule.pattern,clean))) return null;
    const chosen = new Set(selected || []);
    return (rules || []).find(rule => rule && rule.enabled && rule.kind === 'url' && chosen.has(ruleKey(rule)) && glob(rule.pattern,clean)) ? clean : null;
  }
  function reportFor(tab, windowInfo, settings, rules, timestamp, id) {
    if (!settings.enabled || !tab || tab.active !== true || !windowInfo || windowInfo.focused !== true || windowInfo.id !== tab.windowId) return {active:false,focused:false,sent_at:timestamp,request_id:id};
    const url = matchingURL(rules,settings.selectedRules,tab.url);
    return url ? {url,active:true,focused:true,sent_at:timestamp,request_id:id,...(Number.isSafeInteger(tab.id)&&tab.id>=0?{tab_id:tab.id,window_id:tab.windowId}:{})} : {active:false,focused:false,sent_at:timestamp,request_id:id};
  }
  async function addressID(value) {
    const bytes = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
    return Array.from(new Uint8Array(bytes), byte => byte.toString(16).padStart(2,'0')).join('');
  }
  const api={safeBase,stripURL,glob,ruleKey,matchingURL,reportFor,addressID};
  root.ChuddyBrowser=api;
  if (typeof module==='object' && module.exports) module.exports=api;
})(typeof globalThis==='object'?globalThis:this);
