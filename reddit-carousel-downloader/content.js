(() => {
  'use strict';

  // This script runs only after the user presses Start; it continues when the
  // popup closes. Avoid installing more than one listener on re-injection.
  const GLOBAL_KEY = '__redditCarouselDownloader_1';
  if (globalThis[GLOBAL_KEY]) return;

  const MAX_IMAGES = 2000;
  const TRANSITION_TIMEOUT_MS = 9000;
  const IMAGE_APPEAR_TIMEOUT_MS = 7000;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

  let stopping = false;
  const state = {
    running: false,
    status: 'Ready. Open a Reddit gallery and press Start.',
    downloaded: 0,
    skipped: 0,
    slide: null,
    total: null,
    lastFile: '',
    error: '',
    reason: '',
    startedAt: null
  };
  const snapshot = () => ({ ...state });
  const update = (changes) => Object.assign(state, changes);

  function deepQueryAll(root, selector) {
    const output = [];
    const seen = new Set();
    const walk = (scope) => {
      if (!scope || seen.has(scope)) return;
      seen.add(scope);
      if (scope instanceof Element && scope.matches(selector)) output.push(scope);
      if (scope instanceof Element && scope.shadowRoot) walk(scope.shadowRoot);
      if (!scope.querySelectorAll) return;
      const nodes = scope.querySelectorAll('*');
      for (const node of nodes) {
        if (node.matches(selector)) output.push(node);
        if (node.shadowRoot) walk(node.shadowRoot);
      }
    };
    walk(root);
    return [...new Set(output)];
  }

  function composedParent(node) {
    const parent = node?.parentElement;
    if (parent) return parent;
    const root = node?.getRootNode?.();
    return root instanceof ShadowRoot ? root.host : null;
  }

  function isActuallyVisible(node) {
    if (!(node instanceof Element) || !node.isConnected) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width < 2 || rect.height < 2) return false;
    for (let cur = node; cur && cur instanceof Element; cur = composedParent(cur)) {
      const style = getComputedStyle(cur);
      if (style.display === 'none' || style.visibility === 'hidden' || style.visibility === 'collapse') return false;
      if (cur.hasAttribute('hidden') || cur.hasAttribute('inert')) return false;
    }
    return true;
  }

  function chooseGallery() {
    const candidates = deepQueryAll(document, 'gallery-carousel, faceplate-carousel, [role="region"][aria-roledescription="carousel"], [data-testid*="carousel"], [aria-roledescription="carousel"]');
    let best = null;
    let bestScore = -Infinity;
    for (const element of candidates) {
      if (!isActuallyVisible(element)) continue;
      if (element.localName === 'faceplate-carousel' && composedParent(element)?.closest?.('gallery-carousel')) continue;
      const rect = element.getBoundingClientRect();
      const intersectionW = Math.max(0, Math.min(innerWidth, rect.right) - Math.max(0, rect.left));
      const intersectionH = Math.max(0, Math.min(innerHeight, rect.bottom) - Math.max(0, rect.top));
      if (intersectionW < 80 || intersectionH < 80) continue;
      const inLightbox = !!element.closest('#shreddit-media-lightbox, [role="dialog"], [data-testid="lightbox-template"]');
      const hasSlides = !!element.querySelector('[slot^="page-"], img, picture');
      const score = (inLightbox ? 10000000 : 0) + (hasSlides ? 1000000 : 0) +
        Math.min(intersectionW * intersectionH, 1000000) +
        (element.localName === 'gallery-carousel' ? 300000 : 0);
      if (score > bestScore) { best = element; bestScore = score; }
    }
    return best;
  }

  function counterFromGallery(gallery) {
    const carousels = gallery.localName === 'faceplate-carousel'
      ? [gallery] : deepQueryAll(gallery, 'faceplate-carousel');
    for (const carousel of carousels) {
      const value = carousel.getAttribute('current-aria-live-msg') || '';
      const numbers = value.match(/\d+/g)?.map(Number) || [];
      if (numbers.length >= 2 && numbers[0] >= 1 && numbers[1] >= numbers[0]) {
        return { index: numbers[0], total: numbers[1] };
      }
    }
    // Fallback when Reddit stops using its current-aria-live-msg attribute.
    const indicator = deepQueryAll(gallery, '[aria-live], [aria-label*=" of "]')
      .map(node => node.getAttribute('aria-label') || node.textContent || '')
      .find(text => /\d+\s*(?:of|\/)\s*\d+/i.test(text));
    const match = indicator?.match(/(\d+)\s*(?:of|\/)\s*(\d+)/i);
    return match ? { index: Number(match[1]), total: Number(match[2]) } : {};
  }

  function chooseActiveSlide(gallery) {
    const slides = deepQueryAll(gallery, '[slot^="page-"], [data-slide-index], [data-carousel-index], [role="tabpanel"]');
    const counter = counterFromGallery(gallery);
    if (!slides.length) return { element: gallery, index: counter.index || null, total: counter.total || null };

    const parseSlot = slide => {
      const slot = slide.getAttribute('slot') || '';
      const match = slot.match(/^page-(\d+)$/);
      return match ? Number(match[1]) : null;
    };
    let selected = counter.index && slides.find(s => parseSlot(s) === counter.index && isActuallyVisible(s));
    if (!selected) selected = slides.find(s => isActuallyVisible(s) && s.style.visibility === 'visible');
    if (!selected) selected = slides.find(s => isActuallyVisible(s) &&
      (s.getAttribute('aria-current') === 'true' || s.getAttribute('aria-selected') === 'true' || s.classList.contains('active')));
    if (!selected) {
      const r = gallery.getBoundingClientRect();
      const cx = Math.max(0, Math.min(innerWidth, (r.left + r.right) / 2));
      const cy = Math.max(0, Math.min(innerHeight, (r.top + r.bottom) / 2));
      selected = slides.filter(isActuallyVisible)
        .map(s => {
          const b = s.getBoundingClientRect();
          return { slide: s, dist: Math.abs((b.left + b.right) / 2 - cx) + Math.abs((b.top + b.bottom) / 2 - cy) };
        }).sort((a, b) => a.dist - b.dist)[0]?.slide;
    }
    if (!selected && counter.index) selected = slides.find(s => parseSlot(s) === counter.index);
    selected ||= slides[0];
    return { element: selected, index: parseSlot(selected) || counter.index || null, total: counter.total || slides.length };
  }

  function mediaURL(input) {
    try {
      const u = new URL(input, location.href);
      if (u.protocol !== 'https:') return null;
      const host = u.hostname.toLowerCase();
      const allowed = host === 'i.redd.it' || host === 'preview.redd.it' ||
        host.endsWith('.preview.redd.it') || host === 'external-preview.redd.it' ||
        host === 'redditmedia.com' || host.endsWith('.redditmedia.com') ||
        (host === 'www.reddit.com' && u.pathname === '/media');
      return allowed ? u : null;
    } catch { return null; }
  }

  function identityFromURL(url) {
    const u = new URL(url);
    let base = decodeURIComponent(u.pathname.split('/').pop() || '');
    base = base.replace(/^.*-v\d+-([a-z0-9]{8,}\.(?:jpe?g|png|webp|gif|avif))$/i, '$1');
    return base.toLowerCase() || u.pathname.toLowerCase();
  }

  function originalForPreview(url) {
    const u = mediaURL(url);
    if (!u || !/(^|\.)preview\.redd\.it$/.test(u.hostname)) return null;
    const name = identityFromURL(u.href);
    if (!/^[a-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif|avif)$/i.test(name)) return null;
    return `https://i.redd.it/${name}`;
  }

  function imageCandidates(slide) {
    const candidates = new Map();
    const add = (raw, extra = 0) => {
      if (!raw) return;
      const url = mediaURL(raw);
      if (!url) return;
      const host = url.hostname.toLowerCase();
      const width = Number(url.searchParams.get('width') || 0);
      const score = (host === 'i.redd.it' ? 10000 : 0) +
        (url.pathname.match(/\.(jpg|jpeg|png|webp|gif|avif)$/i) ? 250 : 0) +
        Math.min(width, 5000) / 10 + extra;
      const existing = candidates.get(url.href);
      if (!existing || existing.score < score) candidates.set(url.href, { url: url.href, score });
    };
    const elements = deepQueryAll(slide, 'img, source, a[href]');
    for (const element of elements) {
      if (element.matches('img') && (element.getAttribute('role') === 'presentation' ||
          element.classList.contains('post-background-image-filter'))) continue;
      for (const attr of ['data-original', 'data-full-src', 'data-src', 'data-url', 'src', 'poster', 'href']) {
        const raw = element.getAttribute(attr);
        if (raw) add(raw, /original|full/i.test(attr) ? 150 : 0);
      }
      if (element.matches('img') && element.currentSrc) add(element.currentSrc, 10);
      // Reddit source sets contain comma-separated URLs with width descriptors.
      for (const part of (element.getAttribute('srcset') || '').split(',')) {
        const [url, descriptor] = part.trim().split(/\s+/);
        if (url) add(url, Number.parseInt(descriptor, 10) / 10 || 0);
      }
    }
    // The currently visible image might only be a Reddit preview, whereas
    // the original lives at i.redd.it using the same media identity.
    for (const candidate of [...candidates.values()]) {
      const original = originalForPreview(candidate.url);
      if (original) add(original, 850);
    }
    return [...candidates.values()].sort((a, b) => b.score - a.score).map(x => x.url);
  }

  function pageSelection() {
    const gallery = chooseGallery();
    if (!gallery) return null;
    const active = chooseActiveSlide(gallery);
    const urls = imageCandidates(active.element);
    return {
      gallery,
      urls,
      identity: urls.length ? identityFromURL(urls[0]) : null,
      index: active.index,
      total: active.total
    };
  }

  function findNextButton(gallery) {
    const buttons = deepQueryAll(gallery, 'button, [role="button"]');
    const scored = [];
    for (const button of buttons) {
      if (!(button instanceof Element) || !isActuallyVisible(button)) continue;
      const label = [button.getAttribute('aria-label'), button.getAttribute('title'),
        button.getAttribute('data-testid')].filter(Boolean).join(' ').toLowerCase();
      const icon = !!button.querySelector('svg[icon-name="caret-right"], svg[icon-name="arrow-right"]');
      const slot = button.closest('[slot="nextButton"], [slot="next-button"]');
      let score = 0;
      if (slot) score += 2000;
      if (/\bnext\b.*\b(page|slide|image|photo|media|item)\b/.test(label)) score += 1500;
      else if (/\bnext\b/.test(label)) score += 1100;
      if (icon) score += 500;
      if (button.closest('[slot="prevButton"], [slot="prev-button"]')) score -= 3000;
      if (/previous|prev|back/.test(label)) score -= 3000;
      if (score > 0) scored.push({ button, score });
    }
    scored.sort((a, b) => b.score - a.score);
    return scored[0]?.button || null;
  }

  function isDisabled(button) {
    return button.disabled === true || button.matches(':disabled, [disabled]') ||
      button.getAttribute('aria-disabled') === 'true' ||
      button.closest('[aria-disabled="true"]') !== null;
  }

  function sanitize(part, max = 65) {
    const text = String(part || '').replace(/[<>:"/\\|?*\x00-\x1f]/g, '_')
      .replace(/[. ]+$/g, '').replace(/\s+/g, ' ').trim().slice(0, max);
    const reserved = /^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])$/i;
    return reserved.test(text) ? `_${text}` : text || 'untitled';
  }

  function saveFolder(gallery) {
    const post = gallery.closest('shreddit-post');
    const postId = (gallery.getAttribute('post-id') || post?.getAttribute('id') ||
      location.pathname.match(/(?:comments|gallery)\/([a-z0-9]+)/i)?.[1] || 'reddit');
    let title = document.querySelector('h1')?.textContent || document.title || 'Gallery';
    title = title.replace(/\s*:\s*r\/[^:]+$/i, '').replace(/\s*[|–-]\s*Reddit\s*$/i, '');
    return `Reddit Carousel/${sanitize(title, 65)}_${sanitize(postId, 32)}`;
  }

  function fileName(selection, imageURL, serial) {
    const pathname = new URL(imageURL).pathname;
    const original = decodeURIComponent(pathname.split('/').pop() || 'image');
    let ext = original.match(/\.(jpe?g|png|gif|webp|avif)$/i)?.[1]?.toLowerCase() || 'jpg';
    if (ext === 'jpeg') ext = 'jpg';
    const basename = sanitize(identityFromURL(imageURL).replace(/\.[a-z0-9]+$/i, ''), 58);
    const position = selection.index || serial;
    return `${saveFolder(selection.gallery)}/${String(position).padStart(4, '0')}_${basename}.${ext}`;
  }

  function download(url, filename) {
    return new Promise(resolve => {
      try {
        chrome.runtime.sendMessage({ type: 'RCD_DOWNLOAD', url, filename }, result => {
          const error = chrome.runtime.lastError;
          if (error) return resolve({ ok: false, error: error.message });
          resolve(result || { ok: false, error: 'No response from the download service.' });
        });
      } catch (error) {
        resolve({ ok: false, error: String(error?.message || error) });
      }
    });
  }

  async function waitForImage(selection) {
    const start = Date.now();
    while (!stopping && Date.now() - start < IMAGE_APPEAR_TIMEOUT_MS) {
      if (selection?.urls?.length) return selection;
      await sleep(160);
      selection = pageSelection();
    }
    return selection;
  }

  async function waitForNext(previous, minDelay) {
    const start = Date.now();
    let stableKey = '';
    let stableCount = 0;
    while (!stopping && Date.now() - start < TRANSITION_TIMEOUT_MS) {
      await sleep(135);
      const next = pageSelection();
      if (!next || !next.urls.length) continue;
      const changedIndex = previous.index !== null && next.index !== null && previous.index !== next.index;
      const changedIdentity = next.identity && next.identity !== previous.identity;
      if (changedIndex || changedIdentity) {
        const key = `${next.index || ''}#${next.identity || ''}`;
        if (key === stableKey) stableCount++;
        else { stableKey = key; stableCount = 1; }
        if (stableCount >= 2 && Date.now() - start >= minDelay) return next;
      }
    }
    return null;
  }

  async function run(options) {
    if (state.running) return;
    const delayMs = Math.max(150, Math.min(2500, Number(options?.delayMs) || 450));
    const visited = new Set();
    const seenMedia = new Set();
    stopping = false;
    update({ running: true, status: 'Locating the visible Reddit gallery…',
      downloaded: 0, skipped: 0, slide: null, total: null,
      lastFile: '', error: '', reason: '', startedAt: Date.now() });
    let selection = null;

    try {
      for (let attempts = 0; attempts < MAX_IMAGES && !stopping; attempts++) {
        selection = await waitForImage(selection || pageSelection());
        if (!selection) throw new Error('No visible Reddit image carousel was found. Open a gallery or its image lightbox first.');
        if (!selection.urls.length) throw new Error('The current slide has no downloadable Reddit image URL.');

        const position = `${selection.index ?? '?'}:${selection.identity}`;
        if (visited.has(position)) {
          update({ reason: 'Stopped: reached a previously visited carousel position.' });
          break;
        }
        visited.add(position);
        update({ slide: selection.index, total: selection.total });

        if (!seenMedia.has(selection.identity)) {
          update({ status: `Downloading image ${selection.index || attempts + 1}${selection.total ? ` of ${selection.total}` : ''}…` });
          let error = 'No usable source found.';
          let succeeded = false;
          for (const url of selection.urls) {
            if (stopping) break;
            const filename = fileName(selection, url, attempts + 1);
            const result = await download(url, filename);
            if (result.ok) {
              seenMedia.add(selection.identity);
              update({ downloaded: state.downloaded + 1, lastFile: filename });
              succeeded = true;
              break;
            }
            error = result.error || 'Download failed.';
            // A missing high-resolution CDN file can be retried with the
            // legitimate preview URL for the same slide.
          }
          if (stopping) break;
          if (!succeeded) throw new Error(`Could not save slide ${selection.index || attempts + 1}: ${error}`);
        } else {
          update({ skipped: state.skipped + 1, status: 'Duplicate image skipped.' });
        }

        if (stopping) break;
        const nextButton = findNextButton(selection.gallery);
        if (!nextButton || isDisabled(nextButton)) {
          update({ reason: 'Reached the end of the carousel.' });
          break;
        }
        update({ status: 'Advancing to next image…' });
        nextButton.click();
        const next = await waitForNext(selection, delayMs);
        if (!next) {
          if (stopping) break;
          // If Reddit removed/disabled the button while navigating, this is
          // a normal carousel end. Otherwise report it as a stall rather
          // than accidentally looping/download the same image again.
          const currentButton = selection.gallery.isConnected ? findNextButton(selection.gallery) : null;
          if (!currentButton || isDisabled(currentButton)) {
            update({ reason: 'Reached the end of the carousel.' });
          } else {
            update({ reason: 'Stopped: the next image did not appear after clicking Next.' });
          }
          break;
        }
        selection = next;
      }
      if (stopping) update({ reason: 'Stopped by user.' });
      else if (!state.reason) update({ reason: `Safety limit of ${MAX_IMAGES} positions reached.` });
      update({ status: state.reason });
    } catch (error) {
      update({ error: String(error?.message || error), status: 'Stopped due to an error.' });
    } finally {
      update({ running: false });
      stopping = false;
    }
  }

  chrome.runtime.onMessage.addListener((message, _sender, reply) => {
    if (!message || !String(message.type).startsWith('RCD_')) return false;
    if (message.type === 'RCD_STATUS') reply(snapshot());
    else if (message.type === 'RCD_STOP') {
      stopping = true;
      reply({ ok: true, state: snapshot() });
    } else if (message.type === 'RCD_START') {
      if (!state.running) void run(message.options || {});
      reply({ ok: true, state: snapshot() });
    }
    return false;
  });

  globalThis[GLOBAL_KEY] = { snapshot, pageSelection, findNextButton };
})();
