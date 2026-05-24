/**
 * Original style recipes and sample gallery.
 * These are not copied template slides.
 */

function listThemeSummaries() {
  return getThemes_().map(function(t) {
    return { id: t.id, name: t.name, description: t.description };
  });
}

function listSampleSummaries() {
  return getSamples_().map(function(s) {
    return {
      id: s.id,
      name: s.name,
      description: s.description,
      themeId: s.themeId,
      layout: s.layout,
      preview: s.preview
    };
  });
}

function getThemeById(id) {
  const themes = getThemes_();
  if (!id || id === 'auto') return themes[0];
  return themes.filter(function(t) { return t.id === id; })[0] || themes[0];
}

function getSampleById(id) {
  const samples = getSamples_();
  if (!id || id === 'auto') return samples[0];
  return samples.filter(function(s) { return s.id === id; })[0] || samples[0];
}

function chooseThemeForText(text) {
  const lower = String(text || '').toLowerCase();
  if (/security|malware|threat|vulnerability|attack|cyber/.test(lower)) return getThemeById('cyber-dark');
  if (/ai|machine learning|model|data|software|code|technology|tech/.test(lower)) return getThemeById('tech-modern');
  if (/startup|business|market|revenue|sales|customer|growth/.test(lower)) return getThemeById('business-clean');
  if (/school|student|teach|learn|education|course|class/.test(lower)) return getThemeById('education-soft');
  if (/biology|health|medical|patient|science|research|study/.test(lower)) return getThemeById('science-light');
  return getThemeById('minimal-editorial');
}

function getThemes_() {
  return [
    {
      id: 'auto',
      name: 'Auto pick',
      description: 'Choose a style from the raw text.',
      bg: '#FFFFFF',
      fg: '#111827',
      muted: '#4B5563',
      accent: '#2563EB',
      accent2: '#DBEAFE',
      card: '#F8FAFC',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '✨',
      attribution: ''
    },
    {
      id: 'tech-modern',
      name: 'Tech Modern',
      description: 'Dark modern tech slide with bright accent shapes.',
      bg: '#0B1020',
      fg: '#F8FAFC',
      muted: '#CBD5E1',
      accent: '#38BDF8',
      accent2: '#1E293B',
      card: '#111827',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '🤖',
      attribution: 'Original style recipe inspired by free tech presentation categories.'
    },
    {
      id: 'business-clean',
      name: 'Business Clean',
      description: 'Clean consulting-style layout with confident blue accents.',
      bg: '#F8FAFC',
      fg: '#0F172A',
      muted: '#475569',
      accent: '#1D4ED8',
      accent2: '#DBEAFE',
      card: '#FFFFFF',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '📈',
      attribution: 'Original style recipe inspired by free business presentation categories.'
    },
    {
      id: 'cyber-dark',
      name: 'Cyber Dark',
      description: 'Security-themed dark slide with green signal accents.',
      bg: '#020617',
      fg: '#ECFDF5',
      muted: '#A7F3D0',
      accent: '#22C55E',
      accent2: '#064E3B',
      card: '#0F172A',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '🛡️',
      attribution: 'Original style recipe inspired by free cybersecurity presentation categories.'
    },
    {
      id: 'education-soft',
      name: 'Education Soft',
      description: 'Friendly education style with soft warm shapes.',
      bg: '#FFF7ED',
      fg: '#431407',
      muted: '#7C2D12',
      accent: '#F97316',
      accent2: '#FED7AA',
      card: '#FFFFFF',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '🎓',
      attribution: 'Original style recipe inspired by free education presentation categories.'
    },
    {
      id: 'science-light',
      name: 'Science Light',
      description: 'Light academic/science style with calm teal accents.',
      bg: '#F0FDFA',
      fg: '#042F2E',
      muted: '#115E59',
      accent: '#0D9488',
      accent2: '#CCFBF1',
      card: '#FFFFFF',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '🧬',
      attribution: 'Original style recipe inspired by free science presentation categories.'
    },
    {
      id: 'minimal-editorial',
      name: 'Minimal Editorial',
      description: 'Magazine-like minimal design with large title contrast.',
      bg: '#FAFAF9',
      fg: '#1C1917',
      muted: '#57534E',
      accent: '#A855F7',
      accent2: '#F3E8FF',
      card: '#FFFFFF',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '✨',
      attribution: 'Original style recipe inspired by free minimal presentation categories.'
    },
    {
      id: 'startup-bold',
      name: 'Startup Bold',
      description: 'Pitch-deck style with bold highlight blocks.',
      bg: '#111827',
      fg: '#FFFFFF',
      muted: '#D1D5DB',
      accent: '#F59E0B',
      accent2: '#374151',
      card: '#1F2937',
      titleFont: 'Arial',
      bodyFont: 'Arial',
      icon: '🚀',
      attribution: 'Original style recipe inspired by free startup pitch presentation categories.'
    }
  ];
}

