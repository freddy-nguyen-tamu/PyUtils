# Comix Reader Zoom & Keys

A small Chrome/Edge Manifest V3 extension for the Comix reader.

## Features

- **Browser zoom changes image width**: it reads the reader's own width percentage (50% on the supplied page) and multiplies it by the browser zoom factor, capped at full viewport width.
  - 100% browser zoom + 50% reader width = 50vw
  - 125% browser zoom + 50% reader width = 62.5vw
  - 150% browser zoom + 50% reader width = 75vw
  - 200% or higher + 50% reader width = 100vw
- **Right Arrow** clicks the enabled `Next chapter` button.
- **Left Arrow** clicks the enabled `Previous chapter` button.
- **Up/Down Arrow** scroll continuously while held.
- **Configurable scroll speed**: click the extension icon and enter a pixels-per-second value from 50 to 10000.
- **Auto-hide reader controls**: the top chapter bar, right-side toolbar, and bottom width controls stay hidden until the mouse enters the **right-most 1/4 of the viewport**. They remain visible while the pointer is over the controls, so you can move left across the top/bottom bar to click them.
- Keyboard overrides are skipped while typing in an input, textarea, select, contenteditable area, or textbox.

## Install in Chrome / Edge

1. Unzip this folder somewhere permanent.
2. Open `chrome://extensions` in Chrome, or `edge://extensions` in Edge.
3. Enable **Developer mode**.
4. Choose **Load unpacked** and select the `comix-reader-zoom-keys` folder.
5. Open/reload a `comix.to` reader page.
6. Pin the extension if you want quick access to the scroll-speed number.

## Notes

The extension targets the stable ARIA labels in the supplied reader markup rather than the SVG path itself. It also uses the reader's `--rpage-max-w` custom property, which is the width value shown as 50% in the supplied page. The auto-hide rule targets `.rpage-floatctl` and `.rpage-bottombar`, which contain the controls shown in the supplied screenshot.
