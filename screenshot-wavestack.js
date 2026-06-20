// screenshot-wavestack.js
//
// Uses NORMAL installed Firefox with NO Selenium and NO Playwright.
// This avoids Google's "browser or app may not be secure" automation block.
//
// How it works:
// 1. Opens normal Firefox with a reusable profile folder.
// 2. You log in manually.
// 3. You close Firefox.
// 4. Script uses Firefox's built-in --screenshot command to capture WaveStack pages.
//
// Run:
//   node screenshot-wavestack.js

const fs = require("fs");
const path = require("path");
const { spawn, spawnSync } = require("child_process");

const BASE_URL = process.env.BASE_URL || "https://wavestack.duckdns.org";

const OUT_DIR = path.resolve(__dirname, "wavestack-feature-screenshots");
const FIREFOX_PROFILE_DIR = path.resolve(__dirname, "wavestack-normal-firefox-profile");

const WINDOW_WIDTH = 1440;
const WINDOW_HEIGHT = 1200;

// Increase this if WaveStack needs more time to load songs/thumbnails.
const SCREENSHOT_DELAY_MS = 5000;

const FEATURES = [
  {
    name: "dashboard-random-discovery",
    path: "/",
    description: "Dashboard / Random discovery",
  },
  {
    name: "library",
    path: "/library",
    description: "Library",
  },
  {
    name: "all-songs",
    path: "/all",
    description: "All songs",
  },
  {
    name: "search",
    path: "/search",
    description: "Search",
  },
  {
    name: "favorites",
    path: "/favorites",
    description: "Favorites",
  },
  {
    name: "recent",
    path: "/recent",
    description: "Recent",
  },
  {
    name: "queue",
    path: "/queue",
    description: "Queue",
  },
  {
    name: "stats",
    path: "/stats",
    description: "Stats",
  },
  {
    name: "playlists",
    path: "/playlists",
    description: "Playlists",
  },
  {
    name: "add-songs",
    path: "/add-songs",
    description: "Add songs",
  },
  {
    name: "profile",
    path: "/profile",
    description: "Profile",
  },
];

function ensureDir(dir) {
  if (!fs.existsSync(dir)) {
    fs.mkdirSync(dir, { recursive: true });
  }
}

function safeFileName(value) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9-_]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function waitForEnter(message) {
  console.log("");
  console.log(message);
  console.log("Press ENTER in this terminal when ready.");
  console.log("");

  return new Promise((resolve) => {
    process.stdin.resume();
    process.stdin.once("data", resolve);
  });
}

function getNormalFirefoxPath() {
  if (process.platform === "win32") {
    const possiblePaths = [
      "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
      "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
    ];

    for (const firefoxPath of possiblePaths) {
      if (fs.existsSync(firefoxPath)) {
        return firefoxPath;
      }
    }

    throw new Error(
      [
        "Normal Firefox was not found.",
        "",
        "Expected one of:",
        "C:\\Program Files\\Mozilla Firefox\\firefox.exe",
        "C:\\Program Files (x86)\\Mozilla Firefox\\firefox.exe",
        "",
        "If Firefox is installed somewhere else, edit getNormalFirefoxPath().",
      ].join("\n")
    );
  }

  if (process.platform === "darwin") {
    const firefoxPath = "/Applications/Firefox.app/Contents/MacOS/firefox";

    if (fs.existsSync(firefoxPath)) {
      return firefoxPath;
    }

    throw new Error(
      "Normal Firefox was not found at /Applications/Firefox.app/Contents/MacOS/firefox"
    );
  }

  const possiblePaths = [
    "/usr/bin/firefox",
    "/snap/bin/firefox",
    "/usr/local/bin/firefox",
  ];

  for (const firefoxPath of possiblePaths) {
    if (fs.existsSync(firefoxPath)) {
      return firefoxPath;
    }
  }

  throw new Error("Normal Firefox was not found. Edit getNormalFirefoxPath().");
}

async function openFirefoxForManualLogin(firefoxPath) {
  ensureDir(FIREFOX_PROFILE_DIR);

  console.log("");
  console.log(`Using normal Firefox from: ${firefoxPath}`);
  console.log(`Using Firefox profile folder: ${FIREFOX_PROFILE_DIR}`);
  console.log("");

  console.log("Opening normal Firefox for manual login...");

  const args = [
    "-profile",
    FIREFOX_PROFILE_DIR,
    "-no-remote",
    BASE_URL,
  ];

  const child = spawn(firefoxPath, args, {
    detached: true,
    stdio: "ignore",
  });

  child.unref();

  await waitForEnter(
    [
      "Log in to WaveStack manually in the Firefox window.",
      "",
      "Important:",
      "After login is fully complete and you can see WaveStack, CLOSE that Firefox window.",
      "Then press ENTER here.",
    ].join("\n")
  );
}

function runFirefoxScreenshot(firefoxPath, url, outputPath) {
  ensureDir(path.dirname(outputPath));

  const args = [
    "-profile",
    FIREFOX_PROFILE_DIR,
    "-no-remote",
    "--headless",
    "--window-size",
    `${WINDOW_WIDTH},${WINDOW_HEIGHT}`,
    "--screenshot",
    outputPath,
    url,
  ];

  const result = spawnSync(firefoxPath, args, {
    encoding: "utf8",
    timeout: 60000,
  });

  if (result.error) {
    throw result.error;
  }

  if (result.status !== 0) {
    throw new Error(
      [
        `Firefox screenshot failed for ${url}`,
        "",
        "STDOUT:",
        result.stdout || "(empty)",
        "",
        "STDERR:",
        result.stderr || "(empty)",
      ].join("\n")
    );
  }
}

async function captureFeature(firefoxPath, feature, index) {
  const url = new URL(feature.path, BASE_URL).toString();

  const fileName = `${String(index + 1).padStart(2, "0")}-${safeFileName(
    feature.name
  )}.png`;

  const filePath = path.join(OUT_DIR, fileName);

  console.log("");
  console.log(`Capturing ${index + 1}/${FEATURES.length}: ${feature.description}`);
  console.log(`URL: ${url}`);

  // Firefox --screenshot captures after page load.
  // For React apps, a small delay can help if the previous Firefox process
  // is still releasing profile locks.
  await sleep(800);

  runFirefoxScreenshot(firefoxPath, url, filePath);

  console.log(`Saved: ${filePath}`);
}

async function main() {
  ensureDir(OUT_DIR);
  ensureDir(FIREFOX_PROFILE_DIR);

  const firefoxPath = getNormalFirefoxPath();

  await openFirefoxForManualLogin(firefoxPath);

  console.log("");
  console.log("Starting screenshots...");
  console.log("Make sure the Firefox login window is closed.");
  console.log("");

  // Give Firefox time to release the profile lock after you close it.
  await sleep(3000);

  for (let i = 0; i < FEATURES.length; i += 1) {
    try {
      await captureFeature(firefoxPath, FEATURES[i], i);
    } catch (error) {
      console.error("");
      console.error(`Failed to capture ${FEATURES[i].name}`);
      console.error(error.message);
    }
  }

  console.log("");
  console.log("Done.");
  console.log(`Screenshots are in: ${OUT_DIR}`);
  console.log("");
  console.log("If screenshots show the login page, run this again and make sure:");
  console.log("1. You logged in successfully.");
  console.log("2. You closed the Firefox window before pressing ENTER.");
  console.log("3. The same profile folder was used.");
}

main().catch((error) => {
  console.error("");
  console.error(error);
  process.exit(1);
});