const DRIVE_SCOPE = "https://www.googleapis.com/auth/drive";
const DRIVE_DISCOVERY = "https://www.googleapis.com/discovery/v1/apis/drive/v3/rest";

let tokenClient = null;
let accessToken = null;
let gapiReady = false;
let gisReady = false;
let currentComic = null;
let currentChapter = null;
let currentImages = [];
let currentObserver = null;

// In-memory cache for the current browser tab.
// This keeps already visited chapters instant when you go next, then back.
const chapterFileCache = new Map(); // key: comicId:chapterNumber -> Drive file metadata list
const imageUrlCache = new Map();    // key: Drive file ID -> blob URL

const app = document.getElementById("app");
const authBox = document.getElementById("authBox");
const authStatus = document.getElementById("authStatus");
const signInBtn = document.getElementById("signInBtn");
const signOutBtn = document.getElementById("signOutBtn");

function showError(err, prefix = "Error") {
  const message = formatError(err);
  app.innerHTML = `<p class="error">${escapeHtml(prefix)}: ${escapeHtml(message)}</p>`;
  console.error(prefix, err);
}

function formatError(err) {
  if (!err) return "Unknown error";
  if (typeof err === "string") return err;
  if (err.message && typeof err.message === "string") return err.message;
  if (err.error && typeof err.error === "string") return err.error;
  if (err.details && typeof err.details === "string") return err.details;
  if (err.result && err.result.error) {
    const e = err.result.error;
    return e.message || JSON.stringify(e, null, 2);
  }
  if (err.body) {
    try {
      const parsed = JSON.parse(err.body);
      return parsed.error?.message || JSON.stringify(parsed, null, 2);
    } catch (_) {
      return String(err.body);
    }
  }
  try { return JSON.stringify(err, null, 2); } catch (_) { return String(err); }
}

window.addEventListener("error", (event) => showError(event.error || event.message, "JavaScript error"));
window.addEventListener("unhandledrejection", (event) => showError(event.reason, "Promise error"));

document.getElementById("viewAllBtn").onclick = () => { try { showComics(); } catch (err) { showError(err, "Could not show comics"); } };
signInBtn.onclick = signIn;
signOutBtn.onclick = signOut;

gapi.load("client", async () => {
  await gapi.client.init({ discoveryDocs: [DRIVE_DISCOVERY] });
  gapiReady = true;
  setupAuthIfReady();
});

window.onload = () => setupAuthIfReady();

function setupAuthIfReady() {
  if (!window.google || !google.accounts || !gapiReady || gisReady) return;
  tokenClient = google.accounts.oauth2.initTokenClient({
    client_id: SITE_CONFIG.clientId,
    scope: DRIVE_SCOPE,
    callback: (resp) => {
      if (resp.error) {
        authStatus.textContent = formatError(resp);
        return;
      }
      accessToken = resp.access_token;
      gapi.client.setToken({ access_token: accessToken });
      authStatus.textContent = "Signed in";
      signInBtn.hidden = true;
      signOutBtn.hidden = false;
    }
  });
  gisReady = true;
}

function needAuth() {
  authBox.hidden = false;
  if (!accessToken) authStatus.textContent = "Sign in before reading or uploading.";
}

function signIn() {
  setupAuthIfReady();
  if (!tokenClient) {
    authStatus.textContent = "Google login is still loading. Try again in a second.";
    return;
  }
  tokenClient.requestAccessToken({ prompt: accessToken ? "" : "consent" });
}

function signOut() {
  if (accessToken) google.accounts.oauth2.revoke(accessToken);
  accessToken = null;
  gapi.client.setToken(null);
  clearReaderCache();
  authStatus.textContent = "Signed out";
  signInBtn.hidden = false;
  signOutBtn.hidden = true;
}

function clearReaderCache() {
  chapterFileCache.clear();
  for (const url of imageUrlCache.values()) URL.revokeObjectURL(url);
  imageUrlCache.clear();
}

function getComics() {
  const saved = localStorage.getItem("comic_metadata");
  try {
    const comics = saved ? JSON.parse(saved) : SITE_CONFIG.comics;
    if (!Array.isArray(comics)) throw new Error("metadata is not a list");
    for (const comic of comics) {
      if (!comic || !comic.id || !Array.isArray(comic.roots)) {
        throw new Error("metadata has an invalid comic entry");
      }
      if (!comic.title && comic.name) comic.title = comic.name;
      if (!comic.title) comic.title = comic.id;
    }
    return comics;
  } catch (err) {
    localStorage.removeItem("comic_metadata");
    return SITE_CONFIG.comics;
  }
}

