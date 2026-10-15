# Gmail Keep & Clean — Browser Mode

A Chrome extension that works in the Gmail web page you're **already signed into**. There is **no Google Cloud project, API key, OAuth client, or second Google login**.

## Install

1. Extract this ZIP.
2. Visit `chrome://extensions` in Chrome, turn on **Developer mode**, and choose **Load unpacked**.
3. Select the `gmail-browser-cleaner` folder (containing `manifest.json`).
4. Keep your already-open `https://mail.google.com/` tab signed in. **No reload is required.** Click the extension icon; it injects the controls directly into that Gmail tab. The blue **Keep & Clean** chip then appears inside Gmail.

## Usage

Enter one keep rule per line, e.g.:

```
important.person@example.com
@mycompany.com
subject:"contract renewal"
query:from:(notifications@service.com) has:attachment
is:starred
```

A bare email address matches messages *from OR to* that address (including cc/bcc), and keeps the **entire conversation**. A domain rule matches a domain; a Gmail search query can also be used. Gmail's own search determines which conversations match.

1. Choose whether to scan all mail or inbox only (default: all mail, which includes sent and archived mail, but excludes Spam and Trash).
2. Click **Scan & preview**. The extension navigates Gmail search results page by page, collecting protected **thread IDs** for every keep rule plus draft conversations. It then scans the target mail to count kept vs. unprotected conversations. Nothing is deleted while previewing.
3. Inspect the counts and sample subjects. Check the explicit confirmation, type **MOVE TO TRASH**, and click **Trash unprotected threads**.
4. Leave the Gmail tab open and don't click around while it runs. It uses Gmail's own conversation checkboxes and Delete control, in small, verified batches. It does *not* use `-from` or `-label` exclusions to choose what to delete: those can accidentally match portions of protected conversations.

You can pause safely. If you want to resume later, first re-run **Scan & preview** so newly arrived messages are considered. A paused run may already have moved some conversations to Trash.

## Important limitations / safety

- **Gmail conversation view must be ON.** If it's OFF, Gmail may show messages separately; stop and enable it in Gmail Settings before cleaning.
- No active drafts are deleted. The extension scans and protects draft conversations automatically.
- It never empties Trash or permanently deletes mail, but Gmail normally purges Trash after **30 days**. Google notes that deleting a *draft* is not recoverable, which is why draft conversations are excluded.
- This tool works by clicking the currently rendered Gmail UI. Gmail can change its HTML or button labels, particularly in non-English locales. The extension deliberately **stops** rather than guesses when a row ID, checkbox, navigation or Trash button cannot be verified.
- Preview is a snapshot, **not a continuous live protection rule**. New mail arriving after a preview is not necessarily protected. Finish promptly with Gmail otherwise idle; re-scan if your inbox changes.
- Large mailboxes require paging through many Gmail screens and may take a long time. Keep the Gmail tab active. Gmail may impose a temporary rate limit. Pause/retry after scanning if needed.
- An existing manually selected checkbox can interfere with automation. Deselect all Gmail messages before starting.
- The extension runs only on `mail.google.com` and makes **no direct network requests**. All rules and preview thread IDs are saved locally in Chrome extension storage, scoped to the Gmail URL account slot. Avoid switching accounts at that path during a run.
- The preview must complete before deletion. If a Gmail UI control is missing, or Gmail displays an unexpected confirmation dialog, the process stops.
- The Gmail website is not an officially supported automation API. This implementation is best-effort and **cannot be guaranteed across every layout or Gmail redesign**. Test on a small mailbox or test account before running against important data; export mail using Google Takeout if you need a backup.

## Why not just use `-from:...`?

Gmail search matches **messages** but groups results as conversations. If one message in a thread matches a negative filter while another message matches a keep rule, that thread can still appear in deletion results. This extension instead explicitly creates a set of **protected thread IDs** from the keep searches and compares every mail row to that set before selecting it.

## Scope and privacy

- Manifest V3; Chrome permissions: `storage`, `activeTab`, `scripting`, and the Gmail host origin (scripting is used to add the controls to an already-open tab without a reload).
- No OAuth, API setup, cookies extracted, or external servers.
- No access to other sites.
- Source code is included, including Node test cases for rule parsing and thread-level filtering.
