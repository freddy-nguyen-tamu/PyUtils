(async () => {
  const MAX_SCROLLS = 120;      // increase for more posts
  const SCROLL_DELAY_MS = 1300; // increase if your internet is slow

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const seen = new Set();

  function cleanUrl(u) {
    if (!u) return null;
    u = String(u).replaceAll("&amp;", "&").replaceAll("\\/", "/").trim();

    try {
      u = new URL(u, location.href).href;
    } catch {
      return null;
    }

    // Keep Reddit-hosted media only.
    const isRedditMedia =
      u.includes("i.redd.it") ||
      u.includes("preview.redd.it") ||
      u.includes("external-preview.redd.it") ||
      u.includes("v.redd.it") ||
      u.includes("reddit.com/gallery/");

    const looksLikeMedia =
      /\.(jpg|jpeg|png|gif|webp|mp4|webm)(\?|$)/i.test(u) ||
      u.includes("reddit.com/gallery/");

    if (!isRedditMedia || !looksLikeMedia) return null;

    return u;
  }

  function add(u) {
    const cleaned = cleanUrl(u);
    if (cleaned) seen.add(cleaned);
  }

  function scanRoot(root) {
    // New Reddit post containers often store the actual media here.
    root.querySelectorAll?.("shreddit-post[content-href]").forEach(el => {
      add(el.getAttribute("content-href"));
    });

    // Images/videos/sources.
    root.querySelectorAll?.("img, source, video").forEach(el => {
      add(el.getAttribute("src"));
      add(el.getAttribute("poster"));
      add(el.getAttribute("data-src"));

      const srcset = el.getAttribute("srcset");
      if (srcset) {
        for (const part of srcset.split(",")) {
          add(part.trim().split(/\s+/)[0]);
        }
      }
    });

    // Links.
    root.querySelectorAll?.("a[href]").forEach(el => {
      add(el.getAttribute("href"));
    });

    // Shadow DOM support.
    root.querySelectorAll?.("*").forEach(el => {
      if (el.shadowRoot) scanRoot(el.shadowRoot);
    });
  }

  function scanHTML() {
    const html = document.documentElement.innerHTML;
    const matches = html.match(/https?:\/\/[^"'<>\\\s]+/g) || [];
    for (const m of matches) add(m);
  }

  let noChangeRounds = 0;
  let lastCount = 0;
  let lastHeight = 0;

  for (let i = 0; i < MAX_SCROLLS; i++) {
    scanRoot(document);
    scanHTML();

    console.log(`Scroll ${i + 1}/${MAX_SCROLLS}: found ${seen.size} media/gallery URLs`);

    window.scrollTo(0, document.documentElement.scrollHeight);
    await sleep(SCROLL_DELAY_MS);

    const height = document.documentElement.scrollHeight;
    if (seen.size === lastCount && height === lastHeight) {
      noChangeRounds++;
    } else {
      noChangeRounds = 0;
    }

    lastCount = seen.size;
    lastHeight = height;

    // Stop after several rounds where nothing new loads.
    if (noChangeRounds >= 6) break;
  }

  scanRoot(document);
  scanHTML();

  const urls = [...seen].sort();

  const text = urls.join("\n");
  const blob = new Blob([text], { type: "text/plain" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `reddit-media-urls-${location.pathname.replaceAll("/", "_") || "subreddit"}.txt`;
  document.body.appendChild(a);
  a.click();
  a.remove();

  console.log(`DONE. Saved ${urls.length} URLs to a text file.`);
  console.log(urls);
})();