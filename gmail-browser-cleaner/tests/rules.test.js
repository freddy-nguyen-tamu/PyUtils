const assert = require('node:assert/strict');
const test = require('node:test');
const {parseRules} = require('../rules.js');
const Gmail = require('../gmail-dom.js');

test('plain address protects both directions in Gmail search', () => {
  assert.deepEqual(parseRules('person@example.com')[0].query,
    '{from:person@example.com to:person@example.com cc:person@example.com bcc:person@example.com}');
});
test('domain rule protects both directions', () => {
  assert.match(parseRules('@company.com')[0].query,/from:@company\.com to:@company\.com/);
});
test('supports Gmail search operators and explicit queries', () => {
  const parsed = parseRules('subject:"Project"\nquery:from:someone@example.com has:attachment\nis:starred');
  assert.deepEqual(parsed.map(x=>x.query), ['subject:"Project"','from:someone@example.com has:attachment','is:starred']);
});
test('rejects ambiguous strings rather than broad unintended searches', () => {
  assert.throws(()=>parseRules('coworker'), /Rule 1/);
  assert.throws(()=>parseRules(''), /at least one/);
});
test('ignores comments and duplicate rules', () => {
  assert.equal(parseRules('# why\nfrom:bob@a.com\nfrom:bob@a.com')[0].query,'from:bob@a.com');
  assert.equal(parseRules('# why\nfrom:bob@a.com\nfrom:bob@a.com').length,1);
});
test('search hash safely encodes special characters', () => {
  assert.equal(Gmail.searchHash('subject:"a/b"'), '#search/subject%3A%22a%2Fb%22');
});
test('account slot is scoped to the active Gmail URL', () => {
  const a = {origin:'https://mail.google.com',pathname:'/mail/u/0/'};
  const b = {origin:'https://mail.google.com',pathname:'/mail/u/1/'};
  assert.notEqual(Gmail.accountKey(a), Gmail.accountKey(b));
});
test('Gmail search empty result is not guessed from absent rows', () => {
  assert.equal(Gmail.isEmpty({querySelector:()=>({textContent:'Loading'})}), false);
  assert.equal(Gmail.isEmpty({querySelector:()=>({textContent:'No conversations found.'})}), true);
});
