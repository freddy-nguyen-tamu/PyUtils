# ChatGPT Lazy Bubbles

A Chrome/Edge extension that reduces long ChatGPT conversation lag by making far-away message turns "sleep" while preserving their measured height, so the scroll position does not jump.

## How it works

Normal chat apps usually use list virtualization/windowing: only messages near the viewport are fully rendered. Older or far-away messages are represented by fixed-height placeholders until you scroll near them again.

This extension applies the same idea safely from a content script:

- Finds ChatGPT turn nodes using selectors like `[data-testid^="conversation-turn-"]`, `section[data-turn][data-turn-id]`, and `[data-turn-id-container]`.
- Measures each turn's rendered height.
- Keeps nearby turns active.
- Applies `content-visibility: hidden`, containment, and fixed preserved height to far-away turns.
- Restores turns as you scroll back to them.

It does not read, store, or transmit conversation content.

## Install unpacked

1. Unzip this folder.
2. Open `chrome://extensions` or `edge://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked**.
5. Select the unzipped `chatgpt-lazy-bubbles-extension` folder.
6. Open or reload a ChatGPT conversation.

## Settings

Click the extension icon to change:

- Enabled
- How many newest turns stay fully active
- How many screens above/below the viewport remain active
- How many turns must exist before virtualization starts
- Status pill visibility
- Debug logging

## Notes

This is intentionally conservative. It does not delete ChatGPT DOM nodes because ChatGPT is a React app and removing nodes behind React's back can break hydration, streaming, editing, copying, or scroll anchoring. CSS containment and content-visibility are safer.
