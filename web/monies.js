/* Present server-owned amounts and rates; never synthesize money in the browser. */
(function(root) {
  'use strict';
  root.ChuddyMonies = {
    boost(passive) {
      if (!passive || passive.status === 'error') return '—';
      if (passive.status === 'stopped' || !(passive.boost_remaining_ms > 0)) return 'Inactive';
      const seconds = Math.ceil(passive.boost_remaining_ms / 1000);
      const hours = Math.floor(seconds / 3600);
      const minutes = Math.floor(seconds / 60) % 60;
      const remainder = String(seconds % 60).padStart(2, '0');
      const time = hours ? hours + ':' + String(minutes).padStart(2, '0') + ':' + remainder : minutes + ':' + remainder;
      return passive.multiplier + '× · ' + time;
    },
    status(state, connected = true) {
      if (!connected) return 'Disconnected';
      const p = state.earnings && state.earnings.passive;
      if (!p) return 'Rate unavailable';
      if (p.error) return p.error;
      if (p.status === 'wallet_full') return 'Wallet full · 0 monies/s';
      if (p.status === 'work_detected') return 'Work detected · 0 monies/s';
      if (p.status !== 'earning') return (state.session === 'paused' ? 'Paused' : 'Stopped') + ' · 0 monies/s';
      return p.rate_per_second + ' monies/s · ' + p.multiplier + '×' +
        (p.boost_remaining_ms > 0 ? ' · ' + Math.ceil(p.boost_remaining_ms/1000) + 's' : '') +
        (state.earnings.daily_remaining === 0 ? ' · score bonus cap reached' : '');
    }
  };
})(typeof window !== 'undefined' ? window : globalThis);
