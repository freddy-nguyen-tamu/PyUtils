const fs = require("fs");
const path = require("path");
const readline = require("readline");
const { chromium } = require("playwright");

const GEMINI_URL =
  "https://gemini.google.com/app?android-min-version=301356232&ios-min-version=322.0&is_sa=1&hl=en-US&utm_campaign=microsite_gemini_image_generation_page&icid=microsite_gemini_image_generation_page&utm_source=gemini&utm_medium=web";

const OUTPUT_DIR = path.resolve("gemini_comic_output");
const IMAGE_DIR = path.join(OUTPUT_DIR, "images");
const MANIFEST_PATH = path.join(OUTPUT_DIR, "manifest.json");
const USER_DATA_DIR = path.resolve("./chrome-profile");

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function parseArgs() {
  const args = process.argv.slice(2);
  const result = { text: null, file: null, headless: false, delayMs: 3000 };
  for (let i = 0; i < args.length; i++) {
    const a = args[i];
    if (a === "--text" && args[i + 1]) result.text = args[++i];
    else if (a === "--file" && args[i + 1]) result.file = args[++i];
    else if (a === "--headless") result.headless = true;
    else if (a === "--delayMs" && args[i + 1]) result.delayMs = Number(args[++i]);
  }
  if (!result.text && !result.file) throw new Error("Provide either --text or --file");
  return result;
}

function loadInputText({ text, file }) {
  if (text) return text.trim();
  return fs.readFileSync(path.resolve(file), "utf8").trim();
}

function splitIntoSentences(text) {
  if (typeof Intl !== "undefined" && Intl.Segmenter) {
    const segmenter = new Intl.Segmenter("en", { granularity: "sentence" });
    return [...segmenter.segment(text)].map(s => s.segment.trim()).filter(Boolean);
  }
  return text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+|[^.!?]+$/g)?.map(s => s.trim()).filter(Boolean) || [];
}

function sanitizeFilename(s, max = 80) {
  return s.replace(/[<>:"/\\|?*\x00-\x1F]/g, "").replace(/\s+/g, "_").slice(0, max);
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

function askUser(question) {
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });
  return new Promise(resolve => rl.question(question, answer => { rl.close(); resolve(answer); }));
}

async function waitForManualLogin() {
  await askUser("\nLog in to Gemini in the opened browser, open the target conversation if needed, then press ENTER here to continue...");
}

async function dumpComposerDebug(page) {
  try {
    const info = await page.evaluate(() => {
      const nodes = Array.from(document.querySelectorAll('rich-textarea .ql-editor, [contenteditable="true"], button[aria-label], textarea, input')).slice(0, 40);
      return nodes.map(el => ({
        tag: el.tagName,
        className: el.className || "",
        ariaLabel: el.getAttribute("aria-label") || "",
        role: el.getAttribute("role") || "",
        contenteditable: el.getAttribute("contenteditable") || "",
        visible: el.getBoundingClientRect().width > 0 && el.getBoundingClientRect().height > 0 && getComputedStyle(el).display !== "none" && getComputedStyle(el).visibility !== "hidden",
        text: (el.textContent || "").trim().slice(0, 80),
      }));
    });
    console.log("\n[DEBUG] Candidate input elements:", JSON.stringify(info, null, 2));
  } catch (err) {
    console.log(`\n[DEBUG] Failed to inspect page: ${err.message}`);
  }
}

async function waitForGeminiReady(page) {
  await page.waitForLoadState("domcontentloaded");
  await sleep(2000);
  try {
    await page.waitForFunction(() => {
      const selectors = ['rich-textarea .ql-editor[contenteditable="true"]', 'div.ql-editor[contenteditable="true"][role="textbox"]', 'rich-textarea div[contenteditable="true"]'];
      return selectors.some(selector => {
        const el = document.querySelector(selector);
        if (!el) return false;
        const rect = el.getBoundingClientRect();
        const style = window.getComputedStyle(el);
        return rect.width > 0 && rect.height > 0 && style.display !== "none" && style.visibility !== "hidden";
      });
    }, { timeout: 45000 });
  } catch (err) {
    await dumpComposerDebug(page);
    throw new Error(`Gemini did not become ready: ${err.message}`);
  }
}

async function locatePromptBox(page) {
  const candidateSelectors = [
    'rich-textarea .ql-editor[contenteditable="true"][aria-label*="Enter a prompt"]',
    'rich-textarea .ql-editor[contenteditable="true"][aria-label*="Gemini"]',
    'div.ql-editor[contenteditable="true"][role="textbox"][aria-multiline="true"]',
    'rich-textarea .ql-editor.textarea.new-input-ui[contenteditable="true"]',
    'rich-textarea .ql-editor.textarea[contenteditable="true"]',
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
        const isUsable = await locator.evaluate(el => {
          if (!(el instanceof HTMLElement)) return false;
          const style = window.getComputedStyle(el);
          const rect = el.getBoundingClientRect();
          const ariaHidden = el.getAttribute("aria-hidden");
          const tabIndex = el.getAttribute("tabindex");
          const classText = el.className || "";
          const hiddenClipboard = classText.includes("ql-clipboard") || ariaHidden === "true" || tabIndex === "-1";
          return !hiddenClipboard && el.getAttribute("contenteditable") === "true" && style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
        });
        if (isUsable) return locator;
      } catch (_) {}
    }
  }
  await dumpComposerDebug(page);
  throw new Error("Could not find the Gemini prompt box.");
}

