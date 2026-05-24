/**
 * Draws the one-slide design using SlidesApp.
 */

const SLIDE_W = 720;
const SLIDE_H = 405;

function renderSlide_(presentation, slide, plan) {
  clearSlide_(slide);

  const theme = plan.theme;
  setSlideBackground_(slide, theme.bg);
  drawBackgroundDecor_(slide, theme);

  if (plan.layout === 'hero') {
    drawHeroLayout_(slide, plan, theme);
  } else if (plan.layout === 'cards') {
    drawCardsLayout_(slide, plan, theme);
  } else if (plan.layout === 'quote') {
    drawQuoteLayout_(slide, plan, theme);
  } else if (plan.layout === 'timeline') {
    drawTimelineLayout_(slide, plan, theme);
  } else if (plan.layout === 'compare') {
    drawCompareLayout_(slide, plan, theme);
  } else if (plan.layout === 'data') {
    drawDataLayout_(slide, plan, theme);
  } else {
    drawSplitLayout_(slide, plan, theme);
  }

  if (plan.theme.id !== 'auto') {
    drawAttribution_(slide, plan, theme);
  }
}

function clearSlide_(slide) {
  const elements = slide.getPageElements();
  for (let i = elements.length - 1; i >= 0; i--) {
    elements[i].remove();
  }
}

function setSlideBackground_(slide, color) {
  slide.getBackground().setSolidFill(color);
}

function drawBackgroundDecor_(slide, theme) {
  const c1 = slide.insertShape(SlidesApp.ShapeType.ELLIPSE, 570, -95, 220, 220);
  c1.getFill().setSolidFill(theme.accent2);
  c1.getLine().setTransparent();

  const c2 = slide.insertShape(SlidesApp.ShapeType.ELLIPSE, -75, 305, 170, 170);
  c2.getFill().setSolidFill(theme.accent2);
  c2.getLine().setTransparent();

  const bar = slide.insertShape(SlidesApp.ShapeType.RECTANGLE, 0, 0, 8, SLIDE_H);
  bar.getFill().setSolidFill(theme.accent);
  bar.getLine().setTransparent();
}

