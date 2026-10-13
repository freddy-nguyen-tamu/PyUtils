/* Google Photos Auto Fit — Manifest V3 content script.
 * Only adjusts an opened photo, never album thumbnails or browser zoom.
 * Native Google Photos wheel-zoom is tried first; small images that Google
 * refuses to zoom are enlarged using the independent CSS `scale` property.
 */
(() => {
  "use strict";

  const DEFAULTS = Object.freeze({
    enabled: true,
    edgeGap: 0,
    tryNativeZoom: true
  });

  let settings = { ...DEFAULTS };
  let active = null;
  let timer = 0;
  let nextSessionId = 0;
  const originalScales = new WeakMap();
  const MAX_SCALE = 100;
  const FIT_EPSILON = 0.012;

  const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

  function visible(element) {
    if (!element || !element.isConnected || element.getClientRects().length === 0) return false;
    const style = getComputedStyle(element);
    return style.visibility !== "hidden" && style.display !== "none" &&
      style.opacity !== "0";
  }

  // The saved Google Photos HTML contains several preloaded neighboring photos
  // and even more than one viewer. Only the non-hidden, foreground photo counts.
  function currentPhoto() {
    const viewers = document.querySelectorAll("c-wiz.AGwIld");
    let best = null;
    let bestScore = -1;

    for (const viewer of viewers) {
      if (!visible(viewer)) continue;
      const viewport = viewer.querySelector(".VmkiKe");
      if (!visible(viewport)) continue;
      const viewRect = viewport.getBoundingClientRect();
      if (viewRect.width < 80 || viewRect.height < 80) continue;

      for (const photo of viewport.querySelectorAll("c-wiz.A7ANFe")) {
        if (!visible(photo)) continue;
        const stage = photo.querySelector(".XXKL8c");
        const transition = stage?.querySelector(".TTxCae");
        const image = stage?.querySelector("img.BiCYpc");
        if (!stage || !image || !visible(stage) || !visible(image)) continue;
        if (transition && Number.parseFloat(getComputedStyle(transition).opacity) < 0.96) continue;
        if (!image.complete || image.naturalWidth < 16 || image.naturalHeight < 16) continue;
        const rect = stage.getBoundingClientRect();
        if (rect.width < 16 || rect.height < 16) continue;
        // The active photo is visible, opaque and intersects its viewing area.
        const score = Math.max(0, Math.min(rect.right, viewRect.right) - Math.max(rect.left, viewRect.left)) *
          Math.max(0, Math.min(rect.bottom, viewRect.bottom) - Math.max(rect.top, viewRect.top));
        if (score > bestScore) {
          best = { viewport, viewer, photo, stage, image,
            imageUrl: image.currentSrc || image.src };
          bestScore = score;
        }
      }
    }
    return best;
  }

  function storeOriginalScale(stage) {
    if (!originalScales.has(stage)) {
      originalScales.set(stage, {
        value: stage.style.getPropertyValue("scale"),
        priority: stage.style.getPropertyPriority("scale"),
        origin: stage.style.getPropertyValue("transform-origin"),
        originPriority: stage.style.getPropertyPriority("transform-origin")
      });
    }
  }

  function restoreStage(stage) {
    if (!stage || !originalScales.has(stage)) return;
    const previous = originalScales.get(stage);
    if (previous.value) {
      stage.style.setProperty("scale", previous.value, previous.priority);
    } else {
      stage.style.removeProperty("scale");
    }
    if (previous.origin) {
      stage.style.setProperty("transform-origin", previous.origin, previous.originPriority);
    } else {
      stage.style.removeProperty("transform-origin");
    }
    originalScales.delete(stage);
  }

  function discardActive() {
    if (!active) return;
    active.cancelled = true;
    restoreStage(active.stage);
    active = null;
  }

  function stillCurrent(session) {
    return settings.enabled && active === session && !session.cancelled &&
      session.stage.isConnected && session.image.isConnected &&
      (session.image.currentSrc || session.image.src) === session.imageUrl;
  }

  function geometry(session) {
    if (!stillCurrent(session)) return null;
    const view = session.viewport.getBoundingClientRect();
    const photo = session.stage.getBoundingClientRect();
    if (view.width < 80 || view.height < 80 || photo.width < 16 || photo.height < 16) return null;

    // getBoundingClientRect includes the native Google Photos transforms and
    // our own scale. Dividing out only our scale avoids compounding on reflow.
    const width = photo.width / session.cssScale;
    const height = photo.height / session.cssScale;
    const availableWidth = Math.max(1, view.width - settings.edgeGap * 2);
    const availableHeight = Math.max(1, view.height - settings.edgeGap * 2);
    const desired = Math.min(MAX_SCALE, availableWidth / width, availableHeight / height);
    return { desired, width, height, availableWidth, availableHeight,
      viewWidth: view.width, viewHeight: view.height };
  }

  function setVisualScale(session, factor) {
    if (!stillCurrent(session)) return;
    const next = factor <= 1 + FIT_EPSILON ? 1 : Math.max(1, Math.min(MAX_SCALE, factor));
    if (Math.abs(session.cssScale - next) < 0.003) return;
    if (next === 1) {
      restoreStage(session.stage);
    } else {
      storeOriginalScale(session.stage);
      // `scale` composes with, rather than replaces, Photos' `transform`.
      // Center-based scaling retains Photos' normal photo alignment and clips
      // only at the existing native viewer boundaries.
      session.stage.style.setProperty("scale", String(Number(next.toFixed(6))), "important");
      session.stage.style.setProperty("transform-origin", "50% 50%");
    }
    session.cssScale = next;
  }

  function applyExactFit(session) {
    const info = geometry(session);
    if (!info || session.manual) return;
    setVisualScale(session, info.desired);
    session.lastView = [info.viewWidth, info.viewHeight];
    session.lastBase = [info.width, info.height];
  }

  // A script-generated wheel event is deliberately untrusted. Google Photos
  // may accept it or reject it (especially on low-resolution photos). We test
  // whether the actual photo dimensions change and always have a fallback.
  function sendNativeZoomTick(session) {
    const rect = session.viewport.getBoundingClientRect();
    const photo = session.stage.getBoundingClientRect();
    const x = Math.max(rect.left + 1, Math.min(rect.right - 1, photo.left + photo.width / 2));
    const y = Math.max(rect.top + 1, Math.min(rect.bottom - 1, photo.top + photo.height / 2));
    const hovered = document.elementFromPoint(x, y);
    const target = hovered && session.viewport.contains(hovered) ? hovered : session.photo;
    target.dispatchEvent(new WheelEvent("wheel", {
      bubbles: true,
      cancelable: true,
      composed: true,
      deltaX: 0,
      deltaY: -100,
      deltaMode: WheelEvent.DOM_DELTA_PIXEL,
      clientX: x,
      clientY: y,
      view: window
    }));
  }

  async function initializeFit(session) {
    session.busy = true;
    try {
      await delay(90); // Allow Google Photos to finish its slide transition.
      if (!stillCurrent(session) || session.manual) return;
      let info = geometry(session);
      if (!info) return;

      if (settings.tryNativeZoom && info.desired > 1 + FIT_EPSILON) {
        // Try once. If site handlers do not respond, do not waste time sending
        // more events or endlessly retrying them on each DOM mutation.
        let expectedNativeGain = 1.25;
        for (let tick = 0; tick < 24; tick++) {
          if (!stillCurrent(session) || session.manual) return;
          info = geometry(session);
          if (!info || info.desired <= expectedNativeGain * 1.015) break;
          const beforeWidth = info.width;
          const beforeHeight = info.height;
          sendNativeZoomTick(session);
          await delay(tick === 0 ? 160 : 95);
          if (!stillCurrent(session) || session.manual) return;
          const updated = geometry(session);
          if (!updated) break;
          const progress = Math.max(updated.width / beforeWidth, updated.height / beforeHeight);
          if (progress < 1.007) break;
          expectedNativeGain = progress;
          // Stop before the next discrete native wheel tick would crop an edge.
          // An exact CSS fraction finishes the final amount without overshoot.
          if (updated.desired <= expectedNativeGain * 1.015) break;
        }
      }
      applyExactFit(session);
    } catch (_error) {
      // Site DOM and zoom semantics can change; keep the page functional.
      if (stillCurrent(session)) applyExactFit(session);
    } finally {
      session.busy = false;
    }
  }

  function isSamePhoto(candidate, session) {
    return session && candidate && session.stage === candidate.stage &&
      session.image === candidate.image && session.imageUrl === candidate.imageUrl &&
      session.viewport === candidate.viewport;
  }

  function scan() {
    if (!settings.enabled) {
      discardActive();
      return;
    }
    const candidate = currentPhoto();
    if (!candidate) {
      discardActive();
      return;
    }
    if (!isSamePhoto(candidate, active)) {
      discardActive();
      const session = {
        ...candidate,
        id: ++nextSessionId,
        cancelled: false,
        manual: false,
        busy: false,
        cssScale: 1,
        lastView: null,
        lastBase: null
      };
      active = session;
      void initializeFit(session);
      return;
    }
    if (active.busy || active.manual) return;
    const info = geometry(active);
    if (!info) return;
    if (!active.lastView || !active.lastBase ||
        Math.abs(info.viewWidth - active.lastView[0]) > 1 ||
        Math.abs(info.viewHeight - active.lastView[1]) > 1 ||
        Math.abs(info.width - active.lastBase[0]) > 1 ||
        Math.abs(info.height - active.lastBase[1]) > 1 ||
        Math.abs(info.desired - active.cssScale) > 0.015) {
      applyExactFit(active);
    }
  }

  function scheduleScan() {
    if (timer) return;
    timer = setTimeout(() => {
      timer = 0;
      scan();
    }, 110);
  }

  // Respect intentional user zooming, panning, and mouse interaction on the
  // open image. These listeners never cancel, modify or synthesize user input.
  function userIsAdjusting(event) {
    if (!event.isTrusted || !active || !active.viewport.contains(event.target)) return;
    active.manual = true;
  }
  document.addEventListener("wheel", userIsAdjusting, { capture: true, passive: true });
  document.addEventListener("pointerdown", event => {
    if (!event.isTrusted || !active || !active.viewport.contains(event.target)) return;
    active.manual = true;
  }, { capture: true, passive: true });

  const observer = new MutationObserver(scheduleScan);
  function start() {
    const root = document.body || document.documentElement;
    if (!root) return;
    observer.observe(root, {
      childList: true,
      subtree: true,
      attributes: true,
      attributeFilter: ["class", "style", "src", "hidden", "aria-hidden"]
    });
    window.addEventListener("resize", scheduleScan, { passive: true });
    window.addEventListener("pageshow", scheduleScan);
    // Backstop for SPA photo switches not reflected by URL or DOM structure.
    setInterval(scan, 1200);
    scheduleScan();
  }

  try {
    chrome.storage.sync.get(DEFAULTS, saved => {
      if (!chrome.runtime.lastError && saved) settings = { ...DEFAULTS, ...saved };
      settings.edgeGap = Math.max(0, Math.min(64, Number(settings.edgeGap) || 0));
      start();
    });
    chrome.storage.onChanged.addListener((changes, area) => {
      if (area !== "sync") return;
      let refresh = false;
      for (const key of Object.keys(DEFAULTS)) {
        if (changes[key]) {
          settings[key] = changes[key].newValue ?? DEFAULTS[key];
          refresh = true;
        }
      }
      settings.edgeGap = Math.max(0, Math.min(64, Number(settings.edgeGap) || 0));
      if (refresh) {
        discardActive();
        scheduleScan();
      }
    });
  } catch (_error) {
    // Graceful startup in restrictive browsing contexts.
    start();
  }
})();