async function clearAndTypeIntoEditor(page, box, text) {
  await box.click({ force: true });
  await sleep(300);
  await page.keyboard.press(process.platform === "darwin" ? "Meta+A" : "Control+A");
  await sleep(100);
  await page.keyboard.press("Backspace");
  await sleep(150);
  try {
    await box.evaluate(el => {
      el.focus();
      if (el.innerHTML !== undefined) el.innerHTML = "<p><br></p>";
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
  const hasText = await box.evaluate(el => (el.textContent || "").replace(/\s+/g, " ").trim().length > 0);
  if (!hasText) {
    await box.click({ force: true });
    await page.keyboard.type(text, { delay: 5 });
  }
}

async function clickSendIfPossible(page) {
  const sendSelectors = ['button[aria-label="Send message"]', 'button.send-button[aria-label="Send message"]', '.send-button button[aria-label="Send message"]', 'button[aria-label*="Send"]'];
  for (const selector of sendSelectors) {
    const locator = page.locator(selector).first();
    try {
      await locator.waitFor({ state: "visible", timeout: 3000 });
      const enabled = await locator.evaluate(btn => {
        if (!(btn instanceof HTMLElement)) return false;
        const style = window.getComputedStyle(btn);
        const rect = btn.getBoundingClientRect();
        const ariaDisabled = btn.getAttribute("aria-disabled");
        const disabledProp = btn.disabled === true;
        return style.display !== "none" && style.visibility !== "hidden" && rect.width > 0 && rect.height > 0 && !disabledProp && ariaDisabled !== "true";
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
      const currentText = await box.evaluate(el => (el.textContent || "").replace(/\s+/g, " ").trim());
      if (!currentText || currentText !== promptText.replace(/\s+/g, " ").trim()) return true;
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
  if (!submitted) throw new Error("Prompt was typed, but the script could not submit it.");
  await sleep(1200);
}

// Wait for image generation to complete by detecting the "Stop generating" button disappearance
// and then waiting for the final image to be present in the last model response.
async function waitForImageGenerationComplete(page, timeoutMs = 180000) {
  const start = Date.now();
  let lastResponseCount = 0;
  while (Date.now() - start < timeoutMs) {
    // Check if there's a "Stop generating" button visible
    const isGenerating = await page.evaluate(() => {
      const stopBtn = document.querySelector('button[aria-label="Stop generating"], button[aria-label*="Stop"]');
      return stopBtn && stopBtn.offsetParent !== null;
    });
    if (!isGenerating) {
      // Give a moment for the final image to render
      await sleep(2000);
      // Additionally, check if any image with blob: src exists in the last model response
      const hasImage = await page.evaluate(() => {
        const modelResponses = Array.from(document.querySelectorAll('model-response, .model-response'));
        if (modelResponses.length === 0) return false;
        const lastResponse = modelResponses[modelResponses.length - 1];
        const imgs = lastResponse.querySelectorAll('img[src^="blob:"], img[src^="data:"]');
        return imgs.length > 0;
      });
      if (hasImage) return true;
      // If no image but also no generating indicator, assume done (maybe no image generated)
      return true;
    }
    await sleep(1000);
  }
  throw new Error("Timed out waiting for image generation to complete.");
}

// Extract images from the latest assistant response.
// Returns array of { src: string, elementHandle: ElementHandle } for new images.
async function getNewImagesFromLatestResponse(page, previousImageUrls) {
  // Wait for the response to be fully rendered and image to be loaded
  await waitForImageGenerationComplete(page, 180000);

  // Get all img elements inside the last model-response that have blob: or data: src and are likely generated images.
  const newImageInfos = await page.evaluate((prevUrls) => {
    const modelResponses = Array.from(document.querySelectorAll('model-response, .model-response'));
    if (modelResponses.length === 0) return [];
    const lastResponse = modelResponses[modelResponses.length - 1];
    // Find all img tags inside the response content area (inside .markdown or .response-container-content)
    const contentArea = lastResponse.querySelector('.response-container-content, .markdown, .model-response-text');
    if (!contentArea) return [];
    const imgs = Array.from(contentArea.querySelectorAll('img'));
    // Filter for generated images: blob: or data: src, and reasonable size (ignore tiny icons)
    const validImgs = imgs.filter(img => {
      const src = img.src;
      if (!src.startsWith('blob:') && !src.startsWith('data:')) return false;
      const rect = img.getBoundingClientRect();
      // Filter out very small images (likely icons)
      return rect.width > 50 && rect.height > 50;
    });
    // Return src and a unique identifier (index) to map back
    return validImgs.map((img, idx) => ({
      src: img.src,
      tempId: idx,
      naturalWidth: img.naturalWidth,
      naturalHeight: img.naturalHeight,
    }));
  }, previousImageUrls);

  if (newImageInfos.length === 0) return [];

  // Now we need to get the actual ElementHandle for each new image.
  // Since we can't directly pass the index from evaluate, we'll locate them again.
  const imageHandles = [];
  for (let i = 0; i < newImageInfos.length; i++) {
    const info = newImageInfos[i];
    // Find the image by its src (might be blob, which is unique)
    const handle = await page.evaluateHandle((src) => {
      const modelResponses = Array.from(document.querySelectorAll('model-response, .model-response'));
      if (modelResponses.length === 0) return null;
      const lastResponse = modelResponses[modelResponses.length - 1];
      const contentArea = lastResponse.querySelector('.response-container-content, .markdown, .model-response-text');
      if (!contentArea) return null;
      const img = contentArea.querySelector(`img[src="${src}"]`);
      return img;
    }, info.src);
    const elementHandle = handle.asElement();
    if (elementHandle) {
      imageHandles.push({ handle: elementHandle, info });
    }
  }
  return imageHandles;
}

// Download image from ElementHandle using canvas
async function downloadImageFromElement(page, imgElementHandle, outPath) {
  const base64 = await imgElementHandle.evaluate(async (img) => {
    if (!img.complete) {
      await new Promise(resolve => { img.onload = resolve; img.onerror = resolve; });
    }
    try {
      const canvas = document.createElement('canvas');
      canvas.width = img.naturalWidth || img.width;
      canvas.height = img.naturalHeight || img.height;
      if (canvas.width === 0 || canvas.height === 0) throw new Error('Invalid dimensions');
      const ctx = canvas.getContext('2d');
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      let dataUrl;
      try { dataUrl = canvas.toDataURL('image/png'); } catch (e) { dataUrl = canvas.toDataURL('image/jpeg'); }
      return dataUrl;
    } catch (err) {
      return null;
    }
  });
  if (!base64 || !base64.startsWith('data:image')) throw new Error('Could not extract image data');
  const match = base64.match(/^data:image\/(png|jpeg|jpg|webp|gif);base64,(.*)$/);
  if (!match) throw new Error('Unexpected data URL format');
  let ext = match[1];
  if (ext === 'jpeg') ext = 'jpg';
  const finalPath = outPath.replace(/\.[a-zA-Z0-9]+$/, `.${ext}`);
  fs.writeFileSync(finalPath, Buffer.from(match[2], 'base64'));
  return finalPath;
}

// Page health check
async function ensurePageAlive(page, context) {
  try {
    await page.evaluate(() => document.title);
    return page;
  } catch (err) {
    console.log("\n[WARN] Page dead. Recovering...");
    const newPage = await context.newPage();
    await newPage.goto(GEMINI_URL, { waitUntil: "domcontentloaded" });
    await waitForManualLogin();
    await waitForGeminiReady(newPage);
    return newPage;
  }
}

async function main() {
  const args = parseArgs();
  const inputText = loadInputText(args);
  const sentences = splitIntoSentences(inputText);
  if (!sentences.length) throw new Error("No sentences found.");
  ensureDir(OUTPUT_DIR);
  ensureDir(IMAGE_DIR);
  console.log(`Found ${sentences.length} sentence(s).`);

  const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
    channel: "chrome",
    headless: args.headless,
    viewport: { width: 1440, height: 1100 },
    args: ["--disable-blink-features=AutomationControlled", "--disable-features=ChromeWhatsNewUI", "--no-first-run", "--disable-default-apps", "--disable-sync"],
  });

  let page = await context.newPage();
  await page.goto(GEMINI_URL, { waitUntil: "domcontentloaded" });
  console.log("\nOpened Gemini URL.");
  await waitForManualLogin();
  await waitForGeminiReady(page);

  const manifest = [];

  // Setup prompt – no images expected, just send and continue
  const setupPrompt = [
    "We are creating a comic from a story, one panel at a time.",
    "For the entire conversation, keep a consistent visual style and consistent character designs across all panels.",
    "Persist the same faces, hair, body proportions, clothing silhouettes, and overall art style unless a sentence explicitly says they changed.",
    "Generate exactly one comic panel per prompt.",
    "Do not make a multi-panel page.",
    "Prioritize clear storytelling and visual continuity.",
  ].join(" ");
  console.log("\nSending setup instruction...");
  await submitPrompt(page, setupPrompt);
  // Wait a bit for any response but don't expect images
  await sleep(5000);
  console.log("Setup instruction sent (no images expected).");

  for (let i = 0; i < sentences.length; i++) {
    page = await ensurePageAlive(page, context);
    const sentence = sentences[i];
    const prompt = [
      "Generate exactly one comic panel for the following sentence.",
      "Keep character designs, outfits, proportions, and art style consistent with previous panels in this same conversation.",
      "Do not generate a collage or multiple panels.",
      `Sentence: "${sentence}"`,
    ].join(" ");
    console.log(`\n[${i+1}/${sentences.length}] Sending sentence: ${sentence}`);

    await submitPrompt(page, prompt);
    console.log("Waiting for Gemini to finish generating image...");

    let imageHandles = [];
    try {
      imageHandles = await getNewImagesFromLatestResponse(page, []);
      if (imageHandles.length === 0) {
        // Wait a bit longer and try again
        await sleep(5000);
        imageHandles = await getNewImagesFromLatestResponse(page, []);
      }
      console.log(`Found ${imageHandles.length} new image(s).`);
    } catch (err) {
      console.error(`Failed to get images for sentence ${i+1}: ${err.message}`);
      manifest.push({ index: i+1, sentence, images: [], error: err.message });
      continue;
    }

    const savedImages = [];
    for (let j = 0; j < imageHandles.length; j++) {
      const baseName = `${String(i+1).padStart(4,"0")}_${sanitizeFilename(sentence,50)}_${j+1}.png`;
      const targetPath = path.join(IMAGE_DIR, baseName);
      try {
        const savedPath = await downloadImageFromElement(page, imageHandles[j].handle, targetPath);
        savedImages.push(savedPath);
        console.log(`Saved ${savedPath}`);
      } catch (err) {
        console.error(`Failed to save image ${j+1}: ${err.message}`);
      }
    }

    manifest.push({ index: i+1, sentence, images: savedImages });
    fs.writeFileSync(MANIFEST_PATH, JSON.stringify(manifest, null, 2), "utf8");
    await sleep(args.delayMs);
  }

  console.log(`\nDone. Manifest: ${MANIFEST_PATH}\nImages: ${IMAGE_DIR}\nBrowser open, Ctrl+C to exit.`);
  await new Promise(() => {});
}

main().catch(err => { console.error(err); process.exit(1); });