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
let currentReaderRun = 0;
let readerScrollAttached = false;
let readerScrollQueued = false;
let lastReaderImageWorkAt = 0;

const MAX_LOADED_IMAGES = 30;
const IMAGE_LOAD_MARGIN_PX = 1600;

// In-memory metadata/cache for the current browser tab.
// Chapter file metadata is tiny; JPG blob URLs are kept to a small sliding window.
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

function getDriveApiKey() {
  return String(SITE_CONFIG.apiKey || "").trim();
}

function canReadDrive() {
  return Boolean(accessToken || getDriveApiKey());
}

function needAuth(reason = "reading or uploading") {
  authBox.hidden = false;
  if (!accessToken) authStatus.textContent = `Sign in before ${reason}.`;
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
  releaseImageResources();
}

function releaseImageResources() {
  currentReaderRun += 1;
  if (currentObserver) {
    currentObserver.disconnect();
    currentObserver = null;
  }
  detachReaderScroll();
  for (const img of currentImages) {
    if (img && img.isConnected) img.removeAttribute("src");
  }
  currentImages = [];
  for (const url of imageUrlCache.values()) URL.revokeObjectURL(url);
  imageUrlCache.clear();
}

function detachReaderScroll() {
  if (!readerScrollAttached) return;
  window.removeEventListener("scroll", scheduleReaderImageWork);
  window.removeEventListener("resize", scheduleReaderImageWork);
  readerScrollAttached = false;
  readerScrollQueued = false;
  lastReaderImageWorkAt = 0;
}

