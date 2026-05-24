/**
 * Converts raw text into a one-slide plan.
 * Uses Groq when enabled and saved keys exist; otherwise uses local fallback.
 */

function createSlidePlan(rawText, options) {
  const sample = getSampleById(options.sampleId);
  const chosenTheme = chooseTheme_(rawText, options, sample);
  const forcedLayout = chooseLayout_(rawText, options, sample);

  let plan = null;
  let source = 'local-rule-engine';
  let groqModel = '';
  let groqKeyName = '';

  if (options.useGroq) {
    try {
      const groqPrompt = buildGroqPrompt_(rawText, options, chosenTheme, sample, forcedLayout);
      const groq = askGroqAutoSwitch_(groqPrompt);
      plan = parseGroqJson_(groq.content);
      source = 'groq';
      groqModel = groq.model;
      groqKeyName = groq.keyName;
    } catch (err) {
      plan = null;
      source = 'local-rule-engine-groq-fallback';
    }
  }

  if (!plan) {
    plan = createFallbackPlan_(rawText, options, chosenTheme, sample, forcedLayout);
  }

  plan = sanitizePlan_(plan, rawText, chosenTheme, sample, forcedLayout);
  plan.theme = chosenTheme;
  plan.sample = sample.id === 'auto' ? null : sample;
  plan.source = source;
  plan.groqModel = groqModel;
  plan.groqKeyName = groqKeyName;
  return plan;
}

function chooseTheme_(rawText, options, sample) {
  if (sample && sample.id !== 'auto' && sample.themeId) return getThemeById(sample.themeId);
  if (options.themeId && options.themeId !== 'auto') return getThemeById(options.themeId);
  return chooseThemeForText(rawText);
}

function chooseLayout_(rawText, options, sample) {
  if (sample && sample.id !== 'auto' && sample.layout) return sample.layout;
  if (options.layout && options.layout !== 'auto') return options.layout;
  return null;
}

function buildGroqPrompt_(rawText, options, theme, sample, forcedLayout) {
  return [
    'Create a concise JSON plan for exactly one slide.',
    'Theme: ' + theme.name,
    'Sample style: ' + (sample && sample.id !== 'auto' ? sample.name + ' - ' + sample.description : 'Auto'),
    'Preferred layout: ' + (forcedLayout || 'auto'),
    'Density: ' + options.density,
    '',
    'Raw text:',
    rawText
  ].join('\n');
}

function parseGroqJson_(content) {
  let text = String(content || '').trim();

  // Remove accidental code fence if a model includes one.
  text = text.replace(/^```(?:json)?/i, '').replace(/```$/i, '').trim();

  const first = text.indexOf('{');
  const last = text.lastIndexOf('}');
  if (first >= 0 && last > first) {
    text = text.slice(first, last + 1);
  }

  return JSON.parse(text);
}

function createFallbackPlan_(rawText, options, theme, sample, forcedLayout) {
  const clean = rawText
    .replace(/\r/g, '\n')
    .replace(/[ \t]+/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();

  const lines = clean
    .split(/\n+/)
    .map(function(line) {
      return line.replace(/^[-*•\d.)\s]+/, '').trim();
    })
    .filter(Boolean);

  const sentences = clean
    .split(/(?<=[.!?])\s+/)
    .map(function(s) { return s.trim(); })
    .filter(Boolean);

  const first = lines[0] || sentences[0] || clean;
  const title = titleCase_(shorten_(first, 58));

  const subtitleCandidate = lines.length > 1 ? lines[1] : (sentences[1] || '');
  const subtitle = shorten_(subtitleCandidate || inferSubtitle_(clean), 95);

  let bulletPool = lines.slice(1);
  if (bulletPool.length < 3) bulletPool = sentences.slice(1);
  if (bulletPool.length < 3) bulletPool = extractKeywordBullets_(clean);

  const bulletCount = options.density === 'light' ? 3 : (options.density === 'dense' ? 5 : 4);
  const bullets = bulletPool
    .filter(function(x) { return x.toLowerCase() !== title.toLowerCase(); })
    .slice(0, bulletCount)
    .map(function(b) { return shorten_(b, 70); });

  while (bullets.length < 3) {
    bullets.push([
      'Clarify the main idea',
      'Highlight the strongest evidence',
      'End with one memorable takeaway'
    ][bullets.length]);
  }

  return {
    title: title || 'Slide Summary',
    subtitle: subtitle || 'A clean one-slide summary from your raw text',
    bullets: bullets,
    callout: inferCallout_(clean),
    visualKeyword: inferVisualKeyword_(clean),
    icon: inferIcon_(clean),
    layout: forcedLayout || inferLayout_(clean, bullets)
  };
}