function getSamples_() {
  return [
    {
      id: 'auto',
      name: 'Auto Designer',
      description: 'Let the add-on choose the best sample from the raw text.',
      themeId: 'auto',
      layout: null,
      preview: 'auto'
    },
    {
      id: 'tech-split',
      name: 'Tech Split Brief',
      description: 'Large left headline with a right-side bullet panel.',
      themeId: 'tech-modern',
      layout: 'split',
      preview: 'split'
    },
    {
      id: 'business-cards',
      name: 'Business 4 Cards',
      description: 'Four clean cards for strategy, features, or key points.',
      themeId: 'business-clean',
      layout: 'cards',
      preview: 'cards'
    },
    {
      id: 'cyber-risk',
      name: 'Cyber Risk Panel',
      description: 'Dark security slide with threat-focused bullet panel.',
      themeId: 'cyber-dark',
      layout: 'split',
      preview: 'split'
    },
    {
      id: 'education-steps',
      name: 'Education Steps',
      description: 'Friendly step-by-step learning or lesson slide.',
      themeId: 'education-soft',
      layout: 'timeline',
      preview: 'timeline'
    },
    {
      id: 'science-research',
      name: 'Science Research',
      description: 'Academic summary with calm colors and evidence cards.',
      themeId: 'science-light',
      layout: 'cards',
      preview: 'cards'
    },
    {
      id: 'minimal-quote',
      name: 'Minimal Quote',
      description: 'Editorial quote/callout slide with supporting notes.',
      themeId: 'minimal-editorial',
      layout: 'quote',
      preview: 'quote'
    },
    {
      id: 'startup-hero',
      name: 'Startup Hero',
      description: 'Pitch-style hero slide with takeaway and visual badge.',
      themeId: 'startup-bold',
      layout: 'hero',
      preview: 'hero'
    },
    {
      id: 'roadmap',
      name: 'Roadmap Timeline',
      description: 'Horizontal timeline for phases, plans, or processes.',
      themeId: 'business-clean',
      layout: 'timeline',
      preview: 'timeline'
    },
    {
      id: 'compare',
      name: 'Before vs After',
      description: 'Two-column comparison for problem and solution.',
      themeId: 'minimal-editorial',
      layout: 'compare',
      preview: 'compare'
    },
    {
      id: 'data-insight',
      name: 'Data Insight',
      description: 'KPI-like visual card for numbers, growth, and results.',
      themeId: 'business-clean',
      layout: 'data',
      preview: 'data'
    },
    {
      id: 'ai-product',
      name: 'AI Product Feature',
      description: 'Modern AI/software feature slide.',
      themeId: 'tech-modern',
      layout: 'cards',
      preview: 'cards'
    },
    {
      id: 'executive-summary',
      name: 'Executive Summary',
      description: 'Polished one-slide executive summary.',
      themeId: 'business-clean',
      layout: 'split',
      preview: 'split'
    }
  ];
}
