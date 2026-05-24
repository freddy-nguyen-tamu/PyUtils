const DEFAULT_SETTINGS = {
  enabled: true,
  language: "english",
  customRegex: "",
  threshold: 0.25,
  minWordHits: 2
};

const enabled = document.getElementById("enabled");
const language = document.getElementById("language");
const customBox = document.getElementById("customBox");
const customRegex = document.getElementById("customRegex");
const wordBox = document.getElementById("wordBox");
const minWordHits = document.getElementById("minWordHits");
const thresholdBox = document.getElementById("thresholdBox");
const threshold = document.getElementById("threshold");
const thresholdText = document.getElementById("thresholdText");
const save = document.getElementById("save");
const status = document.getElementById("status");

const WORD_LANGUAGES = new Set(["english", "custom"]);

function updateVisibility() {
  const isCustom = language.value === "custom";
  const isWordLanguage = WORD_LANGUAGES.has(language.value);

  customBox.classList.toggle("hidden", !isCustom);
  wordBox.classList.toggle("hidden", !isWordLanguage);
  thresholdBox.classList.toggle("hidden", isWordLanguage);
}

function updateThresholdText() {
  thresholdText.textContent = `${Math.round(Number(threshold.value) * 100)}% of title characters`;
}

function showStatus(message) {
  status.textContent = message;
  setTimeout(() => {
    status.textContent = "";
  }, 1500);
}

chrome.storage.sync.get(DEFAULT_SETTINGS, (settings) => {
  enabled.checked = settings.enabled;
  language.value = settings.language;
  customRegex.value = settings.customRegex;
  threshold.value = settings.threshold;
  minWordHits.value = settings.minWordHits;

  updateVisibility();
  updateThresholdText();
});

language.addEventListener("change", updateVisibility);
threshold.addEventListener("input", updateThresholdText);

save.addEventListener("click", () => {
  if (language.value === "custom" && customRegex.value.trim()) {
    try {
      new RegExp(customRegex.value.trim(), "giu");
    } catch (error) {
      showStatus("Invalid custom regex.");
      return;
    }
  }

  chrome.storage.sync.set(
    {
      enabled: enabled.checked,
      language: language.value,
      customRegex: customRegex.value.trim(),
      threshold: Number(threshold.value),
      minWordHits: Number(minWordHits.value || 1)
    },
    () => showStatus("Saved.")
  );
});
