/* Only email-address and domain rules. No Gmail search assumptions. */
(function (root) {
  'use strict';
  const VALID_EMAIL = /^[a-z0-9.!#$%&'*+\-/=?^_`{|}~]+@(?:[a-z0-9-]+\.)+[a-z]{2,}$/i;
  const VALID_DOMAIN = /^(?:[a-z0-9-]+\.)+[a-z]{2,}$/i;
  function parse(input) {
    const rules = []; const seen = new Set();
    for (const [index, raw] of String(input).split(/\r?\n/).entries()) {
      let value = raw.trim().toLowerCase();
      if (!value || value.startsWith('#')) continue;
      // Strip repeated @ for the common accidental @@domain typo.
      let type;
      if (value.startsWith('@')) {
        value = value.replace(/^@+/, '');
        if (!VALID_DOMAIN.test(value)) throw new Error(`Line ${index + 1}: invalid domain ${raw.trim()}`);
        type = 'domain';
      } else if (VALID_EMAIL.test(value)) type = 'email';
      else throw new Error(`Line ${index + 1}: enter an email address or @domain. No Gmail search syntax is used.`);
      const key = `${type}:${value}`;
      if (!seen.has(key)) { rules.push({type, value, label: type === 'domain' ? '@' + value : value}); seen.add(key); }
    }
    if (!rules.length) throw new Error('Enter at least one email or @domain to keep.');
    if (rules.length > 200) throw new Error('Maximum 200 keep rules.');
    return rules;
  }
  function matching(address, rules) {
    const email = String(address || '').replace(/^mailto:/i, '').trim().toLowerCase();
    if (!VALID_EMAIL.test(email)) return null;
    const domain = email.slice(email.lastIndexOf('@') + 1);
    return rules.find(rule => rule.type === 'email' ? rule.value === email : domain === rule.value || domain.endsWith('.' + rule.value)) || null;
  }
  const api = {parse, matching, VALID_EMAIL};
  root.GKRulesV5 = api;
  if (typeof module === 'object' && module.exports) module.exports = api;
})(typeof globalThis !== 'undefined' ? globalThis : this);
