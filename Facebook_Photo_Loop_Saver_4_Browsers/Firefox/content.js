(() => {
  "use strict";

  // Prevent duplicate listeners when the background script injects us on demand.
  if (window.__FB_PHOTO_LOOP_SAVER_LOADED__) return;
  window.__FB_PHOTO_LOOP_SAVER_LOADED__ = true;

  const state = {
    active: false,
    runId: 0,
    toast: null,
  };

  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function isCurrentRun(runId) {
    return state.active && state.runId === runId;
  }

  function showToast(message, kind = "normal") {
    let box = state.toast;
    if (!box || !document.documentElement.contains(box)) {
      box = document.createElement("div");
      box.id = "__fb_photo_loop_saver_toast";
      Object.assign(box.style, {
        position: "fixed",
        top: "14px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: "2147483647",
        padding: "9px 13px",
        borderRadius: "8px",
        font: "600 13px/1.35 system-ui, -apple-system, Segoe UI, sans-serif",
        boxShadow: "0 4px 18px rgba(0,0,0,.28)",
        pointerEvents: "none",
        maxWidth: "min(760px, 88vw)",
        textAlign: "center",
      });
      document.documentElement.appendChild(box);
      state.toast = box;
    }

    box.textContent = message;
    box.style.background = kind === "error" ? "#7f1d1d" : kind === "done" ? "#14532d" : "#111827";
    box.style.color = "#fff";
    box.style.display = "block";
  }

  function hideToastLater(delay = 3500) {
    const box = state.toast;
    if (!box) return;
    setTimeout(() => {
      if (box === state.toast && !state.active) {
        box.style.display = "none";
      }
    }, delay);
  }

  function imageVisible(img) {
    if (!img || !img.isConnected) return false;
    const rect = img.getBoundingClientRect();
    if (rect.width < 120 || rect.height < 120) return false;
    if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) return false;

    const style = getComputedStyle(img);
    return style.display !== "none" &&
           style.visibility !== "hidden" &&
           Number(style.opacity || "1") > 0;
  }

  function pickBestPhotoImage() {
    let best = null;
    let bestScore = -1;

    for (const img of Array.from(document.images)) {
      if (!imageVisible(img)) continue;

      const src = (img.currentSrc || img.src || "").trim();
      if (!src || src.startsWith("data:") || src.startsWith("blob:")) continue;

      const naturalArea = Math.max(1, (img.naturalWidth || 0) * (img.naturalHeight || 0));
      const rect = img.getBoundingClientRect();
      const renderedArea = Math.max(1, rect.width * rect.height);

      // Ignore small UI images / avatars.
      if (naturalArea < 50000 && renderedArea < 50000) continue;

      let score = naturalArea + renderedArea * 3;

      if (/fbcdn|scontent/i.test(src)) score *= 1.7;
      if (img.closest('[role="dialog"]')) score *= 1.5;

      const cx = rect.left + rect.width / 2;
      const cy = rect.top + rect.height / 2;
      const dx = Math.abs(cx - innerWidth / 2) / Math.max(1, innerWidth);
      const dy = Math.abs(cy - innerHeight / 2) / Math.max(1, innerHeight);
      score *= Math.max(0.55, 1.25 - dx - dy * 0.35);

      if (score > bestScore) {
        bestScore = score;
        best = img;
      }
    }

    return best;
  }

  function getPhotoId() {
    try {
      const url = new URL(location.href);
      for (const key of ["fbid", "photo_id", "story_fbid"]) {
        const value = url.searchParams.get(key);
        if (value && /^\d+$/.test(value)) return value;
      }

      const pathMatches = [
        /\/photos\/[^/]+\/(\d+)/i,
        /\/photos\/(\d+)/i,
        /\/photo\/(\d+)/i,
      ];

      for (const re of pathMatches) {
        const m = url.pathname.match(re);
        if (m) return m[1];
      }
    } catch (_) {}

    const m = location.href.match(/[?&](?:fbid|photo_id|story_fbid)=(\d+)/i);
    return m ? m[1] : "";
  }

  function canonicalImageSrc(src) {
    try {
      const u = new URL(src, location.href);
      // Facebook CDN query parameters can be temporary. The path identifies
      // the underlying displayed image well enough for loop detection.
      return `${u.origin}${u.pathname}`;
    } catch (_) {
      return src.split("#")[0].split("?")[0];
    }
  }

  function guessExtension(src) {
    try {
      const path = new URL(src, location.href).pathname.toLowerCase();
      const m = path.match(/\.(jpe?g|png|webp|gif|avif)$/);
      if (m) {
        const ext = m[1] === "jpeg" ? "jpg" : m[1];
        return ext;
      }
    } catch (_) {}
    return "jpg";
  }

  function snapshotCurrentPhoto() {
    const img = pickBestPhotoImage();
    if (!img) return null;

    const src = (img.currentSrc || img.src || "").trim();
    if (!src) return null;

    const id = getPhotoId();
    const canonicalSrc = canonicalImageSrc(src);

    return {
      img,
      src,
      id,
      key: `${id ? `id:${id}|` : ""}src:${canonicalSrc}`,
      width: img.naturalWidth || 0,
      height: img.naturalHeight || 0,
    };
  }

  async function waitForPhoto(runId, {
    differentFrom = null,
    timeoutMs = 10000,
    stableSamples = 2,
  } = {}) {
    const deadline = Date.now() + timeoutMs;
    let lastKey = "";
    let stable = 0;

    while (Date.now() < deadline && isCurrentRun(runId)) {
      const photo = snapshotCurrentPhoto();

      if (photo && photo.img.complete && photo.width > 0 && photo.height > 0) {
        const isDifferent = !differentFrom || photo.key !== differentFrom;
        if (isDifferent) {
          if (photo.key === lastKey) {
            stable += 1;
          } else {
            lastKey = photo.key;
            stable = 1;
          }

          if (stable >= stableSamples) return photo;
        }
      }

      await sleep(220);
    }

    return null;
  }

  function dispatchRightArrow() {
    const init = {
      key: "ArrowRight",
      code: "ArrowRight",
      keyCode: 39,
      which: 39,
      bubbles: true,
      cancelable: true,
      composed: true,
    };

    // Dispatch on document so Facebook's viewer-level keyboard listener can see it.
    document.dispatchEvent(new KeyboardEvent("keydown", init));
    document.dispatchEvent(new KeyboardEvent("keyup", init));
  }

  function findNextButton() {
    const candidates = Array.from(document.querySelectorAll(
      'button, [role="button"], a[role="button"]'
    ));

    const labelMatchers = [
      /\bnext photo\b/i,
      /\bnext image\b/i,
      /^\s*next\s*$/i,
      /ảnh tiếp theo/i,
      /^\s*tiếp\s*$/i,
      /tiếp theo/i,
      /hình tiếp theo/i,
    ];

    let best = null;
    let bestScore = -Infinity;

    for (const el of candidates) {
      const rect = el.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      if (rect.bottom <= 0 || rect.right <= 0 || rect.top >= innerHeight || rect.left >= innerWidth) continue;

      const style = getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || "1") <= 0) continue;

      const label = [
        el.getAttribute("aria-label") || "",
        el.getAttribute("title") || "",
        el.textContent || "",
      ].join(" ").trim();

      let score = 0;
      if (labelMatchers.some((re) => re.test(label))) score += 1000;

      // Facebook's next-photo control normally sits on the right side,
      // vertically near the middle of the viewer.
      const centerX = rect.left + rect.width / 2;
      const centerY = rect.top + rect.height / 2;
      if (centerX > innerWidth * 0.60) score += 80;
      if (centerX > innerWidth * 0.78) score += 80;
      score -= Math.abs(centerY - innerHeight / 2) / 12;

      // Avoid giant containers and tiny invisible-ish hit targets.
      const area = rect.width * rect.height;
      if (area > 150 && area < 50000) score += 20;
      if (!label && centerX < innerWidth * 0.72) continue;

      if (score > bestScore) {
        bestScore = score;
        best = el;
      }
    }

    return bestScore >= 120 ? best : null;
  }

  async function advanceToNextPhoto(runId, previousKey) {
    // First do exactly what the user requested: send Right Arrow.
    dispatchRightArrow();

    let next = await waitForPhoto(runId, {
      differentFrom: previousKey,
      timeoutMs: 3200,
      stableSamples: 2,
    });
    if (next) return next;

    // Some sites ignore synthetic keyboard events. In that case, click the
    // actual visible "Next photo" / right-arrow control.
    const nextButton = findNextButton();
    if (nextButton) {
      nextButton.click();
      next = await waitForPhoto(runId, {
        differentFrom: previousKey,
        timeoutMs: 5000,
        stableSamples: 2,
      });
      if (next) return next;
    }

    // One final retry in case Facebook was briefly busy rendering.
    dispatchRightArrow();
    return await waitForPhoto(runId, {
      differentFrom: previousKey,
      timeoutMs: 5000,
      stableSamples: 2,
    });
  }

  function runtimeMessage(message) {
    if (globalThis.browser?.runtime?.sendMessage) {
      return globalThis.browser.runtime.sendMessage(message);
    }

    return new Promise((resolve, reject) => {
      try {
        chrome.runtime.sendMessage(message, (response) => {
          const err = chrome.runtime.lastError;
          if (err) reject(new Error(err.message));
          else resolve(response);
        });
      } catch (err) {
        reject(err);
      }
    });
  }

  async function requestDownload(photo, sequence, runFolder) {
    const ext = guessExtension(photo.src);
    const safeId = photo.id || `photo_${String(sequence).padStart(4, "0")}`;
    const filename = `${runFolder}/${String(sequence).padStart(4, "0")}_fbid_${safeId}.${ext}`;

    const response = await runtimeMessage({
      type: "download-photo",
      url: photo.src,
      filename,
    });

    if (!response || response.ok !== true) {
      throw new Error(response?.error || "Image did not finish downloading.");
    }

    // The background script only returns ok=true AFTER downloads.search() says
    // state=complete, the file still exists, and its size is non-zero.
    if (response.state !== "complete" || response.exists !== true || !(Number(response.fileSize) > 0)) {
      throw new Error("Browser did not verify a completed, non-empty image file.");
    }

    return { filename, fileSize: Number(response.fileSize) };
  }

  async function getPreviouslyDownloadedFbids() {
    try {
      const response = await runtimeMessage({ type: "get-downloaded-fbids" });
      if (!response || response.ok !== true || !Array.isArray(response.fbids)) {
        return new Set();
      }
      return new Set(response.fbids.filter((id) => /^\d+$/.test(String(id))));
    } catch (_) {
      // If history lookup fails, downloading is still safe; it simply cannot
      // skip files from older runs for this session.
      return new Set();
    }
  }

  async function sendStatus(active, text = "") {
    try {
      await runtimeMessage({ type: "status", active, text });
    } catch (_) {}
  }

  function makeRunFolder() {
    const stamp = new Date().toISOString().replace(/[:.]/g, "-");
    return `Facebook_Photo_Loop_Saver/${stamp}`;
  }

  async function runSaver(runId) {
    const seen = new Set();
    let saved = 0;
    let skippedExisting = 0;
    let processed = 0;
    let stopReason = "";
    const runFolder = makeRunFolder();

    try {
      showToast("Facebook Photo Saver: checking previous downloads…");
      await sendStatus(true, "Checking previous downloads");

      // Persistent across extension runs: query browser download history for
      // verified completed files that still exist under our saver folder.
      const previouslyDownloadedFbids = await getPreviouslyDownloadedFbids();

      showToast(
        `Facebook Photo Saver: ${previouslyDownloadedFbids.size} previous image${previouslyDownloadedFbids.size === 1 ? "" : "s"} found. Finding first photo…`
      );

      let current = await waitForPhoto(runId, { timeoutMs: 10000, stableSamples: 2 });
      if (!current) {
        throw new Error("No large visible Facebook photo was found. Open a photo in the viewer first.");
      }

      const startKey = current.key;

      while (isCurrentRun(runId)) {
        // 'processed' includes both newly downloaded and previously-downloaded
        // photos, so an all-skipped album still detects the loop correctly.
        if (processed > 0 && current.key === startKey) {
          stopReason = `Loop detected. Saved ${saved} new, skipped ${skippedExisting} already downloaded.`;
          break;
        }

        // Secondary safety stop for unusual Facebook viewer behavior.
        if (seen.has(current.key)) {
          stopReason = `A previously seen photo appeared again. Saved ${saved} new, skipped ${skippedExisting} already downloaded.`;
          break;
        }

        if (current.id && previouslyDownloadedFbids.has(current.id)) {
          // Previous-run file is already complete and still exists according
          // to the browser downloads database. Do not download a duplicate.
          seen.add(current.key);
          skippedExisting += 1;
          processed += 1;
          showToast(`Already downloaded FBID ${current.id}. Skipping it…`);
          await sendStatus(true, `Saved ${saved}; skipped ${skippedExisting}`);
        } else {
          showToast(`Downloading image ${saved + 1}… waiting for completion`);

          // This await does NOT resolve merely because a download started.
          // It resolves only after the background verifies state=complete,
          // exists=true, and fileSize>0. If verification fails, it throws and
          // the loop stops here WITHOUT pressing Right Arrow.
          const completed = await requestDownload(current, saved + 1, runFolder);

          seen.add(current.key);
          if (current.id) previouslyDownloadedFbids.add(current.id);
          saved += 1;
          processed += 1;
          showToast(`Verified image ${saved} (${completed.fileSize.toLocaleString()} bytes). Pressing Right Arrow…`);
          await sendStatus(true, `Verified ${saved}; skipped ${skippedExisting}`);
        }

        // High safety ceiling only; normal runs stop by detecting the loop.
        if (processed >= 10000) {
          stopReason = "Safety limit reached at 10,000 photos processed.";
          break;
        }

        // CRITICAL: execution reaches this line only after either:
        //   A) a NEW image download was verified complete, or
        //   B) the FBID was found as a completed existing previous download.
        const next = await advanceToNextPhoto(runId, current.key);
        if (!isCurrentRun(runId)) {
          stopReason = `Stopped manually. Saved ${saved} new, skipped ${skippedExisting} already downloaded.`;
          break;
        }

        if (!next) {
          stopReason = `Could not move to a different photo. Saved ${saved} new, skipped ${skippedExisting} already downloaded.`;
          break;
        }

        current = next;
      }

      if (!stopReason) {
        stopReason = `Stopped. Saved ${saved} new, skipped ${skippedExisting} already downloaded.`;
      }

      if (state.runId === runId) {
        state.active = false;
        showToast(`Facebook Photo Saver: ${stopReason}`, "done");
        hideToastLater(6000);
        await sendStatus(false, stopReason);
      }
    } catch (err) {
      if (state.runId === runId) {
        state.active = false;
        const message = err instanceof Error ? err.message : String(err);
        showToast(`Facebook Photo Saver error: ${message} Right Arrow was NOT pressed for this photo.`, "error");
        hideToastLater(8000);
        await sendStatus(false, message);
      }
    }
  }

  function toggleSaver() {
    if (state.active) {
      state.active = false;
      state.runId += 1;
      showToast("Facebook Photo Saver: stopped manually.", "done");
      hideToastLater();
      sendStatus(false, "Stopped manually");
      return;
    }

    state.active = true;
    const runId = ++state.runId;
    runSaver(runId);
  }

  const runtime = globalThis.browser?.runtime || globalThis.chrome?.runtime;
  runtime?.onMessage?.addListener((message) => {
    if (message?.type === "toggle-saver") {
      toggleSaver();
    }
  });
})();
