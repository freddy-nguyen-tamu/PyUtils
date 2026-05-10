# Conan Simple Site

A static Google Drive comic reader/uploader that can be deployed directly with GitHub Pages.

## Files

- `index.html` - the only page
- `config.js` - comic metadata and Google OAuth client ID
- `app.js` - reader, chapter navigation, Drive loading, and upload logic
- `styles.css` - minimal CSS
- `.nojekyll` - tells GitHub Pages to serve files exactly as-is

## Deploy on GitHub Pages

1. Create a GitHub repository, for example:

```text
conan-reader
```

2. Upload these files to the repository root:

```text
index.html
config.js
app.js
styles.css
.nojekyll
README.md
```

3. In the repository, open:

```text
Settings > Pages
```

4. Under **Build and deployment**, choose:

```text
Source: Deploy from a branch
Branch: main
Folder: / root
```

5. Save. Your website URL will look like:

```text
https://YOUR_GITHUB_USERNAME.github.io/conan-reader/
```

## Google OAuth setup for GitHub Pages

1. Open Google Cloud Console.
2. Create or edit an OAuth **Web application** client.
3. Add your GitHub Pages URL origin to **Authorized JavaScript origins**.

Use only the origin, not the full page path. Example:

```text
https://YOUR_GITHUB_USERNAME.github.io
```

If you also run locally, add:

```text
http://localhost:8000
```

4. Copy the OAuth Web Client ID into `config.js`:

```js
clientId: "YOUR_CLIENT_ID.apps.googleusercontent.com"
```

5. Share every Google Drive root folder with the Google account you sign in with on the website.

## Drive folder IDs

For best reliability, paste each root folder ID into `config.js` under `rootFolderId`.

The folder ID is the part after `/folders/` in a Drive URL.

Example:

```text
https://drive.google.com/drive/folders/1X8TZ3-5gG80UTmht7T8gh6LKKRJOOP3U
```

Folder ID:

```text
1X8TZ3-5gG80UTmht7T8gh6LKKRJOOP3U
```

## Expected folder format

Root folders:

```text
conan1
conan2
conan3
conan4
conan_last
```

Chapter folders:

```text
Conan_Chapter_1095
Conan_Chapter_1096
```

Images:

```text
Conan_Chapter_1095_001.jpg
Conan_Chapter_1095_002.jpg
```

## Use

- Click **View all comics**.
- Click **View list of chapters**.
- Click a chapter number.
- Use keyboard left/right arrows or the `←` and `→` buttons.
- Use `☰` to focus the chapter dropdown.
- Click **Upload new chapter(s)** to edit metadata and upload JPG files.
