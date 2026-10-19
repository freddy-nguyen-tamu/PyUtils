# Gmail Keep & Clean — Adaptive UI v5.1.0

This Chrome Manifest V3 extension runs in your already signed-in Gmail tab. It requires **no Google Cloud project, Gmail API tokens, external server, or separate login**.

## Install

1. Stop the previous version. In `chrome://extensions`, remove old Gmail Keep & Clean versions so only one copy operates on Gmail.
2. Extract the ZIP and use **Load unpacked** to select the `gmail-keep-clean` folder.
3. Reload your signed-in Gmail tab and open **All Mail** from Gmail's sidebar.
4. Open **Keep & Clean**, verify all preservation rules, then use **Review 10** first.
5. For cleanup, type **MOVE TO TRASH** and click **Start cleaning**.

## How this build works

- Operates directly on Gmail's **All Mail** conversation list.
- Uses Gmail's list rows and the legacy conversation ID contained in each row.
- Opens a conversation using its visible subject and **verifies its ID** against the open conversation's heading before examining or deleting it.
- Checks actual expanded message cards, sender address, recipient addresses and visible recipient-details popovers.
- Opens genuinely collapsed cards and uses Gmail's **Show more messages** / **Expand all** controls where available.
- Keeps the **entire conversation** if *any* verified participant address matches an exact-email or `@domain` rule. Domain rules include subdomains.
- Does not delete conversations when any message is unreadable, recipient information is missing, or identities don't match.
- Uses Gmail's native **Back to All Mail** button, including its `title` attribute observed in the user's captured Gmail HTML. If Back fails or is absent, it tries Gmail's **All Mail sidebar link**.
- Uses Gmail's visible **Next results** button for pagination: no fabricated page routes, date boundaries, or Gmail search queries.
- Reports skip/keep/trash counts and supports review-only mode and pause.

The default settings contain the twelve user-supplied protection rules, with malformed `@@confirminsurance.com` and `@ebookviecom@gmail.com` entries corrected.

## Known limitations / safety

- Opening emails may mark them read. There is no guarantee this version preserves unread status.
- Gmail's HTML is undocumented and may change; **real-account verification is still required**.
- The extension can only use participant information that Gmail displays. Conversations with hidden or unverifiable recipients are skipped, not deleted.
- It is not a backup. Trash is not permanent storage; Gmail can automatically delete messages after 30 days in Trash.
- **Never run multiple installed versions simultaneously.** Do not interact with Gmail while an operation is running.
- It deliberately stops if an actual opened conversation ID differs from the selected row or if navigation can't be verified. It never tries guessed browser URLs as a fallback.

## Tests performed

- JavaScript rule-parser unit tests.
- Six simulated browser scenarios: cleanup, insufficient recipients, Review 10, Back button absent/sidebar fallback, actually collapsed message expansion, and mismatched open-conversation ID.
- In-memory, read-only structure test using the `title="Back to All Mail"` and expanded `.h7[aria-expanded="true"]` shapes from the supplied Gmail HTML, with dummy email addresses.

These tests are simulations, not a claim that all real Gmail layouts have been verified.