function getComics() {
  const saved = localStorage.getItem("comic_metadata");
  try {
    const defaultComics = Array.isArray(SITE_CONFIG.comics) ? SITE_CONFIG.comics : [];
    const comics = saved ? JSON.parse(saved) : defaultComics;
    if (!Array.isArray(comics)) throw new Error("metadata is not a list");
    if (saved) {
      const savedIds = new Set(comics.map(comic => comic && comic.id));
      for (const comic of defaultComics) {
        if (comic && comic.id && !savedIds.has(comic.id)) comics.push(comic);
      }
    }
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

function hasConfiguredChapters(comic) {
  return Array.isArray(comic.chapters) && comic.chapters.length > 0;
}

function getChapterEntries(comic) {
  if (hasConfiguredChapters(comic)) {
    return comic.chapters.map((chapter, index) => {
      const number = Number(chapter.number != null ? chapter.number : index + 1);
      const title = chapter.title || chapter.name || chapter.folderName || String(number);
      return { ...chapter, number, title };
    }).filter(chapter => Number.isFinite(chapter.number));
  }

  const first = Number(comic.firstChapter || 1);
  const last = Number(comic.lastChapter || first);
  const chapters = [];
  for (let number = first; number <= last; number++) {
    chapters.push({ number, title: String(number) });
  }
  return chapters;
}

function getChapterEntry(comic, chapterNumber) {
  const number = Number(chapterNumber);
  return getChapterEntries(comic).find(chapter => chapter.number === number);
}

function getChapterLabel(comic, chapterNumber) {
  const chapter = getChapterEntry(comic, chapterNumber);
  return chapter ? chapter.title : String(chapterNumber);
}

function getChapterNavLabel(comic, chapterNumber) {
  const label = getChapterLabel(comic, chapterNumber);
  return hasConfiguredChapters(comic) ? label : `Chapter ${label}`;
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

  const chapters = getChapterEntries(currentComic);
  if (!chapters.length) {
    app.insertAdjacentHTML("beforeend", '<p class="error">No chapters are configured for this comic.</p>');
    return;
  }

  for (const chapter of chapters) {
    const btn = document.createElement("button");
    btn.textContent = chapter.title;
    btn.onclick = () => readChapter(currentComic.id, chapter.number);
    app.appendChild(btn);
  }
}

async function readChapter(comicId, chapterNumber) {
  if (!canReadDrive()) {
    needAuth("reading");
    return;
  }
  currentComic = findComic(comicId);
  if (!currentComic) {
    app.innerHTML = '<p class="error">Comic not found. Clear browser localStorage or reset metadata.</p>';
    return;
  }
  currentChapter = chapterNumber;
  const chapterLabel = getChapterNavLabel(currentComic, chapterNumber);

  const cacheKey = getChapterCacheKey(comicId, chapterNumber);
  const cachedFiles = chapterFileCache.get(cacheKey);
  if (cachedFiles) {
    renderChapter(cachedFiles);
    return;
  }

  app.innerHTML = "";
  const loading = document.createElement("p");
  loading.textContent = `Loading ${chapterLabel}...`;
  app.appendChild(loading);
  try {
    const folderId = await getChapterFolderId(currentComic, chapterNumber, false);
    if (!folderId) throw new Error("Chapter folder was not found.");

    // This only lists the JPG metadata. It does not download every page.
    const files = await listImages(folderId);
    chapterFileCache.set(cacheKey, files);
    renderChapter(files);
  } catch (err) {
    if (!accessToken && getDriveApiKey()) {
      authBox.hidden = false;
      authStatus.textContent = "Public read failed. Sign in if this folder is private or the API key restrictions need updating.";
    }
    showError(err, `Could not load ${chapterLabel}`);
  }
}

function renderChapter(files) {
  releaseImageResources();
  const readerRun = currentReaderRun;
  app.innerHTML = "";

  const nav = document.createElement("div");
  nav.className = "nav";

  const prev = document.createElement("button");
  prev.textContent = "←";
  prev.onclick = previousChapter;

  const next = document.createElement("button");
  next.textContent = "→";
  next.onclick = nextChapter;

  const select = document.createElement("select");
  const chapters = getChapterEntries(currentComic);
  for (const chapter of chapters) {
    const opt = document.createElement("option");
    opt.value = chapter.number;
    opt.textContent = chapter.title;
    if (chapter.number === currentChapter) opt.selected = true;
    select.appendChild(opt);
  }
  select.onchange = () => readChapter(currentComic.id, Number(select.value));

  const list = document.createElement("button");
  list.textContent = "☰";
  list.onclick = () => select.focus();

  nav.append(prev, document.createTextNode(` ${getChapterNavLabel(currentComic, currentChapter)} `), next, list, select);
  app.appendChild(nav);

  if (!files || files.length === 0) {
    app.insertAdjacentHTML("beforeend", "<p>No JPG images found.</p>");
    return;
  }

  const status = document.createElement("p");
  status.textContent = `Found ${files.length} pages. Images load as you scroll. Keeping up to ${MAX_LOADED_IMAGES} JPGs in memory.`;
  app.appendChild(status);

  currentObserver = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (!entry.isIntersecting) continue;
      const img = entry.target;
      loadImageIntoElement(img, img.dataset.fileId, img.dataset.fileName, img.dataset.resourceKey);
    }
  }, { rootMargin: `${IMAGE_LOAD_MARGIN_PX}px 0px` });

  for (let index = 0; index < files.length; index++) {
    const file = files[index];
    const img = document.createElement("img");
    img.alt = file.name;
    img.dataset.fileId = file.id;
    img.dataset.fileName = file.name;
    img.dataset.resourceKey = file.resourceKey || "";
    img.dataset.pageIndex = String(index);
    img.dataset.readerRun = String(readerRun);
    img.loading = "lazy";
    img.decoding = "async";
    img.style.minHeight = "300px";
    img.style.display = "block";
    img.style.marginBottom = "0";
    currentImages.push(img);
    currentObserver.observe(img);
    app.appendChild(img);
  }
  window.addEventListener("scroll", scheduleReaderImageWork, { passive: true });
  window.addEventListener("resize", scheduleReaderImageWork);
  readerScrollAttached = true;
  runReaderImageWork();
}

function scheduleReaderImageWork() {
  if (!currentImages.length) return;
  const now = Date.now();
  if (now - lastReaderImageWorkAt >= 100) {
    readerScrollQueued = false;
    runReaderImageWork();
    return;
  }
  if (readerScrollQueued) return;
  readerScrollQueued = true;
  setTimeout(() => {
    readerScrollQueued = false;
    runReaderImageWork();
  }, 0);
}

function runReaderImageWork() {
  lastReaderImageWorkAt = Date.now();
  loadNearbyImages();
  trimLoadedImages(getViewportCenterImageIndex());
}

function loadNearbyImages() {
  for (const img of currentImages) {
    if (!img.isConnected || !isImageNearViewport(img)) continue;
    loadImageIntoElement(img, img.dataset.fileId, img.dataset.fileName, img.dataset.resourceKey);
  }
}

