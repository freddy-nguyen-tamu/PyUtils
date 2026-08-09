# Gmail Merged TXT Exporter

A local Chrome Manifest V3 extension that starts from the currently open Gmail inbox, label, or search-results URL, visits each conversation, gathers visible message content and email-address metadata, and downloads everything as one TXT file.

It supports Gmail routes such as:

- `https://mail.google.com/mail/u/5/#inbox`
- `https://mail.google.com/mail/u/5/#inbox/p2`
- other Gmail label/search routes whose later pages end in `/p2`, `/p3`, and so on

## Install

1. Extract the ZIP file.
2. Open `chrome://extensions` in Chrome.
3. Turn on **Developer mode**.
4. Click **Load unpacked**.
5. Select the extracted `gmail-merged-exporter` folder.

## Use

1. Open Gmail and navigate to the inbox, label, or search results you want to export.
2. Click the extension icon.
3. Keep **Start from page 1** checked to remove a trailing `/p2`, `/p3`, etc. and begin at the first page.
4. Set **Pages to scan** to `0` to continue until Gmail has no older page, or enter a page limit.
5. Click **Start export**.
6. Keep the Gmail tab open. Progress appears in the bottom-right corner of Gmail.
7. At completion, Chrome downloads one file named like `gmail-merged-export-2026-08-04T18-00-00-000Z.txt`.

## Important limitations

- The extension reads Gmail's web interface, not the official Gmail API. Gmail can change its HTML structure, which may require selector updates.
- Opening unread conversations through the interface can mark them as read.
- Hidden recipient details are requested through Gmail's “Show details” control when available. Gmail sometimes displays aliases such as `me` without exposing the underlying address, so some `To`, `Cc`, or `Bcc` fields may be marked as not found.
- Collapsed or clipped content is expanded when Gmail exposes an accessible “Expand all” or “Show trimmed content” control.
- It only processes the active list and later/older pages. It does not cross into other labels automatically.
- Very large mailboxes can take a long time because each conversation must load in the browser.

## Privacy

All extraction occurs inside the open Gmail tab. The extension does not send message content to an external server. The only persistent data saved by the extension is its own scan settings.
