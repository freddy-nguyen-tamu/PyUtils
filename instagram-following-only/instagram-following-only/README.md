# Instagram Following Only

A small Manifest V3 extension for Chrome and Microsoft Edge that hides Instagram home-feed posts and Reels when the page indicates that the account is not followed.

## What it does

- Filters the Instagram home feed (`instagram.com/`)
- Filters the Reels viewer (`instagram.com/reels/` and individual `/reel/...` pages)
- Uses visible page signals, especially an exact **Follow** action near the account name
- Can also recognize common “Suggested for you” labels
- Includes conservative, balanced, and aggressive detection modes
- Does not call Instagram private APIs, read cookies, or store your Instagram credentials

## Install

1. Unzip `instagram-following-only.zip`.
2. Open `chrome://extensions` in Chrome, or `edge://extensions` in Microsoft Edge.
3. Enable **Developer mode**.
4. Select **Load unpacked**.
5. Choose the unzipped `instagram-following-only` folder.
6. Open or refresh Instagram.

Click the extension icon to change filtering options.

## Detection modes

- **Conservative:** hides only cards with a visible exact “Follow” action.
- **Balanced:** also hides cards with common suggested-content labels.
- **Aggressive:** additionally treats broad “recommended” and “sponsored” labels as filter signals.

Balanced is the default.

## Important limitations

Instagram changes its page structure frequently. This extension deliberately avoids private Instagram APIs, so it depends on visible interface text and DOM structure. That makes it safer and simpler, but not perfectly reliable.

Instagram can also be displayed in many languages. The extension includes several common translations of “Follow” and suggestion labels, but not every language.

The extension targets the home feed and Reels viewer. It does not attempt to filter profile grids, Explore grids, Stories, search results, or Direct Messages.

## Troubleshooting

- Refresh the Instagram tab after first installing the extension.
- Try **Aggressive** mode if suggested posts remain.
- Try **Conservative** mode if a followed account is hidden incorrectly.
- Enable placeholders to see why an item was hidden and reveal it once.
- If Instagram changes its markup, selectors and heuristics in `content.js` may need updating.

## Privacy

All processing happens locally in the browser. Settings are stored with Chrome's extension storage. No analytics, tracking, external requests, or remote code are included.

## License

MIT
