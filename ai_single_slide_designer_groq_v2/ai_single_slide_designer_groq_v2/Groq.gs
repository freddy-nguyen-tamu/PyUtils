/**
 * Groq API client.
 *
 * Mimics the attached Python idea:
 * - load GROQ_API_KEY, GROQ_API_KEY1...GROQ_API_KEY9
 * - rotate through models
 * - try every key for each model before failing
 *
 * Apps Script has no normal environment variables, so the keys are stored in
 * User Properties with the same names.
 */

const GROQ_API_URL = 'https://api.groq.com/openai/v1/chat/completions';

const GROQ_MODELS = [
  'meta-llama/llama-4-scout-17b-16e-instruct',
  'groq/compound',
  'groq/compound-mini',
  'llama-3.3-70b-versatile',
  'openai/gpt-oss-120b',
  'openai/gpt-oss-20b',
  'qwen/qwen3-32b',
  'llama-3.1-8b-instant',
  'allam-2-7b'
];

const GROQ_MAX_TOKENS = 850;
const GROQ_TEMPERATURE = 0.35;

function getSavedGroqKeys_() {
  const props = PropertiesService.getUserProperties();
  const keys = [];

  const baseKey = props.getProperty('GROQ_API_KEY');
  if (baseKey) keys.push({ name: 'GROQ_API_KEY', value: baseKey });

  for (let i = 1; i < 10; i++) {
    const name = 'GROQ_API_KEY' + i;
    const key = props.getProperty(name);
    if (key) keys.push({ name: name, value: key });
  }

  return keys;
}

function askGroqAutoSwitch_(prompt) {
  const keys = getSavedGroqKeys_();
  if (!keys.length) {
    throw new Error('No Groq keys saved. Paste one or more keys in the sidebar first.');
  }

  const errors = [];
  const retrySeconds = [];

  for (let m = 0; m < GROQ_MODELS.length; m++) {
    const model = GROQ_MODELS[m];

    for (let k = 0; k < keys.length; k++) {
      const key = keys[k];

      try {
        const content = askGroqWithModelAndKey_(prompt, model, key.name, key.value);
        return {
          content: content,
          model: model,
          keyName: key.name
        };
      } catch (err) {
        const msg = String(err && err.message ? err.message : err);
        errors.push(key.name + ' + ' + model + ': ' + msg);

        const retry = extractRetrySecondsFromText_(msg);
        if (retry !== null) retrySeconds.push(retry);

        // Continue to next key/model for both temporary and permanent errors.
      }
    }
  }

  const retryHint = retrySeconds.length
    ? '\nSuggested retry after about ' + Math.ceil(Math.min.apply(null, retrySeconds)) + ' second(s).'
    : '';

  throw new Error(
    'Every saved Groq key/model combination failed.' +
    retryHint +
    '\nLast errors:\n' +
    errors.slice(-6).join('\n')
  );
}

function askGroqWithModelAndKey_(prompt, model, keyName, apiKey) {
  const payload = {
    model: model,
    messages: [
      {
        role: 'system',
        content: [
          'You design exactly one Google Slides slide from raw text.',
          'Return ONLY valid JSON. No markdown. No commentary.',
          'Schema:',
          '{"title":"string","subtitle":"string","bullets":["string"],"callout":"string","visualKeyword":"string","icon":"emoji","layout":"hero|split|cards|quote|timeline|compare|data"}',
          'Rules:',
          '- title max 9 words',
          '- subtitle max 18 words',
          '- bullets 3 to 5 items, max 11 words each',
          '- callout max 12 words',
          '- choose exactly one layout'
        ].join('\n')
      },
      { role: 'user', content: prompt }
    ],
    temperature: GROQ_TEMPERATURE,
    max_tokens: GROQ_MAX_TOKENS
  };

  const resp = UrlFetchApp.fetch(GROQ_API_URL, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: 'Bearer ' + apiKey
    },
    payload: JSON.stringify(payload),
    muteHttpExceptions: true
  });

  const status = resp.getResponseCode();
  const text = resp.getContentText();

  if (status !== 200) {
    throw new Error('Groq API error ' + status + ' for ' + keyName + ' + ' + model + ': ' + text.slice(0, 500));
  }

  const data = JSON.parse(text);
  const content = data &&
    data.choices &&
    data.choices[0] &&
    data.choices[0].message &&
    data.choices[0].message.content;

  if (!content) {
    throw new Error('Groq returned no content for ' + keyName + ' + ' + model);
  }

  return String(content).trim();
}

function extractRetrySecondsFromText_(text) {
  const match = String(text || '').match(/try again in ([0-9.]+)s/i);
  if (!match) return null;
  const n = Number(match[1]);
  return isNaN(n) ? null : n;
}