function saveComics(comics) {
  localStorage.setItem("comic_metadata", JSON.stringify(comics, null, 2));
}

function showComics() {
  const comics = getComics();
  app.innerHTML = "<h2>Comics</h2>";
  if (!comics.length) {
    app.innerHTML += '<p class="error">No comics are configured in config.js.</p>';
    return;
  }
  comics.forEach((comic) => {
    const div = document.createElement("div");
    div.className = "comic";
    div.innerHTML = `<b>${escapeHtml(comic.title)}</b><br>`;
    const listBtn = document.createElement("button");
    listBtn.textContent = "View list of chapters";
    listBtn.onclick = () => showChapterList(comic.id);
    const uploadBtn = document.createElement("button");
    uploadBtn.textContent = "Upload new chapter(s)";
    uploadBtn.onclick = () => showUpload(comic.id);
    div.append(listBtn, uploadBtn);
    app.appendChild(div);
  });
}

function findComic(id) {
  return getComics().find(c => c.id === id);
}

function showChapterList(comicId) {
  currentComic = findComic(comicId);
  if (!currentComic) {
    app.innerHTML = '<p class="error">Comic not found. Clear browser localStorage or reset metadata.</p>';
    return;
  }
  app.innerHTML = `<h2>${escapeHtml(currentComic.title)} chapters</h2>`;
  const back = document.createElement("button");
  back.textContent = "Back to comics";
  back.onclick = showComics;
  app.appendChild(back);

  for (let n = currentComic.firstChapter; n <= currentComic.lastChapter; n++) {
    const btn = document.createElement("button");
    btn.textContent = String(n);
    btn.onclick = () => readChapter(currentComic.id, n);
    app.appendChild(btn);
  }
}

async function readChapter(comicId, chapterNumber) {
  needAuth();
  if (!accessToken) return;
  currentComic = findComic(comicId);
  currentChapter = chapterNumber;

  const cacheKey = getChapterCacheKey(comicId, chapterNumber);
  const cachedFiles = chapterFileCache.get(cacheKey);
  if (cachedFiles) {
    renderChapter(cachedFiles);
    return;
  }

  app.innerHTML = `<p>Loading chapter ${chapterNumber}...</p>`;
  try {
    const folderId = await getChapterFolderId(currentComic, chapterNumber, false);
    if (!folderId) throw new Error("Chapter folder was not found.");

    // This only lists the JPG metadata. It does not download every page.
    const files = await listImages(folderId);
    chapterFileCache.set(cacheKey, files);
    renderChapter(files);
  } catch (err) {
    showError(err, `Could not load chapter ${chapterNumber}`);
  }
}

function renderChapter(files) {
  app.innerHTML = "";
  if (currentObserver) currentObserver.disconnect();

  const nav = document.createElement("div");
  nav.className = "nav";

  const prev = document.createElement("button");
  prev.textContent = "←";
  prev.onclick = previousChapter;

  const next = document.createElement("button");
  next.textContent = "→";
  next.onclick = nextChapter;

  const select = document.createElement("select");
  for (let n = currentComic.firstChapter; n <= currentComic.lastChapter; n++) {
    const opt = document.createElement("option");
    opt.value = n;
    opt.textContent = n;
    if (n === currentChapter) opt.selected = true;
    select.appendChild(opt);
  }
  select.onchange = () => readChapter(currentComic.id, Number(select.value));

  const list = document.createElement("button");
  list.textContent = "☰";
  list.onclick = () => select.focus();

  nav.append(prev, document.createTextNode(` Chapter ${currentChapter} `), next, list, select);
  app.appendChild(nav);

  if (!files || files.length === 0) {
    app.insertAdjacentHTML("beforeend", "<p>No JPG images found.</p>");
    return;
  }

  const status = document.createElement("p");
  status.textContent = `Found ${files.length} pages. Images load as you scroll.`;
  app.appendChild(status);

  currentObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const img = entry.target;
      currentObserver.unobserve(img);
      loadImageIntoElement(img, img.dataset.fileId, img.dataset.fileName);
    }
  }, { rootMargin: "1200px 0px" });

  for (const file of files) {
    const img = document.createElement("img");
    img.alt = file.name;
    img.dataset.fileId = file.id;
    img.dataset.fileName = file.name;
    img.loading = "lazy";
    img.decoding = "async";
    img.style.minHeight = "300px";
    img.style.display = "block";
    img.style.marginBottom = "12px";
    currentObserver.observe(img);
    app.appendChild(img);
  }
}

