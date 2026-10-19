# Reddit Carousel Downloader

Chrome Manifest V3 extension for downloading an open Reddit image carousel, starting at the slide currently displayed. Its downloading loop remains active when the extension popup closes.

## Installation

1. Extract the ZIP.
2. Open `chrome://extensions/` in Chrome and enable **Developer mode**.
3. Select **Load unpacked**, then choose the extracted `reddit-carousel-downloader` folder.
4. Open a Reddit post and expand its image gallery/lightbox. Navigate to the slide where you want to begin.
5. Open the extension icon and select **Download carousel**.

Images are saved under your Chrome Downloads folder, in `Reddit Carousel/<post-title>_<post-id>/`. The extension starts at the current slide, saves images one at a time, then clicks Reddit's **Next page** control until it is disabled, disappears, or the gallery stops changing. Filenames include slide indexes. The popup displays progress and allows stopping the run. The Chrome setting **Ask where to save each file before downloading** should be disabled for fully unattended operation.

## How it adapts to Reddit

- Searches within the visible gallery/lightbox instead of assuming there is only one gallery on the page.
- Searches **open Shadow DOM**, where Reddit renders its current navigation buttons.
- Locates forward navigation by semantics (Next page / slide / image), slotted navigation, and a right-arrow icon fallback instead of a fragile generated CSS class.
- Selects the current slide from Reddit's index announcement, visible slide state, or geometry.
- Downloads original `i.redd.it` files where possible, falling back to Reddit previews on download failure.
- Keeps track of both carousel positions and image identities, skips repeated media, and stops if advancing cannot be confirmed. Includes a 2,000-position safety cap.

The extension will tolerate many markup changes, but no browser extension can guarantee compatibility with every future redesign. It does not bypass private or inaccessible media, download videos, or download images from unrelated sites. Clicking Start grants temporary access to the selected Reddit tab rather than blanket access to every website.

## Troubleshooting

- If Chrome says **Could not start**, reload the Reddit page and reopen its image gallery before trying again.
- If it says **The current slide has no downloadable Reddit image URL**, check whether you're looking at a video or an image hosted outside Reddit.
- If a carousel stops early, the extension will not click blindly when it cannot verify a change; the status explains why it stopped.
- Files are downloaded by Chrome. Chrome may block or interrupt a download because of its own safety settings, missing permissions, or network failures.