function sanitizePlan_(plan, rawText, theme, sample, forcedLayout) {
  plan = plan || {};
  const fallback = createFallbackPlan_(rawText, { density: 'balanced' }, theme, sample, forcedLayout);
  const bullets = Array.isArray(plan.bullets) ? plan.bullets : fallback.bullets;

  return {
    title: shorten_(String(plan.title || fallback.title || 'Slide Summary'), 62),
    subtitle: shorten_(String(plan.subtitle || fallback.subtitle || ''), 100),
    bullets: bullets
      .map(function(b) { return shorten_(String(b || '').replace(/\s+/g, ' ').trim(), 72); })
      .filter(Boolean)
      .slice(0, 5),
    callout: shorten_(String(plan.callout || fallback.callout || 'Key takeaway'), 70),
    visualKeyword: shorten_(String(plan.visualKeyword || fallback.visualKeyword || 'idea'), 30),
    icon: String(plan.icon || fallback.icon || theme.icon || '✨').slice(0, 4),
    layout: ['hero', 'split', 'cards', 'quote', 'timeline', 'compare', 'data'].indexOf(forcedLayout || plan.layout) >= 0
      ? (forcedLayout || plan.layout)
      : fallback.layout
  };
}

function inferSubtitle_(text) {
  if (/problem|challenge|risk|issue|pain/i.test(text)) return 'A focused view of the problem, impact, and next step';
  if (/research|study|data|analysis|experiment/i.test(text)) return 'A concise research summary with the most important signal';
  if (/startup|market|customer|revenue|growth/i.test(text)) return 'A business-ready summary built for fast understanding';
  if (/security|malware|threat|vulnerability|attack/i.test(text)) return 'A practical security summary with clear risk framing';
  return 'A polished one-slide summary of the main idea';
}

function inferCallout_(text) {
  if (/increase|growth|improve|higher|faster/i.test(text)) return 'Momentum is improving';
  if (/risk|threat|problem|failure|decline/i.test(text)) return 'Reduce the biggest risk first';
  if (/launch|release|ship|build/i.test(text)) return 'Ship the clearest next step';
  return 'Make the idea easy to remember';
}

function inferVisualKeyword_(text) {
  if (/security|malware|threat|vulnerability|attack/i.test(text)) return 'security';
  if (/ai|machine learning|model|data/i.test(text)) return 'AI';
  if (/finance|revenue|market|startup|business/i.test(text)) return 'growth';
  if (/education|student|learn|course/i.test(text)) return 'learning';
  if (/health|biology|medical|patient/i.test(text)) return 'health';
  return 'idea';
}

function inferIcon_(text) {
  if (/security|malware|threat|vulnerability|attack/i.test(text)) return '🛡️';
  if (/ai|machine learning|model|neural/i.test(text)) return '🤖';
  if (/finance|revenue|market|startup|business/i.test(text)) return '📈';
  if (/education|student|learn|course/i.test(text)) return '🎓';
  if (/health|biology|medical|patient/i.test(text)) return '🧬';
  if (/design|creative|brand/i.test(text)) return '🎨';
  return '✨';
}

function inferLayout_(text, bullets) {
  if (/quote|said|statement/i.test(text)) return 'quote';
  if (/versus|compared|compare|difference|pros|cons/i.test(text)) return 'compare';
  if (/step|phase|timeline|roadmap|first|second|third/i.test(text)) return 'timeline';
  if (/metric|percent|growth|data|number|kpi|\d+%/i.test(text)) return 'data';
  if (bullets.length >= 4) return 'cards';
  return 'split';
}

function extractKeywordBullets_(text) {
  const words = text.toLowerCase().match(/[a-z][a-z0-9-]{3,}/g) || [];
  const stop = {
    this:1, that:1, with:1, from:1, have:1, will:1, your:1, about:1,
    into:1, they:1, them:1, then:1, than:1, when:1, where:1, which:1
  };
  const counts = {};
  words.forEach(function(w) {
    if (!stop[w]) counts[w] = (counts[w] || 0) + 1;
  });
  return Object.keys(counts)
    .sort(function(a, b) { return counts[b] - counts[a]; })
    .slice(0, 4)
    .map(function(w) { return 'Emphasize ' + w.replace(/-/g, ' '); });
}

function shorten_(text, max) {
  text = String(text || '').replace(/\s+/g, ' ').trim();
  if (text.length <= max) return text;
  return text.slice(0, max - 1).replace(/\s+\S*$/, '') + '…';
}

function titleCase_(text) {
  text = String(text || '').trim();
  if (!text) return text;
  return text.charAt(0).toUpperCase() + text.slice(1);
}
