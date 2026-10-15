/* Shared rule parsing. Exposed globally for browser content script and in Node for tests. */
(function(root) {
  'use strict';
  function parseRules(text) {
    const result = [];
    const seen = new Set();
    const lines = String(text || '').split(/\r?\n/);
    if (lines.length > 100) throw Error('Maximum 100 keep rules.');
    for (let i = 0; i < lines.length; i++) {
      const raw = lines[i].trim();
      if (!raw || raw.startsWith('#')) continue;
      let query;
      let description;
      if (/^(?:[a-z\d._%+-]+)@(?:[a-z\d-]+\.)+[a-z]{2,}$/i.test(raw)) {
        // A conversation involving this address, regardless of direction.
        query = `{from:${raw} to:${raw} cc:${raw} bcc:${raw}}`;
        description = `Address: ${raw}`;
      } else if (/^@(?:[a-z\d-]+\.)+[a-z]{2,}$/i.test(raw)) {
        query = `{from:${raw} to:${raw} cc:${raw} bcc:${raw}}`;
        description = `Domain: ${raw}`;
      } else if (/^query\s*:/i.test(raw)) {
        query = raw.replace(/^query\s*:/i, '').trim();
        description = `Search: ${query}`;
      } else if (/^(?:from|to|cc|bcc|subject|label|is|has|in|category|after|before|older|newer|filename|larger|smaller):/i.test(raw) || raw.startsWith('{') || raw.startsWith('(')) {
        query = raw;
        description = `Search: ${raw}`;
      } else if (/^"[^"\r\n]+"$/.test(raw)) {
        query = raw;
        description = `Phrase: ${raw}`;
      } else {
        throw Error(`Rule ${i + 1} needs an email, @domain, Gmail search operator, or query: prefix.`);
      }
      if (!query || query.length > 1200 || /[\r\n]/.test(query)) throw Error(`Rule ${i + 1} is invalid or too long.`);
      if (!seen.has(query)) { result.push({query, description}); seen.add(query); }
    }
    if (!result.length) throw Error('Enter at least one keep rule first.');
    return result;
  }
  const api = {parseRules};
  root.GKRules = api;
  if (typeof module !== 'undefined') module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
