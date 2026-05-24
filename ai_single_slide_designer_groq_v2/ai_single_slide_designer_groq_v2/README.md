# AI Single-Slide Designer for Google Slides — Groq + Sample Gallery

This is a Google Slides Apps Script add-on that designs **one slide at a time** from raw text.

Version 2 adds:

- A preview gallery of slide samples/styles.
- Groq API key support.
- Multiple Groq API key slots:
  - `GROQ_API_KEY`
  - `GROQ_API_KEY1`
  - ...
  - `GROQ_API_KEY9`
- Model rotation similar to the attached Python script.
- Local fallback if Groq fails or no key is saved.

## Why Apps Script instead of a Chrome extension?

A Chrome extension can inject UI into Google Slides, but reliable editing of slide objects requires Google Slides API authorization and more OAuth work.

Apps Script is the better first version because it runs directly inside Google Slides and can modify the current presentation.

## Install

1. Download and unzip this package.
2. Open a Google Slides presentation.
3. Go to **Extensions → Apps Script**.
4. Create these files in Apps Script and paste the matching contents:
   - `Code.gs`
   - `Groq.gs`
   - `Ai.gs`
   - `Themes.gs`
   - `LayoutEngine.gs`
   - `Sidebar.html`
5. In Apps Script, open **Project Settings**.
6. Enable **Show "appsscript.json" manifest file in editor**.
7. Open `appsscript.json` and replace it with the included manifest.
8. Click **Save**.
9. Reload the Google Slides tab.
10. Open **AI Slide Designer → Open designer**.
11. Authorize the script on first run.

## Use

1. Select a slide.
2. Open **AI Slide Designer → Open designer**.
3. Pick a preview sample.
4. Paste raw text.
5. Click **Design current**.

Use **Insert new** when you want to preserve the original slide and add exactly one new designed slide after it.

## Groq API keys

Paste one or more Groq keys into the sidebar, one per line, then click **Save keys**.

The add-on stores them in Apps Script User Properties using these names:

- first key: `GROQ_API_KEY`
- second key: `GROQ_API_KEY1`
- third key: `GROQ_API_KEY2`
- ...
- tenth key: `GROQ_API_KEY9`

When Groq is enabled, it tries each key for each model before failing.

## Groq model order

```text
meta-llama/llama-4-scout-17b-16e-instruct
groq/compound
groq/compound-mini
llama-3.3-70b-versatile
openai/gpt-oss-120b
openai/gpt-oss-20b
qwen/qwen3-32b
llama-3.1-8b-instant
allam-2-7b
```

## Sample gallery included

- Auto Designer
- Tech Split Brief
- Business 4 Cards
- Cyber Risk Panel
- Education Steps
- Science Research
- Minimal Quote
- Startup Hero
- Roadmap Timeline
- Before vs After
- Data Insight
- AI Product Feature
- Executive Summary

## License/template note

This package does not scrape or redistribute third-party slide templates. The previews and layouts are original style recipes inspired by common free presentation categories.