async function loadImageIntoElement(img, fileId, fileName) {
  try {
    let url = imageUrlCache.get(fileId);
    if (!url) {
      url = await getFileBlobUrl(fileId);
      imageUrlCache.set(fileId, url);
    }
    img.src = url;
    img.style.minHeight = "";
  } catch (err) {
    img.replaceWith(document.createTextNode(`Could not load ${fileName}: ${formatError(err)}`));
  }
}

function getChapterCacheKey(comicId, chapterNumber) {
  return `${comicId}:${chapterNumber}`;
}

function previousChapter() {
  if (currentChapter > currentComic.firstChapter) readChapter(currentComic.id, currentChapter - 1);
}

function nextChapter() {
  if (currentChapter < currentComic.lastChapter) readChapter(currentComic.id, currentChapter + 1);
}

document.addEventListener("keydown", (e) => {
  if (!currentComic || currentChapter == null) return;
  if (e.key === "ArrowLeft") previousChapter();
  if (e.key === "ArrowRight") nextChapter();
});

function showUpload(comicId) {
  needAuth();
  currentComic = findComic(comicId);
  app.innerHTML = `<h2>Upload chapters for ${escapeHtml(currentComic.title)}</h2>`;

  const metaLabel = document.createElement("p");
  metaLabel.textContent = "Edit upload metadata here. For best results, paste rootFolderId values for each root folder.";

  const meta = document.createElement("textarea");
  meta.value = JSON.stringify(currentComic, null, 2);

  const saveMeta = document.createElement("button");
  saveMeta.textContent = "Save metadata";
  saveMeta.onclick = () => {
    try {
      const updated = JSON.parse(meta.value);
      const comics = getComics().map(c => c.id === currentComic.id ? updated : c);
      saveComics(comics);
      currentComic = updated;
      status.textContent = "Metadata saved.";
      status.className = "ok";
    } catch (e) {
      status.textContent = e.message;
      status.className = "error";
    }
  };

  const resetMeta = document.createElement("button");
  resetMeta.textContent = "Reset metadata";
  resetMeta.onclick = () => {
    localStorage.removeItem("comic_metadata");
    currentComic = findComic(comicId);
    meta.value = JSON.stringify(currentComic, null, 2);
  };

  const chapterInput = document.createElement("input");
  chapterInput.type = "number";
  chapterInput.min = currentComic.firstChapter;
  chapterInput.max = currentComic.lastChapter;
  chapterInput.placeholder = "Chapter number";

  const fileInput = document.createElement("input");
  fileInput.type = "file";
  fileInput.multiple = true;
  fileInput.accept = ".jpg,.jpeg,image/jpeg";

  const uploadOne = document.createElement("button");
  uploadOne.textContent = "Upload selected JPGs as this chapter";
  uploadOne.onclick = () => uploadSingleChapter(Number(chapterInput.value), Array.from(fileInput.files));

  const dirInput = document.createElement("input");
  dirInput.type = "file";
  dirInput.multiple = true;
  dirInput.webkitdirectory = true;

  const uploadMany = document.createElement("button");
  uploadMany.textContent = "Upload selected folder of chapter folders";
  uploadMany.onclick = () => uploadChapterFolders(Array.from(dirInput.files));

  const status = document.createElement("p");

  app.append(metaLabel, meta, document.createElement("br"), saveMeta, resetMeta,
    document.createElement("hr"), chapterInput, fileInput, uploadOne,
    document.createElement("hr"), dirInput, uploadMany, status);
}

