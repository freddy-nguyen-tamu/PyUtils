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

function splitIntoSentences(text) {
  if (typeof Intl !== "undefined" && Intl.Segmenter) {
    const segmenter = new Intl.Segmenter("en", { granularity: "sentence" });
    return [...segmenter.segment(text)]
      .map((s) => s.segment.trim())
      .filter(Boolean);
  }

  return (
    text
      .replace(/\s+/g, " ")
      .match(/[^.!?]+[.!?]+|[^.!?]+$/g)
      ?.map((s) => s.trim())
      .filter(Boolean) || []
  );
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

async function downloadImageFromPage(page, url, outPath) {
  const result = await page.evaluate(async (src) => {
    try {
      const res = await fetch(src);
      const blob = await res.blob();

      return await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onloadend = () => resolve(String(reader.result));
        reader.onerror = reject;
        reader.readAsDataURL(blob);
      });
    } catch (err) {
      return { error: String(err) };
    }
  }, url);

  if (!result || typeof result !== "string" || !result.startsWith("data:")) {
    throw new Error(`Failed to fetch image: ${url}`);
  }

  const match = result.match(/^data:(.*?);base64,(.*)$/);
  if (!match) throw new Error(`Unexpected data URL format for: ${url}`);

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
  // The profile folder keeps cookies and login state across runs.
  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    channel: "chrome",            // use normal Chrome, not Chromium
    headless: args.headless,
    viewport: { width: 1440, height: 1100 },
    args: [
      "--disable-blink-features=AutomationControlled", // hides automation flags
      "--disable-features=ChromeWhatsNewUI",
      "--no-first-run",
      "--disable-default-apps",
      "--disable-sync",
      // Optional: if you encounter GPU issues, add "--disable-gpu"
    ],
  });

  const page = await context.newPage();
  await page.goto(GEMINI_URL, { waitUntil: "domcontentloaded" });

  console.log("\nOpened Gemini URL.");

  // Wait for manual login only on the first run.
  // On subsequent runs the profile already contains the login cookies,
  // so the user can just press Enter without logging in again.
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
      const baseName = `${String(i + 1).padStart(4, "0")}_${sanitizeFilename(sentence, 50)}_${j + 1}.png`;
      const targetPath = path.join(IMAGE_DIR, baseName);

      try {
        const savedPath = await downloadImageFromPage(page, newUrls[j], targetPath);
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