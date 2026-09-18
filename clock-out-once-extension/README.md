# One-Time Clock Out

A tiny Chrome/Edge Manifest V3 extension for Intern Hub.

## What it does

- Lets you set one future local date/time from the extension popup.
- Shows a live countdown directly under the Intern Hub clock controls.
- At zero, clicks the existing `#checkout-btn` once.
- Uses the site's own button, so the site's existing `doCheckin('checkout')` flow handles the real checkout.
- Stores the schedule in `chrome.storage.local`, so a page refresh does not erase it.
- Uses a background one-time alarm plus the page countdown as redundant wakeups.
- Prevents multiple Intern Hub tabs from clicking more than once by claiming the schedule in the background worker before clicking.

## Install in Chrome / Edge

1. Unzip this folder.
2. Open `chrome://extensions` in Chrome or `edge://extensions` in Edge.
3. Enable **Developer mode**.
4. Choose **Load unpacked**.
5. Select the unzipped `clock-out-once-extension` folder.
6. Open or refresh your `intern-hub.html` page after installing the extension.

## Use

1. Make sure Intern Hub is open and you are checked in.
2. Click the extension icon.
3. Pick the date and time.
4. Click **Set**.
5. A **Scheduled Clock Out** countdown appears in the Intern Hub shift UI.
6. At zero, the extension clicks the visible `Clock Out` button once.

You can cancel from either the popup or the countdown panel on Intern Hub.

## Important behavior

The extension intentionally does **not** call Intern Hub's backend API directly. It clicks the site's existing Clock Out button.

For the click to happen, an Intern Hub page must be open and logged in, and the real `#checkout-btn` must be visible and enabled. If the target time passes while the browser/page is unavailable, the schedule remains due and will trigger when an eligible Intern Hub page becomes available, unless you cancel it.

The content script is limited to URLs whose path matches `intern-hub.html`.