function isImageNearViewport(img, margin = IMAGE_LOAD_MARGIN_PX) {
  const rect = img.getBoundingClientRect();
  return rect.bottom >= -margin && rect.top <= window.innerHeight + margin;
}

function getViewportCenterImageIndex() {
  let closestIndex = null;
  let closestDistance = Infinity;
  const viewportCenter = window.innerHeight / 2;

  for (const img of currentImages) {
    if (!img.isConnected) continue;
    const rect = img.getBoundingClientRect();
    const imageCenter = rect.top + rect.height / 2;
    const distance = Math.abs(imageCenter - viewportCenter);
    if (distance < closestDistance) {
      closestDistance = distance;
      closestIndex = Number(img.dataset.pageIndex);
    }
  }

  return closestIndex;
}

function trimLoadedImages(centerIndex) {
  const loadedImages = currentImages.filter(img => img.isConnected && img.dataset.loaded === "true");
  if (loadedImages.length <= MAX_LOADED_IMAGES) return;

  const fallbackCenter = Number.isFinite(centerIndex) ? centerIndex : 0;
  const removable = loadedImages.slice().sort((a, b) => {
    const aVisible = isImageNearViewport(a, 0);
    const bVisible = isImageNearViewport(b, 0);
    if (aVisible !== bVisible) return aVisible ? 1 : -1;
    const aDistance = Math.abs(Number(a.dataset.pageIndex) - fallbackCenter);
    const bDistance = Math.abs(Number(b.dataset.pageIndex) - fallbackCenter);
    return bDistance - aDistance;
  });

  for (let i = 0; i < loadedImages.length - MAX_LOADED_IMAGES; i++) {
    unloadImageElement(removable[i]);
  }
}

function unloadImageElement(img) {
  const fileId = img.dataset.fileId;
  const cachedUrl = fileId ? imageUrlCache.get(fileId) : null;
  if (cachedUrl) {
    URL.revokeObjectURL(cachedUrl);
    imageUrlCache.delete(fileId);
  }

  if (img.isConnected) {
    const currentHeight = img.getBoundingClientRect().height;
    img.style.minHeight = `${Math.max(300, Math.ceil(currentHeight))}px`;
    img.removeAttribute("src");
  }
  delete img.dataset.loaded;
  delete img.dataset.loading;
}

function getCachedImageUrl(fileId) {
  const cachedUrl = imageUrlCache.get(fileId);
  if (!cachedUrl) return null;
  imageUrlCache.delete(fileId);
  imageUrlCache.set(fileId, cachedUrl);
  return cachedUrl;
}

function setCachedImageUrl(fileId, url) {
  const oldUrl = imageUrlCache.get(fileId);
  if (oldUrl && oldUrl !== url) URL.revokeObjectURL(oldUrl);
  imageUrlCache.delete(fileId);
  imageUrlCache.set(fileId, url);
}

async function loadImageIntoElement(img, fileId, fileName, resourceKey = "") {
  if (img.dataset.loaded === "true" || img.dataset.loading === "true") return;
  if (img.dataset.readerRun !== String(currentReaderRun)) return;
  img.dataset.loading = "true";

  try {
    if (!accessToken) {
      img.onerror = () => {
        img.replaceWith(document.createTextNode(`Could not load ${fileName} from the public Drive URL.`));
      };
      img.src = buildPublicImageUrl(fileId, resourceKey);
      img.dataset.loaded = "true";
      img.style.minHeight = "";
      trimLoadedImages(Number(img.dataset.pageIndex));
      return;
    }

    let url = getCachedImageUrl(fileId);
    if (!url) {
      url = await getFileBlobUrl(fileId, resourceKey);
      if (img.dataset.readerRun !== String(currentReaderRun) || !img.isConnected) {
        URL.revokeObjectURL(url);
        return;
      }
      setCachedImageUrl(fileId, url);
    }
    img.src = url;
    img.dataset.loaded = "true";
    img.style.minHeight = "";
    trimLoadedImages(Number(img.dataset.pageIndex));
  } catch (err) {
    img.replaceWith(document.createTextNode(`Could not load ${fileName}: ${formatError(err)}`));
  } finally {
    delete img.dataset.loading;
  }
}

function buildPublicImageUrl(fileId, resourceKey = "") {
  const url = new URL(`https://lh3.googleusercontent.com/d/${encodeURIComponent(fileId)}`);
  if (resourceKey) url.searchParams.set("resourcekey", resourceKey);
  return url.toString();
}

