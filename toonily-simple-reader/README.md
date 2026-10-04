# Toonily Simple Reader

A minimal Chrome/Edge Manifest V3 extension for Toonily chapter pages.

## What it does

- Replaces the chapter page with only the chapter images.
- Centers every image with no extra text, buttons, chapter header, ads, or page controls.
- Leaves ordinary mouse wheel / touchpad scrolling native to the browser.
- Disables Toonily's own scroll/key handlers from interfering with the reader.
- Prevents overscroll/bounce behavior with `overscroll-behavior: none`.
- Right Arrow: next chapter.
- Left Arrow: previous chapter.
- Down Arrow: scroll down by the configured key step.
- Up Arrow: scroll up by the configured key step.
- Enter: toggle auto-scroll on/off.
- Auto-scroll state is saved and survives next-chapter navigation.
- Auto-scroll automatically opens the next chapter after reaching the true bottom.
- At the final chapter, auto-scroll stops.
- Extension popup controls auto-scroll speed and arrow-key step.

## Install

1. Extract the ZIP.
2. Open `chrome://extensions/` in Chrome, or `edge://extensions/` in Edge.
3. Turn on **Developer mode**.
4. Choose **Load unpacked**.
5. Select the extracted `toonily-simple-reader` folder.
6. Open/reload a Toonily chapter page.

There is intentionally no reader UI drawn on the webpage itself.
