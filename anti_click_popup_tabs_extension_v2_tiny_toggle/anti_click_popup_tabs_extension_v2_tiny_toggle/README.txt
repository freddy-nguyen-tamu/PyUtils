# Anti Click Popup Tabs v1.1.0

This Chrome extension blocks websites that open new tabs/popups when you click anywhere.

## New in this version

A tiny floating ON/OFF button appears in the bottom-right corner of each page.

- Green `ON` = protection is active for this website.
- Red `OFF` = protection is disabled for this website.
- Click the tiny button to toggle protection for the current website.

The button is very small: 34px wide and 18px tall.

## Install on Chrome

1. Unzip this file.
2. Open Chrome.
3. Go to:

   chrome://extensions

4. Turn on **Developer mode**.
5. Click **Load unpacked**.
6. Select the unzipped folder:

   anti_click_popup_tabs_extension_v2_tiny_toggle

## If updating from old version

1. Go to chrome://extensions
2. Remove the old Anti Click Popup Tabs extension.
3. Load this new unzipped folder.

## What it blocks

- window.open(...)
- target="_blank"
- javascript popup links
- new tabs spawned by the current page

## Settings

Click the extension icon to control:

- Enable extension
- Protect this website
- Aggressive auto-close new tabs
- Show tiny ON/OFF button