function getChapterCacheKey(comicId, chapterNumber) {
  return `${comicId}:${chapterNumber}`;
}

function previousChapter() {
  const chapters = getChapterEntries(currentComic);
  const index = chapters.findIndex(chapter => chapter.number === currentChapter);
  if (index > 0) readChapter(currentComic.id, chapters[index - 1].number);
}

function nextChapter() {
  const chapters = getChapterEntries(currentComic);
  const index = chapters.findIndex(chapter => chapter.number === currentChapter);
  if (index >= 0 && index < chapters.length - 1) readChapter(currentComic.id, chapters[index + 1].number);
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
  const files = await listDriveFiles({ q, fields: "nextPageToken,files(id,name,resourceKey)" });
  if (!files.length) throw new Error(`Root folder not found: ${root.rootFolderName}. Paste its folder ID into metadata.`);
  return files[0].id;
}

async function getChapterFolderId(comic, chapterNumber, createIfMissing) {
  const chapter = getChapterEntry(comic, chapterNumber);
  if (chapter && chapter.folderId) return chapter.folderId;
  const root = getRootForChapter(comic, chapterNumber);
  const rootId = await getRootFolderId(root);
  const folderName = chapter && chapter.folderName
    ? chapter.folderName
    : `${comic.chapterFolderPrefix || ""}${String(chapterNumber).padStart(4, "0")}`;
  const q = `'${rootId}' in parents and mimeType='application/vnd.google-apps.folder' and name='${escapeQuery(folderName)}' and trashed=false`;
  const files = await listDriveFiles({ q, fields: "nextPageToken,files(id,name,resourceKey)" });
  if (files.length) return files[0].id;
  if (!createIfMissing) return null;
  if (!accessToken) throw new Error("Sign in before creating missing Drive folders.");
  const created = await gapi.client.drive.files.create({
    resource: { name: folderName, mimeType: "application/vnd.google-apps.folder", parents: [rootId] },
    fields: "id"
  });
  return created.result.id;
}

async function listImages(folderId) {
  const q = `'${folderId}' in parents and mimeType contains 'image/' and trashed=false`;
  const all = await listDriveFiles({ q, fields: "nextPageToken,files(id,name,mimeType,resourceKey)" });
  return all.sort((a,b) => a.name.localeCompare(b.name, undefined, { numeric: true }));
}

async function listDriveFiles({ q, fields }) {
  let all = [];
  let pageToken = null;
  do {
    const result = await fetchDriveJson("files", {
      q,
      fields,
      pageSize: 1000,
      spaces: "drive",
      pageToken
    });
    all = all.concat(result.files || []);
    pageToken = result.nextPageToken || null;
  } while (pageToken);
  return all;
}

async function fetchDriveJson(path, params) {
  const res = await fetch(buildDriveUrl(path, params), { headers: getDriveReadHeaders() });
  if (!res.ok) throw new Error(await getDriveResponseError(res, "Drive request failed."));
  return res.json();
}

async function getFileBlobUrl(fileId, resourceKey = "") {
  const params = { alt: "media" };
  if (resourceKey) params.resourceKey = resourceKey;
  const res = await fetch(buildDriveUrl(`files/${encodeURIComponent(fileId)}`, params), { headers: getDriveReadHeaders() });
  if (!res.ok) throw new Error(await getDriveResponseError(res, "Could not load image from Drive."));
  return URL.createObjectURL(await res.blob());
}

function buildDriveUrl(path, params = {}) {
  const url = new URL(`https://www.googleapis.com/drive/v3/${path}`);
  for (const [key, value] of Object.entries(params)) {
    if (value != null && value !== "") url.searchParams.set(key, value);
  }

  if (!accessToken) {
    const apiKey = getDriveApiKey();
    if (!apiKey) throw new Error("Sign in or add a Drive API key before reading Drive files.");
    url.searchParams.set("key", apiKey);
  }
  return url.toString();
}

function getDriveReadHeaders() {
  return accessToken ? { Authorization: `Bearer ${accessToken}` } : {};
}

async function getDriveResponseError(res, fallback) {
  try {
    const body = await res.json();
    return body.error?.message || fallback;
  } catch (_) {
    try {
      const text = await res.text();
      return text || fallback;
    } catch (__) {
      return fallback;
    }
  }
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
