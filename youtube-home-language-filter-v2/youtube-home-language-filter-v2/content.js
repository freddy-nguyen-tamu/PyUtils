(() => {
  "use strict";

  const DEFAULT_SETTINGS = {
    enabled: true,
    language: "english",
    customRegex: "",
    threshold: 0.25,
    minWordHits: 2
  };

  function isExactYouTubeHome() {
    return (
      location.protocol === "https:" &&
      location.hostname === "www.youtube.com" &&
      (location.pathname === "/" || location.pathname === "") &&
      location.search === "" &&
      location.hash === ""
    );
  }

  const LANGUAGE_RULES = {
    english: {
      type: "word",
      minHits: 2,
      regex: /\b(the|boys|and|for|you|your|with|from|this|that|what|when|where|why|how|who|are|was|were|will|would|can|could|should|have|has|had|not|but|about|into|over|after|before|more|most|new|best|first|last|official|video|music|song|movie|game|games|news|review|reaction|tutorial|explained|highlights|full|episode|season|part|live|trailer|clips|moments|story|stories|season|finale|interview|podcast|today|night|day|week|year|years|time|times|make|made|get|got|see|watch|look|like|try|trying|people|man|woman|men|women|kid|kids|world|life|love|funny|crazy|real|fake|good|bad|great|big|small|old|young|every|only|never|always)\b/giu
    },
    cjk: {
      type: "char",
      regex: /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uac00-\ud7af]/u
    },
    chinese: {
      type: "char",
      regex: /[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u
    },
    japanese: {
      type: "char",
      regex: /[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]/u
    },
    korean: {
      type: "char",
      regex: /[\uac00-\ud7af\u1100-\u11ff\u3130-\u318f]/u
    },
    arabic: {
      type: "char",
      regex: /[\u0600-\u06ff\u0750-\u077f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]/u
    },
    cyrillic: {
      type: "char",
      regex: /[\u0400-\u04ff\u0500-\u052f]/u
    },
    devanagari: {
      type: "char",
      regex: /[\u0900-\u097f]/u
    },
    hebrew: {
      type: "char",
      regex: /[\u0590-\u05ff]/u
    },
    thai: {
      type: "char",
      regex: /[\u0e00-\u0e7f]/u
    },
    vietnamese: {
      type: "char",
      regex: /[ăâđêôơưáàảãạắằẳẵặấầẩẫậéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵĂÂĐÊÔƠƯÁÀẢÃẠẮẰẲẴẶẤẦẨẪẬÉÈẺẼẸẾỀỂỄỆÍÌỈĨỊÓÒỎÕỌỐỒỔỖỘỚỜỞỠỢÚÙỦŨỤỨỪỬỮỰÝỲỶỸỴ]/u
    },
    custom: {
      type: "word",
      regex: null
    }
  };

  let settings = { ...DEFAULT_SETTINGS };
  let observer = null;
  let filterTimer = null;

  function getRule() {
    if (settings.language === "custom") {
      if (!settings.customRegex) return null;

      try {
        return {
          type: "word",
          minHits: Number(settings.minWordHits || 1),
          regex: new RegExp(settings.customRegex, "giu")
        };
      } catch {
        return null;
      }
    }

    return LANGUAGE_RULES[settings.language] || LANGUAGE_RULES.english;
  }

  function getCharScore(text, regex) {
    const cleaned = [...text.trim().replace(/\s+/g, "")];
    if (!cleaned.length) return 0;

    let matches = 0;
    for (const char of cleaned) {
      regex.lastIndex = 0;
      if (regex.test(char)) matches++;
    }

    return matches / cleaned.length;
  }

  function getWordHits(text, regex) {
    regex.lastIndex = 0;
    const matches = text.match(regex);
    return matches ? matches.length : 0;
  }

  function shouldFilterTitle(title) {
    const rule = getRule();
    if (!rule || !title) return false;

    if (rule.type === "word") {
      const hits = getWordHits(title, rule.regex);
      return hits >= Number(rule.minHits || settings.minWordHits || 2);
    }

    return getCharScore(title, rule.regex) >= Number(settings.threshold || DEFAULT_SETTINGS.threshold);
  }

  function findVideoCard(titleEl) {
    return (
      titleEl.closest("ytd-rich-item-renderer") ||
      titleEl.closest("ytd-video-renderer") ||
      titleEl.closest("ytd-grid-video-renderer") ||
      titleEl.closest("ytd-compact-video-renderer") ||
      titleEl.closest("ytd-reel-item-renderer") ||
      titleEl.closest("yt-lockup-view-model") ||
      titleEl.closest("ytd-rich-grid-media")
    );
  }

  function clearAllHidden() {
    document.querySelectorAll(".yt-lang-filter-hidden").forEach((el) => {
      el.classList.remove("yt-lang-filter-hidden");
      el.removeAttribute("data-yt-lang-filtered");
    });
  }

  function filterNow() {
    clearTimeout(filterTimer);

    if (!settings.enabled || !isExactYouTubeHome()) {
      clearAllHidden();
      stopObserver();
      return;
    }

    const titleSelectors = [
      "a#video-title-link",
      "a#video-title",
      "#video-title",
      "h3 a",
      "yt-formatted-string#video-title",
      "span#video-title"
    ];

    const titleElements = document.querySelectorAll(titleSelectors.join(","));

    for (const titleEl of titleElements) {
      const title = (titleEl.textContent || titleEl.getAttribute("title") || "").trim();
      if (!title) continue;

      const card = findVideoCard(titleEl);
      if (!card) continue;

      if (shouldFilterTitle(title)) {
        card.classList.add("yt-lang-filter-hidden");
        card.setAttribute("data-yt-lang-filtered", "true");
      } else {
        card.classList.remove("yt-lang-filter-hidden");
        card.removeAttribute("data-yt-lang-filtered");
      }
    }
  }

  function scheduleFilter() {
    clearTimeout(filterTimer);
    filterTimer = setTimeout(filterNow, 150);
  }

  function startObserver() {
    if (observer || !isExactYouTubeHome()) return;

    observer = new MutationObserver(scheduleFilter);
    observer.observe(document.documentElement, {
      childList: true,
      subtree: true
    });
  }

  function stopObserver() {
    if (!observer) return;
    observer.disconnect();
    observer = null;
  }

  function runForCurrentPage() {
    if (settings.enabled && isExactYouTubeHome()) {
      startObserver();
      scheduleFilter();
    } else {
      stopObserver();
      clearAllHidden();
    }
  }

  function loadSettings() {
    chrome.storage.sync.get(DEFAULT_SETTINGS, (stored) => {
      settings = { ...DEFAULT_SETTINGS, ...stored };
      runForCurrentPage();
    });
  }

  chrome.storage.onChanged.addListener((changes, area) => {
    if (area !== "sync") return;

    for (const [key, change] of Object.entries(changes)) {
      settings[key] = change.newValue;
    }

    runForCurrentPage();
  });

  window.addEventListener("yt-navigate-finish", loadSettings);
  window.addEventListener("popstate", loadSettings);

  const originalPushState = history.pushState;
  const originalReplaceState = history.replaceState;

  history.pushState = function (...args) {
    const result = originalPushState.apply(this, args);
    setTimeout(loadSettings, 0);
    return result;
  };

  history.replaceState = function (...args) {
    const result = originalReplaceState.apply(this, args);
    setTimeout(loadSettings, 0);
    return result;
  };

  loadSettings();
})();