function drawHeroLayout_(slide, plan, theme) {
  drawIconBadge_(slide, plan.icon, theme, 48, 42, 58);

  addText_(slide, plan.title, 48, 112, 505, 96, {
    font: theme.titleFont, size: 34, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 51, 207, 470, 48, {
    font: theme.bodyFont, size: 14, color: theme.muted
  });

  drawCalloutPill_(slide, plan.callout, theme, 50, 288, 390, 44);
  drawRightMetricCard_(slide, plan, theme);
}

function drawSplitLayout_(slide, plan, theme) {
  drawIconBadge_(slide, plan.icon, theme, 46, 38, 48);

  addText_(slide, plan.title, 46, 95, 300, 110, {
    font: theme.titleFont, size: 28, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 48, 207, 285, 64, {
    font: theme.bodyFont, size: 12.5, color: theme.muted
  });

  drawCalloutPill_(slide, plan.callout, theme, 47, 300, 276, 48);
  drawBulletPanel_(slide, plan.bullets, theme, 375, 54, 295, 290);
}

function drawCardsLayout_(slide, plan, theme) {
  addText_(slide, plan.title, 45, 35, 520, 52, {
    font: theme.titleFont, size: 26, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 47, 86, 540, 34, {
    font: theme.bodyFont, size: 12, color: theme.muted
  });

  const bullets = plan.bullets.slice(0, 4);
  const positions = [[45, 145], [370, 145], [45, 260], [370, 260]];

  bullets.forEach(function(b, i) {
    const p = positions[i];
    drawSmallCard_(slide, String(i + 1), b, theme, p[0], p[1], 285, 88);
  });

  drawMiniTag_(slide, plan.icon + ' ' + plan.visualKeyword, theme, 560, 35, 110, 34);
}

function drawQuoteLayout_(slide, plan, theme) {
  addText_(slide, '“', 48, 40, 70, 70, {
    font: theme.titleFont, size: 54, color: theme.accent, bold: true
  });

  addText_(slide, plan.callout || plan.title, 95, 70, 540, 110, {
    font: theme.titleFont, size: 30, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 100, 185, 480, 45, {
    font: theme.bodyFont, size: 13, color: theme.muted
  });

  drawBulletPanel_(slide, plan.bullets.slice(0, 3), theme, 95, 250, 540, 95);
}

function drawTimelineLayout_(slide, plan, theme) {
  addText_(slide, plan.title, 45, 32, 525, 52, {
    font: theme.titleFont, size: 25, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 47, 82, 535, 32, {
    font: theme.bodyFont, size: 12, color: theme.muted
  });

  const bullets = plan.bullets.slice(0, 4);
  const y = 205;
  const line = slide.insertShape(SlidesApp.ShapeType.RECTANGLE, 82, y + 16, 555, 4);
  line.getFill().setSolidFill(theme.accent2);
  line.getLine().setTransparent();

  bullets.forEach(function(b, i) {
    const x = 70 + i * 150;
    const dot = slide.insertShape(SlidesApp.ShapeType.ELLIPSE, x, y, 36, 36);
    dot.getFill().setSolidFill(theme.accent);
    dot.getLine().setTransparent();

    addText_(slide, String(i + 1), x, y + 8, 36, 12, {
      font: theme.bodyFont, size: 9, color: '#FFFFFF', bold: true, align: SlidesApp.ParagraphAlignment.CENTER
    });

    addText_(slide, b, x - 30, y + 50, 105, 70, {
      font: theme.bodyFont, size: 10.5, color: theme.fg, bold: true, align: SlidesApp.ParagraphAlignment.CENTER
    });
  });
}

function drawCompareLayout_(slide, plan, theme) {
  addText_(slide, plan.title, 45, 34, 560, 48, {
    font: theme.titleFont, size: 25, color: theme.fg, bold: true
  });

  const half = Math.ceil(plan.bullets.length / 2);
  const left = plan.bullets.slice(0, half);
  const right = plan.bullets.slice(half);

  drawColumn_(slide, 'Before / Problem', left, theme, 55, 120, 285, 205);
  drawColumn_(slide, 'After / Opportunity', right.length ? right : [plan.callout], theme, 380, 120, 285, 205);
}

function drawDataLayout_(slide, plan, theme) {
  addText_(slide, plan.title, 45, 35, 425, 56, {
    font: theme.titleFont, size: 25, color: theme.fg, bold: true
  });

  addText_(slide, plan.subtitle, 48, 90, 410, 34, {
    font: theme.bodyFont, size: 12, color: theme.muted
  });

  const card = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, 485, 55, 165, 145);
  card.getFill().setSolidFill(theme.card);
  card.getLine().setSolidFill(theme.accent2);
  card.getLine().setWeight(1);

  addText_(slide, plan.icon || '📊', 512, 78, 110, 38, {
    font: theme.bodyFont, size: 28, color: theme.fg, align: SlidesApp.ParagraphAlignment.CENTER
  });

  addText_(slide, plan.visualKeyword.toUpperCase(), 510, 128, 115, 20, {
    font: theme.bodyFont, size: 10, color: theme.accent, bold: true, align: SlidesApp.ParagraphAlignment.CENTER
  });

  addText_(slide, plan.callout, 505, 158, 125, 26, {
    font: theme.bodyFont, size: 10, color: theme.muted, align: SlidesApp.ParagraphAlignment.CENTER
  });

  drawBulletPanel_(slide, plan.bullets, theme, 55, 165, 390, 170);

  const bars = [68, 105, 82, 130];
  bars.forEach(function(h, i) {
    const x = 492 + i * 35;
    const y = 323 - h;
    const bar = slide.insertShape(SlidesApp.ShapeType.RECTANGLE, x, y, 22, h);
    bar.getFill().setSolidFill(i === 3 ? theme.accent : theme.accent2);
    bar.getLine().setTransparent();
  });
}

function drawBulletPanel_(slide, bullets, theme, x, y, w, h) {
  const panel = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, w, h);
  panel.getFill().setSolidFill(theme.card);
  panel.getLine().setSolidFill(theme.accent2);
  panel.getLine().setWeight(1);

  const title = slide.insertShape(SlidesApp.ShapeType.RECTANGLE, x + 18, y + 18, 66, 24);
  title.getFill().setSolidFill(theme.accent);
  title.getLine().setTransparent();

  addText_(slide, 'POINTS', x + 22, y + 24, 58, 12, {
    font: theme.bodyFont, size: 7.5, color: '#FFFFFF', bold: true, align: SlidesApp.ParagraphAlignment.CENTER
  });

  const body = bullets.map(function(b) { return '• ' + b; }).join('\n');
  addText_(slide, body, x + 24, y + 58, w - 48, h - 78, {
    font: theme.bodyFont, size: 13, color: theme.fg, spacing: 115
  });
}

function drawSmallCard_(slide, num, text, theme, x, y, w, h) {
  const card = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, w, h);
  card.getFill().setSolidFill(theme.card);
  card.getLine().setSolidFill(theme.accent2);
  card.getLine().setWeight(1);

  const badge = slide.insertShape(SlidesApp.ShapeType.ELLIPSE, x + 18, y + 20, 30, 30);
  badge.getFill().setSolidFill(theme.accent);
  badge.getLine().setTransparent();

  addText_(slide, num, x + 18, y + 28, 30, 12, {
    font: theme.bodyFont, size: 9, color: '#FFFFFF', bold: true, align: SlidesApp.ParagraphAlignment.CENTER
  });

  addText_(slide, text, x + 62, y + 18, w - 80, h - 30, {
    font: theme.bodyFont, size: 12.5, color: theme.fg, bold: true
  });
}

function drawColumn_(slide, heading, bullets, theme, x, y, w, h) {
  const card = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, w, h);
  card.getFill().setSolidFill(theme.card);
  card.getLine().setSolidFill(theme.accent2);
  card.getLine().setWeight(1);

  addText_(slide, heading, x + 22, y + 22, w - 44, 25, {
    font: theme.titleFont, size: 16, color: theme.accent, bold: true
  });

  addText_(slide, bullets.map(function(b) { return '• ' + b; }).join('\n'), x + 24, y + 67, w - 48, h - 85, {
    font: theme.bodyFont, size: 12.5, color: theme.fg
  });
}

function drawIconBadge_(slide, icon, theme, x, y, size) {
  const badge = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, size, size);
  badge.getFill().setSolidFill(theme.accent2);
  badge.getLine().setTransparent();

  addText_(slide, icon || theme.icon, x, y + size * 0.24, size, size * 0.38, {
    font: theme.bodyFont, size: Math.round(size * 0.34), color: theme.fg, align: SlidesApp.ParagraphAlignment.CENTER
  });
}

