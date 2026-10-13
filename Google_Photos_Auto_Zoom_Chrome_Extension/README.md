# Google Photos Auto Fit (Chrome / Edge / Brave)

Automatically enlarges small photos in **https://photos.google.com/** so that
one image dimension fills the corresponding usable viewing dimension, without
cropping or stretching its aspect ratio. Tabs, address bar and OS taskbar are
excluded automatically because the extension uses the actual photo viewport.
Opening the Google Photos information panel reduces the available viewing area.

## Installation

1. Extract the ZIP to a permanent folder.
2. Open `chrome://extensions` in Chrome (`edge://extensions` in Edge).
3. Switch on **Developer mode**, select **Load unpacked**, and choose the
   extracted folder **containing `manifest.json`**.
4. Refresh any already-open Google Photos tabs.

All settings are in the extension popup. Auto Fit starts enabled, with zero edge
padding. It works on opened photos, not the photo grid or thumbnails.

## Behavior

- Tries Google Photos' own mouse-wheel zoom automatically, then fits any
  remaining difference with a composited image scale. (Google Photos does not
  always allow its native zoom on small, low-resolution images.)
- Fits to the visible image viewer, including when the information panel opens,
  when the window is resized, or when another photo is selected.
- Never changes the browser's zoom level, intercepts user mouse-wheel scrolling,
  fetches photo URLs, or displays controls over the image.
- If you manually use the mouse wheel or drag the photo, automatic re-fitting
  pauses for that photo. Opening another photo automatically fits it again.
- Google Photos' HTML is not a public API; if Google redesigns its viewer,
  selector adjustments may be required.

No external JavaScript, tracking, remote services, or account access is used.
