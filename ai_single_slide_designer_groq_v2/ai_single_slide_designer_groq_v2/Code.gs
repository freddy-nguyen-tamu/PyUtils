/**
 * AI Single-Slide Designer for Google Slides
 * Version 2: sample gallery + Groq API rotation.
 *
 * This is a container-bound Apps Script add-on.
 * It designs exactly one selected/current slide or inserts exactly one new slide.
 */

function onOpen() {
  SlidesApp.getUi()
    .createMenu('AI Slide Designer')
    .addItem('Open designer', 'showDesignerSidebar')
    .addSeparator()
    .addItem('Clear saved Groq keys', 'clearSavedGroqKeysFromMenu')
    .addToUi();
}

function showDesignerSidebar() {
  const html = HtmlService.createHtmlOutputFromFile('Sidebar')
    .setTitle('AI Slide Designer');
  SlidesApp.getUi().showSidebar(html);
}

function getAddonState() {
  return {
    hasGroqKeys: getSavedGroqKeys_().length > 0,
    savedGroqKeyCount: getSavedGroqKeys_().length,
    themes: listThemeSummaries(),
    samples: listSampleSummaries(),
    defaultTheme: 'auto',
    defaultSample: 'auto'
  };
}

function saveGroqApiKeys(rawKeys) {
  const keys = String(rawKeys || '')
    .split(/\r?\n|,/)
    .map(function(k) { return k.trim(); })
    .filter(Boolean)
    .slice(0, 10);

  if (!keys.length) {
    throw new Error('Paste at least one Groq API key first.');
  }

  const props = PropertiesService.getUserProperties();

  // Clear old key slots first.
  for (let i = 0; i < 10; i++) {
    props.deleteProperty(i === 0 ? 'GROQ_API_KEY' : 'GROQ_API_KEY' + i);
  }

  keys.forEach(function(key, i) {
    props.setProperty(i === 0 ? 'GROQ_API_KEY' : 'GROQ_API_KEY' + i, key);
  });

  return {
    ok: true,
    count: keys.length,
    message: 'Saved ' + keys.length + ' Groq API key(s).'
  };
}

function clearSavedGroqKeys() {
  const props = PropertiesService.getUserProperties();
  for (let i = 0; i < 10; i++) {
    props.deleteProperty(i === 0 ? 'GROQ_API_KEY' : 'GROQ_API_KEY' + i);
  }
  return { ok: true, message: 'Saved Groq API keys cleared.' };
}

function clearSavedGroqKeysFromMenu() {
  clearSavedGroqKeys();
  SlidesApp.getUi().alert('Saved Groq API keys cleared.');
}

function designCurrentSlide(rawText, options) {
  options = normalizeOptions_(options);
  const text = String(rawText || '').trim();
  if (!text) throw new Error('Paste raw text first.');

  const presentation = SlidesApp.getActivePresentation();
  const slide = getCurrentOrFirstSlide_(presentation);

  const plan = createSlidePlan(text, options);
  renderSlide_(presentation, slide, plan);

  return {
    ok: true,
    mode: 'redesigned-current-slide',
    title: plan.title,
    themeName: plan.theme.name,
    sampleName: plan.sample ? plan.sample.name : 'Auto',
    layout: plan.layout,
    source: plan.source,
    groqModel: plan.groqModel || '',
    groqKeyName: plan.groqKeyName || ''
  };
}

function insertDesignedSlide(rawText, options) {
  options = normalizeOptions_(options);
  const text = String(rawText || '').trim();
  if (!text) throw new Error('Paste raw text first.');

  const presentation = SlidesApp.getActivePresentation();
  const slides = presentation.getSlides();
  const selected = getCurrentOrFirstSlide_(presentation);
  const selectedIndex = slides.findIndex(function(s) {
    return s.getObjectId() === selected.getObjectId();
  });

  const slide = presentation.insertSlide(selectedIndex + 1, SlidesApp.PredefinedLayout.BLANK);
  const plan = createSlidePlan(text, options);
  renderSlide_(presentation, slide, plan);

  return {
    ok: true,
    mode: 'inserted-one-slide',
    title: plan.title,
    themeName: plan.theme.name,
    sampleName: plan.sample ? plan.sample.name : 'Auto',
    layout: plan.layout,
    source: plan.source,
    groqModel: plan.groqModel || '',
    groqKeyName: plan.groqKeyName || ''
  };
}

function normalizeOptions_(options) {
  options = options || {};
  return {
    themeId: String(options.themeId || 'auto'),
    sampleId: String(options.sampleId || 'auto'),
    layout: String(options.layout || 'auto'),
    density: String(options.density || 'balanced'),
    useGroq: Boolean(options.useGroq),
    language: String(options.language || 'auto')
  };
}

function getCurrentOrFirstSlide_(presentation) {
  const selection = presentation.getSelection();
  const page = selection && selection.getCurrentPage ? selection.getCurrentPage() : null;

  if (page && page.getPageType && page.getPageType() === SlidesApp.PageType.SLIDE) {
    return page.asSlide();
  }

  const slides = presentation.getSlides();
  if (!slides.length) {
    return presentation.appendSlide(SlidesApp.PredefinedLayout.BLANK);
  }
  return slides[0];
}
