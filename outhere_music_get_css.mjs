/*
npm init -y
npm i playwright
npx playwright install chromium
node outhere_music_get_css.mjs
*/
import fs from "node:fs/promises";
import path from "node:path";
import crypto from "node:crypto";
import readline from "node:readline/promises";
import { stdin as input, stdout as output } from "node:process";
import { chromium } from "playwright";

const TARGET_URL = "https://outhere-music.com/en";

/*
  Output folder.
*/
const OUT_DIR = path.resolve("outhere-music-css");
const CSS_DIR = path.join(OUT_DIR, "css");
const ASSET_DIR = path.join(OUT_DIR, "assets");

/*
  Persistent browser profile folder.

  This is important because after you pass Cloudflare once,
  the verification cookie/session can be reused next time.
*/
const USER_DATA_DIR = path.resolve("playwright-outhere-profile");

await fs.mkdir(CSS_DIR, { recursive: true });
await fs.mkdir(ASSET_DIR, { recursive: true });

function hash(input) {
  return crypto.createHash("sha256").update(input).digest("hex").slice(0, 12);
}

function cleanName(input) {
  return input
    .replace(/^https?:\/\//, "")
    .replace(/[?#].*$/, "")
    .replace(/[^a-z0-9._-]+/gi, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 140);
}

function guessExtFromUrl(url) {
  try {
    const pathname = new URL(url).pathname;
    const ext = path.extname(pathname);
    return ext && ext.length <= 8 ? ext : "";
  } catch {
    return "";
  }
}

function isLikelyCssText(text) {
  return (
    text.includes("{") &&
    text.includes("}") &&
    !text.toLowerCase().includes("<html") &&
    !text.toLowerCase().includes("<!doctype")
  );
}

async function fetchBufferWithPageContext(page, url) {
  /*
    Use browser-context fetch instead of plain Node fetch so cookies from
    your manual Cloudflare verification are included.
  */
  const response = await page.request.get(url, {
    headers: {
      accept: "*/*",
      referer: TARGET_URL,
    },
    timeout: 120_000,
  });

  if (!response.ok()) {
    throw new Error(`${response.status()} ${response.statusText()}`);
  }

  return Buffer.from(await response.body());
}

async function fetchTextWithPageContext(page, url) {
  const buf = await fetchBufferWithPageContext(page, url);
  return buf.toString("utf8");
}

const downloadedAssets = new Map();

async function downloadAsset(page, assetUrl) {
  if (downloadedAssets.has(assetUrl)) {
    return downloadedAssets.get(assetUrl);
  }

  const ext = guessExtFromUrl(assetUrl);
  const filename = `${hash(assetUrl)}${ext || ".asset"}`;
  const localPath = path.join(ASSET_DIR, filename);
  const relativePath = `../assets/${filename}`;

  try {
    const data = await fetchBufferWithPageContext(page, assetUrl);
    await fs.writeFile(localPath, data);
    downloadedAssets.set(assetUrl, relativePath);
    console.log(`asset: ${assetUrl} -> ${relativePath}`);
    return relativePath;
  } catch (err) {
    console.warn(`asset failed: ${assetUrl} (${err.message})`);
    downloadedAssets.set(assetUrl, assetUrl);
    return assetUrl;
  }
}

async function rewriteCssUrls(page, cssText, baseUrl) {
  const urlRegex = /url\(\s*(['"]?)(?!data:|blob:|#)([^'")]+)\1\s*\)/gi;

  let outputCss = "";
  let lastIndex = 0;
  let match;

  while ((match = urlRegex.exec(cssText)) !== null) {
    const rawUrl = match[2].trim();

    outputCss += cssText.slice(lastIndex, match.index);

    let absoluteUrl;
    try {
      absoluteUrl = new URL(rawUrl, baseUrl).href;
    } catch {
      outputCss += match[0];
      lastIndex = urlRegex.lastIndex;
      continue;
    }

    const localAssetPath = await downloadAsset(page, absoluteUrl);
    outputCss += `url("${localAssetPath}")`;
    lastIndex = urlRegex.lastIndex;
  }

  outputCss += cssText.slice(lastIndex);
  return outputCss;
}

async function saveCssFile(page, name, cssText, baseUrl) {
  const rewritten = await rewriteCssUrls(page, cssText, baseUrl);
  const filename = name.endsWith(".css") ? name : `${name}.css`;
  const filePath = path.join(CSS_DIR, filename);
  await fs.writeFile(filePath, rewritten, "utf8");
  return filePath;
}

async function waitForEnter(message) {
  const rl = readline.createInterface({ input, output });
  await rl.question(message);
  rl.close();
}

console.log("Opening a visible browser window...");
console.log("Profile folder:", USER_DATA_DIR);

/*
  launchPersistentContext opens a real browser profile.
  headless: false lets you manually pass Cloudflare.
*/
const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
  headless: false,
  viewport: { width: 1440, height: 1200 },
  locale: "en-US",
  timezoneId: "America/Chicago",
  args: [
    "--start-maximized",
    "--disable-blink-features=AutomationControlled",
  ],
});

const page = context.pages()[0] || await context.newPage();

page.setDefaultTimeout(120_000);
page.setDefaultNavigationTimeout(120_000);

console.log(`Opening ${TARGET_URL}`);
await page.goto(TARGET_URL, {
  waitUntil: "domcontentloaded",
  timeout: 120_000,
}).catch((err) => {
  console.warn("Initial goto warning:", err.message);
});

console.log("");
console.log("Browser is open.");
console.log("1. Complete the Cloudflare verification in the browser window.");
console.log("2. Make sure you can see the real Outhere Music homepage.");
console.log("3. Then come back to this terminal and press Enter.");
console.log("");

await waitForEnter("Press Enter here after the real site is visible... ");

await page.waitForLoadState("domcontentloaded").catch(() => {});
await page.waitForLoadState("load", { timeout: 30_000 }).catch(() => {});
await page.waitForTimeout(3000);

/*
  Check whether you are still on Cloudflare.
*/
const pageText = await page.locator("body").innerText({ timeout: 10_000 }).catch(() => "");
if (
  pageText.includes("Performing security verification") ||
  pageText.includes("verifies you are not a bot") ||
  pageText.includes("Performance and Security by Cloudflare")
) {
  console.error("");
  console.error("Still on the Cloudflare verification page.");
  console.error("Pass the check in the browser first, then run this script again.");
  await context.close();
  process.exit(1);
}

/*
  Scroll through the page so lazy components and their styles mount.
*/
console.log("Scrolling page to trigger lazy-rendered styles...");
await page.evaluate(async () => {
  await new Promise((resolve) => {
    let y = 0;
    const step = 700;
    const timer = setInterval(() => {
      window.scrollTo(0, y);
      y += step;

      if (y > document.body.scrollHeight + window.innerHeight) {
        clearInterval(timer);
        window.scrollTo(0, 0);
        resolve();
      }
    }, 250);
  });
});

await page.waitForTimeout(2500);

console.log("Extracting CSS links, inline styles, and CSSOM rules...");

const extracted = await page.evaluate(() => {
  const stylesheetLinks = Array.from(
    document.querySelectorAll('link[rel~="stylesheet"][href]')
  ).map((link) => link.href);

  const inlineStyles = Array.from(document.querySelectorAll("style")).map(
    (style, index) => ({
      index,
      text: style.textContent || "",
    })
  );

  const cssomSheets = [];

  for (const sheet of Array.from(document.styleSheets)) {
    try {
      const rules = Array.from(sheet.cssRules || []).map((rule) => rule.cssText);
      cssomSheets.push({
        href: sheet.href || null,
        text: rules.join("\n"),
        error: null,
      });
    } catch (err) {
      cssomSheets.push({
        href: sheet.href || null,
        text: "",
        error: String(err),
      });
    }
  }

  return {
    url: location.href,
    title: document.title,
    stylesheetLinks,
    inlineStyles,
    cssomSheets,
    html: document.documentElement.outerHTML,
  };
});

await fs.writeFile(
  path.join(OUT_DIR, "rendered-page.html"),
  extracted.html,
  "utf8"
);

const manifest = {
  targetUrl: TARGET_URL,
  finalUrl: extracted.url,
  title: extracted.title,
  linkedCss: [],
  inlineCss: [],
  cssomCss: [],
  assets: [],
};

console.log("");
console.log("Downloading linked CSS files...");

for (const href of extracted.stylesheetLinks) {
  try {
    const cssText = await fetchTextWithPageContext(page, href);

    if (!isLikelyCssText(cssText)) {
      throw new Error("Downloaded content does not look like CSS");
    }

    const name = `linked_${hash(href)}_${cleanName(href)}.css`;
    const filePath = await saveCssFile(page, name, cssText, href);

    manifest.linkedCss.push({
      url: href,
      file: path.relative(OUT_DIR, filePath),
    });

    console.log(`css: ${href} -> ${path.relative(process.cwd(), filePath)}`);
  } catch (err) {
    console.warn(`css failed: ${href} (${err.message})`);
    manifest.linkedCss.push({
      url: href,
      error: err.message,
    });
  }
}

console.log("");
console.log("Saving inline <style> blocks...");

let allInline = "";

for (const style of extracted.inlineStyles) {
  if (!style.text.trim()) continue;

  const name = `inline_style_${String(style.index).padStart(3, "0")}.css`;
  const filePath = await saveCssFile(page, name, style.text, extracted.url);

  manifest.inlineCss.push({
    index: style.index,
    file: path.relative(OUT_DIR, filePath),
  });

  allInline += `\n\n/* ===== inline style ${style.index} ===== */\n`;
  allInline += style.text;
}

await saveCssFile(page, "all_inline_styles.css", allInline, extracted.url);

console.log("");
console.log("Saving CSSOM snapshot...");

let cssomCombined = "";

for (let i = 0; i < extracted.cssomSheets.length; i++) {
  const sheet = extracted.cssomSheets[i];

  cssomCombined += `\n\n/* ===== CSSOM sheet ${i} ===== */\n`;
  cssomCombined += `/* href: ${sheet.href || "inline"} */\n`;

  if (sheet.error) {
    cssomCombined += `/* unreadable: ${sheet.error} */\n`;
  }

  cssomCombined += sheet.text || "";

  if (sheet.text && sheet.text.trim()) {
    const baseUrl = sheet.href || extracted.url;
    const name = `cssom_sheet_${String(i).padStart(3, "0")}.css`;
    const filePath = await saveCssFile(page, name, sheet.text, baseUrl);

    manifest.cssomCss.push({
      href: sheet.href,
      file: path.relative(OUT_DIR, filePath),
      error: sheet.error || null,
    });
  }
}

await saveCssFile(page, "all_cssom_styles.css", cssomCombined, extracted.url);

manifest.assets = Array.from(downloadedAssets.entries()).map(
  ([url, localPath]) => ({
    url,
    localPath,
  })
);

await fs.writeFile(
  path.join(OUT_DIR, "manifest.json"),
  JSON.stringify(manifest, null, 2),
  "utf8"
);

await context.close();

console.log("");
console.log("Done.");
console.log(`Output folder: ${OUT_DIR}`);
console.log("");
console.log("Important files:");
console.log(`- ${path.join(CSS_DIR, "all_inline_styles.css")}`);
console.log(`- ${path.join(CSS_DIR, "all_cssom_styles.css")}`);
console.log(`- ${path.join(OUT_DIR, "manifest.json")}`);
console.log(`- ${path.join(OUT_DIR, "rendered-page.html")}`);