# YouTube Home Language Filter v1.1

This version fixes English filtering.

## Main fixes

- Custom regex is now case-insensitive.
- English filtering uses word hits instead of character ratio.
- Titles like `The Boys` can be filtered by setting **Minimum word hits** to `1`.
- Filtering still only runs on the exact homepage:

```text
https://www.youtube.com/
```

It does not filter search, playlist, watch, shorts, or other YouTube pages.

## Install

1. Unzip the folder.
2. Open Chrome.
3. Go to `chrome://extensions/`.
4. Enable **Developer mode**.
5. Click **Load unpacked**.
6. Select the unzipped `youtube-home-language-filter-v2` folder.

## To filter English

1. Choose **English/common English words**.
2. Set **Minimum word hits**:
   - `1` catches titles like `The Boys`.
   - `2` has fewer false positives.
3. Click **Save**.
4. Refresh `https://www.youtube.com/`.
