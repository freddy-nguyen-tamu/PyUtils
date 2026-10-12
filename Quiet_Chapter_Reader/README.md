# Quiet Chapter Reader

A Chrome/Edge Manifest V3 extension for chapter pages on **truyenhentaiz.net**.

## Install

1. Extract the ZIP to a permanent folder.
2. Open `chrome://extensions` (or `edge://extensions`).
3. Enable **Developer mode**.
4. Click **Load unpacked** and select the extracted `Quiet_Chapter_Reader` folder.
5. Open or reload a chapter page on truyenhentaiz.net.

The chapter page shows **only the original chapter images**, centered with no
site UI, ads, reader controls, or text. The extension popup (toolbar icon) has
the scroll-speed slider and an auto-scroll toggle.

## Keys

- **Right Arrow:** next chapter.
- **Left Arrow:** previous chapter.
- **Up Arrow:** scroll up at the configured speed while held (a tap also steps).
- **Down Arrow:** scroll down at the configured speed while held (a tap also steps).
- **Enter:** turn auto-scroll on/off, remembered across chapters and reloads.

Auto-scroll advances at the saved speed, automatically opens the next listed
chapter at the bottom, resumes there, and stops at the final chapter.

**Mouse-wheel behavior is fully native.** The extension does not install any
wheel/mousewheel listener, cancel wheel events, or emulate wheel input. You can
scroll by mouse or trackpad normally; press **Enter** to turn off auto-scroll
when you want to navigate manually.

## Technical notes

- Only chapter pages containing `#chapter-content img` are replaced.
- URLs are read from the site's `#chaptersList` selector, including chapters
  with special suffixes. It never guesses next chapter URLs.
- Images are requested directly in order, lazily after the first two, with
  no re-encoding or downloading by the extension.
- Uses a sandboxed, **script-free iframe** to isolate the reader's browser-
  native scrolling from the site's JavaScript and CSS effects.
- Autoscroll speed and active state are stored with `chrome.storage.local`.
- If images can't be loaded, the extension won't automatically skip through
  an unreadable chapter. At the last chapter it turns off auto-scroll.

The extension is intentionally limited to the specified website. Other sites
are unaffected. It requires no network permissions beyond page access from
its content-script match patterns.
