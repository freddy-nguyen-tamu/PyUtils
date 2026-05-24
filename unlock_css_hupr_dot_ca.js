(async () => {
  const ZIP_NAME = "website-css-files.zip";

  if (!window.JSZip) {
    await new Promise((resolve, reject) => {
      const s = document.createElement("script");
      s.src = "https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js";
      s.onload = resolve;
      s.onerror = reject;
      document.head.appendChild(s);
    });
  }

  const zip = new JSZip();
  const visitedCss = new Set();
  const visitedAssets = new Set();
  const failed = [];

  function cleanUrl(url) {
    return String(url || "").replace(/&amp;/g, "&").trim();
  }

  function isHttpUrl(url) {
    try {
      const u = new URL(url, location.href);
      return u.protocol === "http:" || u.protocol === "https:";
    } catch {
      return false;
    }
  }

  function absolutizeUrl(rawUrl, baseUrl = location.href) {
    rawUrl = cleanUrl(rawUrl);

    if (
      !rawUrl ||
      rawUrl.startsWith("data:") ||
      rawUrl.startsWith("blob:") ||
      rawUrl.startsWith("#") ||
      rawUrl.startsWith("chrome-extension:")
    ) {
      return null;
    }

    const abs = new URL(rawUrl, baseUrl).href;
    return isHttpUrl(abs) ? abs : null;
  }

  function makeSafePath(url, fallbackName = "file") {
    try {
      const u = new URL(url);
      let path = `${u.hostname}${u.pathname}`;

      if (path.endsWith("/")) path += "index";
      path = path.replace(/^\/+/, "");
      path = path.replace(/[<>:"\\|?*]/g, "_");

      const query = u.search
        ? "_" + u.search.slice(1).replace(/[<>:"\\|?*&=]/g, "_")
        : "";

      return path + query;
    } catch {
      return fallbackName.replace(/[<>:"\\|?*]/g, "_");
    }
  }

  async function fetchAny(url, as = "text") {
    const sameOrigin = new URL(url).origin === location.origin;

    const res = await fetch(url, {
      credentials: sameOrigin ? "include" : "omit",
      cache: "force-cache"
    });

    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText}`);
    }

    return as === "blob" ? res.blob() : res.text();
  }

  function extractImports(cssText, baseUrl) {
    const imports = [];
    const re = /@import\s+(?:url\()?['"]?([^'")\s;]+)['"]?\)?[^;]*;/g;

    let match;
    while ((match = re.exec(cssText))) {
      const abs = absolutizeUrl(match[1], baseUrl);
      if (abs) imports.push(abs);
    }

    return imports;
  }

  function extractAssetUrls(cssText, baseUrl) {
    const urls = [];
    const re = /url\(\s*(['"]?)(.*?)\1\s*\)/g;

    let match;
    while ((match = re.exec(cssText))) {
      const abs = absolutizeUrl(match[2], baseUrl);
      if (abs) urls.push(abs);
    }

    return urls;
  }

  async function saveAsset(url) {
    if (visitedAssets.has(url)) return;
    visitedAssets.add(url);

    try {
      const blob = await fetchAny(url, "blob");
      zip.file("assets/" + makeSafePath(url), blob);
      console.log("Saved asset:", url);
    } catch (err) {
      failed.push({ type: "asset", url, error: String(err) });
      console.warn("Failed asset:", url, err);
    }
  }

  async function saveCss(url) {
    if (visitedCss.has(url)) return;
    visitedCss.add(url);

    try {
      const cssText = await fetchAny(url, "text");
      zip.file("css/" + makeSafePath(url, "style.css"), cssText);
      console.log("Saved CSS:", url);

      for (const importUrl of extractImports(cssText, url)) {
        await saveCss(importUrl);
      }

      for (const assetUrl of extractAssetUrls(cssText, url)) {
        await saveAsset(assetUrl);
      }
    } catch (err) {
      failed.push({ type: "css", url, error: String(err) });
      console.warn("Failed CSS:", url, err);
    }
  }

  const stylesheetLinks = [...document.querySelectorAll('link[rel~="stylesheet"][href]')]
    .map(link => absolutizeUrl(link.getAttribute("href")))
    .filter(Boolean);

  for (const href of stylesheetLinks) {
    await saveCss(href);
  }

  const inlineStyles = [...document.querySelectorAll("style")];

  for (let i = 0; i < inlineStyles.length; i++) {
    const style = inlineStyles[i];
    const id = style.id || style.getAttribute("data-styled") || `inline-style-${i + 1}`;
    const cssText = style.textContent || "";

    const filename = `inline/${String(i + 1).padStart(3, "0")}-${id}.css`
      .replace(/[<>:"\\|?*]/g, "_");

    zip.file(filename, cssText);

    for (const assetUrl of extractAssetUrls(cssText, location.href)) {
      await saveAsset(assetUrl);
    }
  }

  zip.file(
    "manifest.json",
    JSON.stringify(
      {
        page: location.href,
        createdAt: new Date().toISOString(),
        externalStylesheets: stylesheetLinks,
        inlineStyleCount: inlineStyles.length,
        failed
      },
      null,
      2
    )
  );

  const zipBlob = await zip.generateAsync({ type: "blob" });
  const objectUrl = URL.createObjectURL(zipBlob);

  const a = document.createElement("a");
  a.href = objectUrl;
  a.download = ZIP_NAME;
  a.dataset.noSwup = "true";
  a.setAttribute("data-no-swup", "");
  a.style.display = "none";

  a.addEventListener("click", e => {
    e.stopPropagation();
    e.stopImmediatePropagation();
  }, true);

  document.body.appendChild(a);
  a.click();

  setTimeout(() => {
    URL.revokeObjectURL(objectUrl);
    a.remove();
  }, 3000);

  console.log(`Done. Downloaded ${ZIP_NAME}`);
  console.table(failed);
})();