function drawCalloutPill_(slide, text, theme, x, y, w, h) {
  const pill = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, w, h);
  pill.getFill().setSolidFill(theme.accent);
  pill.getLine().setTransparent();

  addText_(slide, text, x + 18, y + 13, w - 36, h - 20, {
    font: theme.bodyFont, size: 12, color: '#FFFFFF', bold: true
  });
}

function drawRightMetricCard_(slide, plan, theme) {
  const card = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, 540, 116, 118, 154);
  card.getFill().setSolidFill(theme.card);
  card.getLine().setSolidFill(theme.accent2);
  card.getLine().setWeight(1);

  addText_(slide, plan.icon || theme.icon, 560, 136, 78, 45, {
    font: theme.bodyFont, size: 30, color: theme.fg, align: SlidesApp.ParagraphAlignment.CENTER
  });

  addText_(slide, plan.visualKeyword.toUpperCase(), 555, 202, 88, 18, {
    font: theme.bodyFont, size: 8, color: theme.accent, bold: true, align: SlidesApp.ParagraphAlignment.CENTER
  });

  addText_(slide, 'single-slide focus', 555, 230, 88, 24, {
    font: theme.bodyFont, size: 9, color: theme.muted, align: SlidesApp.ParagraphAlignment.CENTER
  });
}

function drawMiniTag_(slide, text, theme, x, y, w, h) {
  const tag = slide.insertShape(SlidesApp.ShapeType.ROUND_RECTANGLE, x, y, w, h);
  tag.getFill().setSolidFill(theme.accent2);
  tag.getLine().setTransparent();

  addText_(slide, text, x + 8, y + 10, w - 16, 12, {
    font: theme.bodyFont, size: 9, color: theme.fg, bold: true, align: SlidesApp.ParagraphAlignment.CENTER
  });
}

function drawAttribution_(slide, plan, theme) {
  if (!theme.attribution) return;
  addText_(slide, theme.attribution, 42, 384, 630, 12, {
    font: theme.bodyFont, size: 5.5, color: theme.muted
  });
}

function addText_(slide, text, x, y, w, h, style) {
  style = style || {};
  const box = slide.insertTextBox(String(text || ''), x, y, w, h);
  const textRange = box.getText();

  if (textRange.asString().length > 0) {
    const ts = textRange.getTextStyle();
    ts.setFontFamily(style.font || 'Arial');
    ts.setFontSize(style.size || 12);
    ts.setForegroundColor(style.color || '#111111');
    if (style.bold !== undefined) ts.setBold(Boolean(style.bold));

    const ps = textRange.getParagraphStyle();
    if (style.align) ps.setParagraphAlignment(style.align);
    if (style.spacing) ps.setLineSpacing(style.spacing);
  }

  box.getLine().setTransparent();
  return box;
}
