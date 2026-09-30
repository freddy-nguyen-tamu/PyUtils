# WebtoonScan Simple Reader

A local browser extension that replaces WebtoonScan's chapter UI with a single centered column of the chapter images and native browser scrolling.

## Controls

- **Right Arrow** — next chapter
- **Left Arrow** — previous chapter
- **Hold Down Arrow** — scroll down at the configured rate
- **Hold Up Arrow** — scroll up at the configured rate
- **Enter** — toggle auto-scroll on/off
- **Mouse wheel / touchpad / scrollbar** — normal native browser scrolling at all times

Mouse/touchpad scrolling is true native browser scrolling. The extension disables the Madara SmoothScroll wheel hijacker and does not prevent, stop, or replace wheel/touch events. If auto-scroll is ON, manual input temporarily gets priority; auto-scroll resumes about 0.7 seconds after you stop.

When the bottom is reached and a next chapter exists, the extension automatically opens it. The Enter auto-scroll state and scroll rate are stored with extension storage, so automatic scrolling continues across chapter changes when it was enabled.

There are no controls, labels, overlays, or buttons injected into the webpage itself. The extension popup only contains the scroll-rate setting.

## Install in Chrome / Edge

1. Extract the ZIP.
2. Open `chrome://extensions` in Chrome, or `edge://extensions` in Edge.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select the extracted `webtoonscan-simple-reader` folder.

## Install temporarily in Firefox

1. Extract the ZIP.
2. Open `about:debugging#/runtime/this-firefox`.
3. Choose **Load Temporary Add-on**.
4. Select `manifest.json` inside the extracted folder.

Firefox temporary add-ons are removed when Firefox exits. A permanently installed Firefox build would need normal Mozilla signing.
