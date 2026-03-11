// save as panelwise.js
// usage:
//   npm install playwright
//   node panelwise.js --text "Your paragraph here"
//   node panelwise.js --file novel.txt

const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { chromium } = require("playwright");

const GEMINI_URL =
  "https://gemini.google.com/app?android-min-version=301356232&ios-min-version=322.0&is_sa=1&hl=en-US&utm_campaign=microsite_gemini_image_generation_page&icid=microsite_gemini_image_generation_page&utm_source=gemini&utm_medium=web";

const OUTPUT_DIR = path.resolve("gemini_comic_output");
const IMAGE_DIR = path.join(OUTPUT_DIR, "images");
const MANIFEST_PATH = path.join(OUTPUT_DIR, "manifest.json");

// Folder where Chrome will store profile data (cookies, localStorage, etc.)
const USER_DATA_DIR = path.resolve("./chrome-profile");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const result = {
    text: null,
    file: null,
    headless: false,
    delayMs: 3000,
  };

  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--text" && args[i + 1]) {
      result.text = args[++i];
    } else if (a === "--file" && args[i + 1]) {
      result.file = args[++i];
    } else if (a === "--headless") {
      result.headless = true;
    } else if (a === "--delayMs" && args[i + 1]) {
      result.delayMs = Number(args[++i]);
    }
  }

  if (!result.text && !result.file) {
    throw new Error("Provide either --text or --file");
  }

  return result;
}

function loadInputText({ text, file }) {
  if (text) return text.trim();
  return fs.readFileSync(path.resolve(file), "utf8").trim();
}

