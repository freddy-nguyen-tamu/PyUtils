# YouTube Live Chat Auto Close Simple - Fullscreen Click Fixed

This is the simple version.

It only clicks YouTube's own live chat close button.

Fixes:
- It DOES click while YouTube is fullscreen.
- After clicking, it sends pointerup/mouseup/click/pointercancel/mouseleave events and restores focus to the YouTube player.
- This is meant to prevent the fullscreen controls from getting stuck so mouse seeking/fast-forward still works.
- It stops after success.
- It stops after a short timeout if no live chat button appears.
- It does not remove elements, block requests, or add UI.

Install:
1. Extract the ZIP.
2. Open chrome://extensions.
3. Enable Developer mode.
4. Click Load unpacked.
5. Select the extracted folder.
