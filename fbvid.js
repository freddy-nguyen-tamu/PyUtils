(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  let lastHeight = 0;
  for (let i = 0; i < 20; i++) {
    window.scrollTo(0, document.body.scrollHeight);
    await sleep(1200);
    const h = document.body.scrollHeight;
    if (h === lastHeight) break;
    lastHeight = h;
  }

  window.scrollTo(0, 0);
  await sleep(500);

  const out = new Set();
  const add = (u) => {
    try {
      if (!u) return;
      u = String(u).replace(/\\\//g, "/").replace(/&amp;/g, "&");
      if (u.startsWith("/")) u = location.origin + u;
      const x = new URL(u, location.origin);
      const p = x.pathname;
      if (
        /^\/reel\/\d+\/?$/.test(p) ||
        (p === "/watch/" && x.searchParams.get("v")) ||
        /^\/[^/]+\/videos\/\d+\/?$/.test(p) ||
        /^\/share\/[rv]\/[^/]+\/?$/.test(p)
      ) {
        if (p === "/watch/") {
          const v = x.searchParams.get("v");
          x.search = v ? `?v=${v}` : "";
        } else {
          x.search = "";
        }
        x.hash = "";
        out.add(x.toString());
      }
    } catch {}
  };

  document.querySelectorAll("a[href]").forEach(a => {
    add(a.getAttribute("href"));
    add(a.href);
  });

  const html = document.documentElement.outerHTML;
  (html.match(/\/reel\/\d+\/?/g) || []).forEach(add);
  (html.match(/\/watch\/\?v=\d+/g) || []).forEach(add);
  (html.match(/\/[^/"'\s<>]+\/videos\/\d+\/?/g) || []).forEach(add);
  (html.match(/\/share\/[rv]\/[^/"'\s<>]+\/?/g) || []).forEach(add);

  const results = [...out].sort();
  console.log(`Found ${results.length} unique links`);
  console.log(results.join("\n"));
  copy(results.join("\n"));
  return results;
})();