// Improved sentence splitting that handles quotes and abbreviations
function splitIntoSentences(text) {
  // Common abbreviations that should not end a sentence
  const abbreviations = new Set([
    'mr', 'mrs', 'ms', 'dr', 'prof', 'rev', 'hon', 'st', 'ave', 'blvd',
    'rd', 'etc', 'vs', 'e.g', 'i.e', 'a.m', 'p.m', 'fig', 'vol', 'no',
    'inc', 'corp', 'ltd', 'jr', 'sr', 'phd', 'md', 'al', 'p.s'
  ]);

  // Replace quoted content with placeholders to protect internal punctuation
  const quotes = [];
  const noQuotes = text.replace(/"[^"]*"|'[^']*'|`[^`]*`/g, (match) => {
    quotes.push(match);
    return `__QUOTE_${quotes.length - 1}__`;
  });

  // Split on . ? ! followed by whitespace and a capital letter (or end of string)
  const rawSentences = noQuotes.split(/(?<=[.!?])\s+(?=[A-Z"']|$)/g);

  const sentences = [];
  for (let raw of rawSentences) {
    // Restore quoted content
    raw = raw.replace(/__QUOTE_(\d+)__/g, (_, idx) => quotes[parseInt(idx)]);
    raw = raw.trim();
    if (!raw) continue;

    // Check if the split was caused by an abbreviation
    const match = raw.match(/\b([a-z]+)\.$/i);
    const lastWord = match ? match[1].toLowerCase() : null;
    if (lastWord && abbreviations.has(lastWord)) {
      // Merge with previous sentence
      if (sentences.length) {
        sentences[sentences.length - 1] += ' ' + raw;
      } else {
        sentences.push(raw);
      }
    } else {
      sentences.push(raw);
    }
  }

  // If we still have only 1 sentence but there are many periods, try a simpler approach
  if (sentences.length === 1 && text.includes('. ') && text.length > 500) {
    const simpler = text.split(/(?<=[.!?])\s+(?=[A-Z])/);
    if (simpler.length > 1) return simpler.filter(s => s.trim().length > 0);
  }

  return sentences;
}

function sanitizeFilename(s, max = 80) {
  return s
    .replace(/[<>:"/\\|?*\x00-\x1F]/g, "")
    .replace(/\s+/g, "_")
    .slice(0, max);
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function askUser(question) {
  const rl = readline.createInterface({
    input: process.stdin,
    output: process.stdout,
  });

  return new Promise((resolve) => {
    rl.question(question, (answer) => {
      rl.close();
      resolve(answer);
    });
  });
}

async function waitForManualLogin() {
  await askUser(
    "\nLog in to Gemini in the opened browser, open the target conversation if needed, then press ENTER here to continue..."
  );
}

async function dumpComposerDebug(page) {
  try {
    const info = await page.evaluate(() => {
      const nodes = Array.from(
        document.querySelectorAll(
          'rich-textarea .ql-editor, [contenteditable="true"], button[aria-label], textarea, input'
        )
      ).slice(0, 40);

      return nodes.map((el) => {
        const rect = el.getBoundingClientRect();
        return {
          tag: el.tagName,
          className: el.className || "",
          ariaLabel: el.getAttribute("aria-label") || "",
          role: el.getAttribute("role") || "",
          contenteditable: el.getAttribute("contenteditable") || "",
          visible:
            rect.width > 0 &&
            rect.height > 0 &&
            window.getComputedStyle(el).display !== "none" &&
            window.getComputedStyle(el).visibility !== "hidden",
          text: (el.textContent || "").trim().slice(0, 80),
        };
      });
    });

    console.log("\n[DEBUG] Candidate input elements:");
    console.log(JSON.stringify(info, null, 2));
  } catch (err) {
    console.log(`\n[DEBUG] Failed to inspect page: ${err.message}`);
  }
}

async function waitForGeminiReady(page) {
  await page.waitForLoadState("domcontentloaded");
  await sleep(2000);

  try {
    await page.waitForFunction(() => {
      const selectors = [
        'rich-textarea .ql-editor[contenteditable="true"]',
        'div.ql-editor[contenteditable="true"][role="textbox"]',
        'rich-textarea div[contenteditable="true"]',
      ];

      return selectors.some((selector) => {
        const el = document.querySelector(selector);
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return (
          rect.width > 0 &&
          rect.height > 0 &&
          style.display !== "none" &&
          style.visibility !== "hidden"
        );
      });
    }, { timeout: 45000 });
  } catch (err) {
    await dumpComposerDebug(page);
    throw new Error(
      `Gemini did not become ready after login. Make sure you are signed in and the conversation page is fully loaded. Original error: ${err.message}`
    );
  }
}

async function locatePromptBox(page) {
  const candidateSelectors = [
    'rich-textarea .ql-editor[contenteditable="true"][aria-label*="Enter a prompt"]',
    'rich-textarea .ql-editor[contenteditable="true"][aria-label*="Gemini"]',
    'div.ql-editor[contenteditable="true"][role="textbox"][aria-multiline="true"]',
    'rich-textarea .ql-editor.textarea.new-input-ui[contenteditable="true"]',
    'rich-textarea .ql-editor.textarea[contenteditable="true"]',
    '[data-node-type="input-area"] rich-textarea .ql-editor[contenteditable="true"]',
    'input-area-v2 rich-textarea .ql-editor[contenteditable="true"]',
    'rich-textarea div[contenteditable="true"]',
  ];

  await page.waitForLoadState("domcontentloaded");
  await sleep(1000);

  for (const selector of candidateSelectors) {
    const count = await page.locator(selector).count();

    for (let i = 0; i < count; i++) {
      const locator = page.locator(selector).nth(i);

      try {
        await locator.waitFor({ state: "attached", timeout: 2000 });

        const isUsable = await locator.evaluate((el) => {
          if (!(el instanceof HTMLElement)) return false;

          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const ariaHidden = el.getAttribute("aria-hidden");
          const tabIndex = el.getAttribute("tabindex");
          const classText = el.className || "";

          const hiddenClipboard =
            classText.includes("ql-clipboard") ||
            ariaHidden === "true" ||
            tabIndex === "-1";

          return (
            !hiddenClipboard &&
            el.getAttribute("contenteditable") === "true" &&
            style.visibility !== "hidden" &&
            style.display !== "none" &&
            rect.width > 0 &&
            rect.height > 0
          );
        });

        if (isUsable) {
          return locator;
        }
      } catch (_) {}
    }
  }

  await dumpComposerDebug(page);

  throw new Error(
    "Could not find the Gemini prompt box. The page structure may have changed, the editor may not be fully loaded yet, or a hidden clipboard node is being matched instead."
  );
}

async function clearAndTypeIntoEditor(page, box, text) {
  await box.click({ force: true });
  await sleep(300);

  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
  await sleep(100);
  await page.keyboard.press("Backspace");
  await sleep(150);

  try {
    await box.evaluate((el) => {
      el.focus();
      if (el.innerHTML !== undefined) {
        el.innerHTML = "<p><br></p>";
      }
      el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "deleteContentBackward" }));
    });
  } catch (_) {}

  await box.click({ force: true });
  await sleep(150);

  try {
    await box.type(text, { delay: 5 });
  } catch (_) {
    await page.keyboard.type(text, { delay: 5 });
  }

  const hasText = await box.evaluate((el) => {
    const textContent = (el.textContent || "").replace(/\s+/g, " ").trim();
    return textContent.length > 0;
  });

  if (!hasText) {
    await box.click({ force: true });
    await page.keyboard.type(text, { delay: 5 });
  }
}

async function clickSendIfPossible(page) {
  const sendSelectors = [
    'button[aria-label="Send message"]',
    'button.send-button[aria-label="Send message"]',
    '.send-button button[aria-label="Send message"]',
    'button[aria-label*="Send"]',
  ];

  for (const selector of sendSelectors) {
    const locator = page.locator(selector).first();

    try {
      await locator.waitFor({ state: "visible", timeout: 3000 });

      const enabled = await locator.evaluate((btn) => {
        if (!(btn instanceof HTMLElement)) return false;
        const style = window.getComputedStyle(btn);
        const rect = btn.getBoundingClientRect();
        const ariaDisabled = btn.getAttribute("aria-disabled");
        const disabledProp = btn.disabled === true;

        return (
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          rect.width > 0 &&
          rect.height > 0 &&
          !disabledProp &&
          ariaDisabled !== "true"
        );
      });

      if (enabled) {
        await locator.click({ force: true });
        return true;
      }
    } catch (_) {}
  }

  return false;
}

async function waitForPromptToLeaveInput(page, promptText, timeoutMs = 15000) {
  const start = Date.now();

  while (Date.now() - start < timeoutMs) {
    try {
      const box = await locatePromptBox(page);
      const currentText = await box.evaluate((el) =>
        (el.textContent || "").replace(/\s+/g, " ").trim()
      );

      if (!currentText || currentText !== promptText.replace(/\s+/g, " ").trim()) {
        return true;
      }
    } catch (_) {
      return true;
    }

    await sleep(300);
  }

  return false;
}

async function submitPrompt(page, promptText) {
  const box = await locatePromptBox(page);
  await clearAndTypeIntoEditor(page, box, promptText);

  let submitted = await clickSendIfPossible(page);

  if (!submitted) {
    await page.keyboard.press("Enter");
    submitted = await waitForPromptToLeaveInput(page, promptText, 5000);
  }

  if (!submitted) {
    await sleep(1000);
    submitted = await clickSendIfPossible(page);
  }

  if (!submitted) {
    await dumpComposerDebug(page);
    throw new Error("Prompt was typed, but the script could not submit it.");
  }

  await sleep(1200);
}

async function getCurrentImageUrls(page) {
  return await page.evaluate(() => {
    const imgs = Array.from(document.querySelectorAll("img"));
    const urls = imgs
      .map((img) => img.src)
      .filter(Boolean)
      .filter((src) => src.startsWith("blob:") || src.startsWith("data:") || src.startsWith("https://"));

    return [...new Set(urls)];
  });
}

async function waitForNewImages(page, previousUrls, timeoutMs = 180000) {
  const start = Date.now();

  while (Date.now() - start < timeoutMs) {
    const currentUrls = await getCurrentImageUrls(page);
    const newUrls = currentUrls.filter((u) => !previousUrls.includes(u));

    if (newUrls.length > 0) {
      await sleep(3000);
      const settledUrls = await getCurrentImageUrls(page);
      return settledUrls.filter((u) => !previousUrls.includes(u));
    }

    await sleep(2000);
  }

  throw new Error("Timed out waiting for newly generated images.");
}

// NEW: Download image from an img element using canvas (works even if blob URL is revoked)
async function downloadImageFromPage(page, imgLocator, outPath) {
  const dataUrl = await imgLocator.evaluate((img) => {
    return new Promise((resolve, reject) => {
      const loadAndConvert = () => {
        try {
          const canvas = document.createElement('canvas');
          canvas.width = img.naturalWidth;
          canvas.height = img.naturalHeight;
          const ctx = canvas.getContext('2d');
          ctx.drawImage(img, 0, 0);
          resolve(canvas.toDataURL());
        } catch (err) {
          reject(err);
        }
      };

      if (img.complete && img.naturalWidth > 0) {
        loadAndConvert();
      } else {
        img.addEventListener('load', loadAndConvert);
        img.addEventListener('error', (err) => reject(err));
      }
    });
  });

  const match = dataUrl.match(/^data:(.*?);base64,(.*)$/);
  if (!match) throw new Error(`Invalid data URL for image`);

  const mime = match[1];
  const base64 = match[2];
  let ext = "png";
  if (mime.includes("jpeg")) ext = "jpg";
  else if (mime.includes("webp")) ext = "webp";
  else if (mime.includes("gif")) ext = "gif";

  const finalPath = outPath.replace(/\.[a-zA-Z0-9]+$/, `.${ext}`);
  fs.writeFileSync(finalPath, Buffer.from(base64, "base64"));
  return finalPath;
}

async function main() {
  const args = parseArgs();
  const inputText = loadInputText(args);
  const sentences = splitIntoSentences(inputText);

  if (!sentences.length) {
    throw new Error("No sentences found.");
  }

  ensureDir(OUTPUT_DIR);
  ensureDir(IMAGE_DIR);

  console.log(`Found ${sentences.length} sentence(s).`);

  // Launch a persistent Chrome instance with a dedicated profile folder.
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    channel: "chrome",
    headless: args.headless,
    viewport: { width: 1440, height: 1100 },
    args: [
      "--disable-blink-features=AutomationControlled",
      "--disable-features=ChromeWhatsNewUI",
      "--no-first-run",
      "--disable-default-apps",
      "--disable-sync",
    ],
  });

  const page = await context.newPage();
  await page.goto(GEMINI_URL, { waitUntil: "domcontentloaded" });

  console.log("\nOpened Gemini URL.");
  await waitForManualLogin();
  await waitForGeminiReady(page);

  const manifest = [];

  const setupPrompt = [
    "We are creating a comic from a story, one panel at a time.",
    "For the entire conversation, keep a consistent visual style and consistent character designs across all panels.",
    "Persist the same faces, hair, body proportions, clothing silhouettes, and overall art style unless a sentence explicitly says they changed.",
    "Generate exactly one comic panel per prompt.",
    "Do not make a multi-panel page.",
    "Prioritize clear storytelling and visual continuity.",
  ].join(" ");

  console.log("\nSending setup instruction...");
  let existingImages = await getCurrentImageUrls(page);
  await submitPrompt(page, setupPrompt);

  try {
    const setupNewImages = await waitForNewImages(page, existingImages, 120000);
    existingImages = existingImages.concat(setupNewImages);
    console.log(`Setup produced ${setupNewImages.length} image(s).`);
  } catch (_) {
    console.log("Setup prompt did not appear to produce images. Continuing.");
  }

  for (let i = 0; i < sentences.length; i++) {
    const sentence = sentences[i];

    const prompt = [
      "Generate exactly one comic panel for the following sentence.",
      "Keep character designs, outfits, proportions, and art style consistent with previous panels in this same conversation.",
      "Do not generate a collage or multiple panels.",
      `Sentence: "${sentence}"`,
    ].join(" ");

    console.log(`\n[${i + 1}/${sentences.length}] Sending sentence: ${sentence}`);

    const beforeUrls = await getCurrentImageUrls(page);
    await submitPrompt(page, prompt);

    let newUrls;
    try {
      newUrls = await waitForNewImages(page, beforeUrls, 180000);
      console.log(`Found ${newUrls.length} new image(s).`);
    } catch (err) {
      console.error(`No new images detected for sentence ${i + 1}: ${err.message}`);
      manifest.push({
        index: i + 1,
        sentence,
        images: [],
        error: err.message,
      });
      continue;
    }

    const savedImages = [];
    for (let j = 0; j < newUrls.length; j++) {
      const url = newUrls[j];
      // Find the img element that has this blob URL as its src
      const imgLocator = page.locator(`img[src="${url}"]`).first();
      await imgLocator.waitFor({ state: "attached", timeout: 5000 });

      const baseName = `${String(i + 1).padStart(4, "0")}_${sanitizeFilename(sentence, 50)}_${j + 1}.png`;
      const targetPath = path.join(IMAGE_DIR, baseName);

      try {
        const savedPath = await downloadImageFromPage(page, imgLocator, targetPath);
        savedImages.push(savedPath);
        console.log(`Saved ${savedPath}`);
      } catch (err) {
        console.error(`Failed to save image ${j + 1} for sentence ${i + 1}: ${err.message}`);
      }
    }

    manifest.push({
      index: i + 1,
      sentence,
      images: savedImages,
    });

    fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");

    await sleep(args.delayMs);
  }

  console.log(`\nDone. Manifest saved to: ${MANIFEST_PATH}`);
  console.log(`Images saved in: ${IMAGE_DIR}`);
  console.log("\nBrowser left open for inspection. Press Ctrl+C to exit.");

  await new Promise(() => {});
}

main().catch((err) => {
  console.error(err);
  process.exit(1);
});