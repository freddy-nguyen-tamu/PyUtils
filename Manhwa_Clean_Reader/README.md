# Manhwa Clean Reader v1.1.0

A Chrome / Edge / Brave Manifest V3 extension for chapter pages on
`https://manhwaread.com/` and `https://www.manhwaread.com/`.

## Install or update

1. Extract the ZIP to a normal folder (keep the `Manhwa_Clean_Reader` folder).
2. Open `chrome://extensions` or `edge://extensions` and enable **Developer mode**.
3. New installation: choose **Load unpacked** and select `Manhwa_Clean_Reader`.
4. Updating an existing installation: replace the old folder's files with the
   new ones, press **Reload** on the extension card, and refresh the chapter.
   If you extracted into a different folder, remove the previous installation
   before loading the new one, to avoid having two copies active.
5. Use the extension's toolbar popup to set scrolling speed or auto-scroll.

## Controls

- **Mouse wheel / touchpad / scrollbar:** ordinary browser-native scrolling.
- **Enter:** toggle auto-scroll (saved across chapters / browser restarts).
- **Up / Down:** scroll at the selected speed while held; tap for a small step.
- **Right / Left:** next / previous chapter from the actual chapter list.

Only centered images appear on the chapter page; no labels or controls appear
inside the reader. The popup holds the controls instead.

## v1.1.0 scrolling fix

The original version relocated the website's reader element to the document
body and depended on scrolling the root document. The diagnostic log showed
that mouse-wheel events reached the document but that its scroll position was
unchanged despite plenty of page height.

Version 1.1.0 **does not relocate any reading DOM elements**. The original
`#reader` becomes a fixed, browser-native scrolling viewport; it works even
if the website's root document doesn't scroll. Site lazy loaders retain their
image nodes and ancestors. Content-visibility tricks on panels were removed
in favor of preserving native lazy loading.

The extension NEVER attaches wheel listeners, cancels wheel input, emits
synthetic wheel events or substitutes JavaScript wheel scrolling. Keyboard
and auto-scroll use the scroll viewport's `scrollTop` independently.

Auto-scroll briefly yields to native/manual scrolling; it then resumes without
forgetting its saved enabled state. When the bottom of a fully loaded chapter
is reached it advances to the actual next chapter. At the last chapter,
auto-scroll stops. To avoid skipping images, advancement waits until all
chapter images have loaded successfully; an image that never loads can stop
progression rather than skip unread content.

## Validation and limitations

In automated headless Chromium tests with actual browser mouse-wheel input,
scrolling worked downward and upward in a fixture reproducing the site's
reader hierarchy. A separate test with the HTML snapshot supplied for the
chapter also scrolled natively in both directions. Keyboard input, automatic
scrolling, manual scrolling while auto-scroll is on, adjacent chapter-link
selection, chapter-end transition, and final-chapter stopping were exercised.

Those tests validate the extension logic and saved-page HTML, not a live
browser session on the actual website. If the site changes its markup or its
image-loading code, the extension may require a compatibility update. No
software can guarantee compatibility with every third-party script or browser
configuration without testing those environments.
