# ChatGPT Conversation Exporter (Chrome extension)

## What it does

Click the extension icon while viewing a conversation at `https://chatgpt.com/c/<conversation-id>`. The extension injects the provided exporter into the page's **MAIN** JavaScript world. The exporter logs progress in that tab's DevTools Console and downloads one `.txt` file containing the active conversation branch and any attachment content it can retrieve.

The exporter uses same-origin ChatGPT requests and attachment download URLs. It does not send conversation content to a third-party server, and the supplied script uses GET requests only.

## Install in Chrome

1. Extract this ZIP to a folder you will keep.
2. Open `chrome://extensions`.
3. Turn on **Developer mode**.
4. Click **Load unpacked** and select the extracted `chatgpt-conversation-exporter-extension` folder.
5. Open or reload the ChatGPT conversation you want to export.
6. Click the extension icon. A `RUN` badge means the script was injected; it does **not** mean the export has finished. The page console shows progress, and the TXT download starts when processing completes.

To see the logs, open DevTools on the ChatGPT tab (F12 or Ctrl+Shift+J on Windows/Linux) and select **Console**. The exporter starts even if DevTools is closed.

## Notes

- Only works on a conversation URL matching `chatgpt.com/c/<conversation-id>`.
- Uses `activeTab` and `scripting` permissions; it does not request permanent access to all websites.
- Large attachments may take longer, and some binary files are represented as base64 in the TXT file when no text extractor is available.