async function uploadSingleChapter(chapterNumber, files) {
  needAuth();
  if (!accessToken) return;
  if (!chapterNumber || files.length === 0) return alert("Pick a chapter number and JPG files.");
  const jpgs = files.filter(isJpg).sort((a, b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
  await uploadChapter(chapterNumber, jpgs);
}

async function uploadChapterFolders(files) {
  needAuth();
  if (!accessToken) return;
  const groups = new Map();
  for (const file of files.filter(isJpg)) {
    const path = file.webkitRelativePath || file.name;
    const m = path.match(/Conan_Chapter_(\d+)|Chapter[_ -]?(\d+)|(\d+)/i);
    if (!m) continue;
    const chapterNumber = Number(m[1] || m[2] || m[3]);
    if (!groups.has(chapterNumber)) groups.set(chapterNumber, []);
    groups.get(chapterNumber).push(file);
  }
  for (const [chapterNumber, chapterFiles] of [...groups.entries()].sort((a,b) => a[0] - b[0])) {
    chapterFiles.sort((a,b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
    await uploadChapter(chapterNumber, chapterFiles);
  }
  alert("Done uploading selected chapter folders.");
}

async function uploadChapter(chapterNumber, files) {
  const folderId = await getChapterFolderId(currentComic, chapterNumber, true);
  for (let i = 0; i < files.length; i++) {
    const file = files[i];
    const num = String(i + 1).padStart(currentComic.padImageNumberTo || 3, "0");
    const chapterText = String(chapterNumber).padStart(4, "0");
    const name = `${currentComic.imageFilePrefix}${chapterText}_${num}.${currentComic.imageExtension || "jpg"}`;
    await uploadDriveFile(folderId, name, file);
  }
}

function isJpg(file) {
  return /\.jpe?g$/i.test(file.name) || file.type === "image/jpeg";
}

function getRootForChapter(comic, chapterNumber) {
  const root = comic.roots.find(r => chapterNumber >= r.start && chapterNumber <= r.end);
  if (!root) throw new Error("No Drive root is configured for this chapter number.");
  return root;
}

async function getRootFolderId(root) {
  if (root.rootFolderId) return root.rootFolderId;
  const q = `mimeType='application/vnd.google-apps.folder' and name='${escapeQuery(root.rootFolderName)}' and trashed=false`;
  const res = await gapi.client.drive.files.list({ q, fields: "files(id,name)", spaces: "drive" });
  if (!res.result.files.length) throw new Error(`Root folder not found: ${root.rootFolderName}. Paste its folder ID into metadata.`);
  return res.result.files[0].id;
}

async function getChapterFolderId(comic, chapterNumber, createIfMissing) {
  const root = getRootForChapter(comic, chapterNumber);
  const rootId = await getRootFolderId(root);
  const folderName = `${comic.chapterFolderPrefix}${String(chapterNumber).padStart(4, "0")}`;
  const q = `'${rootId}' in parents and mimeType='application/vnd.google-apps.folder' and name='${escapeQuery(folderName)}' and trashed=false`;
  const res = await gapi.client.drive.files.list({ q, fields: "files(id,name)", spaces: "drive" });
  if (res.result.files.length) return res.result.files[0].id;
  if (!createIfMissing) return null;
  const created = await gapi.client.drive.files.create({
    resource: { name: folderName, mimeType: "application/vnd.google-apps.folder", parents: [rootId] },
    fields: "id"
  });
  return created.result.id;
}

async function listImages(folderId) {
  const q = `'${folderId}' in parents and mimeType contains 'image/' and trashed=false`;
  let all = [];
  let pageToken = null;
  do {
    const res = await gapi.client.drive.files.list({
      q,
      fields: "nextPageToken,files(id,name,mimeType)",
      pageSize: 1000,
      spaces: "drive",
      pageToken
    });
    all = all.concat(res.result.files || []);
    pageToken = res.result.nextPageToken || null;
  } while (pageToken);
  return all.sort((a,b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

async function getFileBlobUrl(fileId) {
  const url = `https://www.googleapis.com/drive/v3/files/${fileId}?alt=media`;
  const res = await fetch(url, { headers: { Authorization: `Bearer ${accessToken}` } });
  if (!res.ok) throw new Error("Could not load image from Drive.");
  return URL.createObjectURL(await res.blob());
}

async function uploadDriveFile(parentId, name, file) {
  const metadata = { name, parents: [parentId], mimeType: "image/jpeg" };
  const boundary = "-------comicreader" + Date.now();
  const body = new Blob([
    `--${boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n${JSON.stringify(metadata)}\r\n`,
    `--${boundary}\r\nContent-Type: image/jpeg\r\n\r\n`,
    file,
    `\r\n--${boundary}--`
  ], { type: `multipart/related; boundary=${boundary}` });

  const res = await fetch("https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart", {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}` },
    body
  });
  if (!res.ok) throw new Error(`Upload failed for ${name}`);
}

function escapeQuery(text) {
  return String(text).replace(/'/g, "\\'");
}

function escapeHtml(text) {
  return String(text).replace(/[&<>'"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;","\"":"&quot;"}[c]));
}
