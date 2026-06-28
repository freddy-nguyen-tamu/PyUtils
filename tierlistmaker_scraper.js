(async () => {
  const zipName = "black-myth-wukong-tier-images.zip";

  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  function cleanUrl(raw) {
    if (!raw) return null;
    raw = raw.trim();

    const urlMatch = raw.match(/url\(["']?(.*?)["']?\)/i);
    if (urlMatch) raw = urlMatch[1];

    if (!raw || raw === "none") return null;

    const u = new URL(raw, location.origin);
    u.pathname = u.pathname.replace(/\/{2,}/g, "/");
    return u.href;
  }

  const urls = [];
  const seen = new Set();

  const nodes = document.querySelectorAll(
    "#tier-container .character, #create-image-carousel .character, .character, img.draggable-filler"
  );

  for (const el of nodes) {
    const owner = el.closest(".character");
    const id = owner?.id || el.id || "";

    const bg = owner
      ? cleanUrl(owner.style.backgroundImage || getComputedStyle(owner).backgroundImage)
      : null;

    const src = el.tagName === "IMG" ? cleanUrl(el.src) : null;

    for (const url of [bg, src]) {
      if (
        url &&
        url.includes("/images/media/template_images/") &&
        !seen.has(url)
      ) {
        seen.add(url);
        urls.push({ url, id });
      }
    }
  }

  console.log(`Found ${urls.length} unique tier images.`);

  if (!urls.length) {
    console.error("No tier images found.");
    return;
  }

  function crc32(buf) {
    let table = crc32.table;
    if (!table) {
      table = crc32.table = new Uint32Array(256);
      for (let i = 0; i < 256; i++) {
        let c = i;
        for (let k = 0; k < 8; k++) {
          c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
        }
        table[i] = c >>> 0;
      }
    }

    let crc = -1;
    for (let i = 0; i < buf.length; i++) {
      crc = (crc >>> 8) ^ table[(crc ^ buf[i]) & 255];
    }
    return (crc ^ -1) >>> 0;
  }

  function dosDateTime(date = new Date()) {
    const time =
      (date.getHours() << 11) |
      (date.getMinutes() << 5) |
      Math.floor(date.getSeconds() / 2);

    const dosDate =
      ((date.getFullYear() - 1980) << 9) |
      ((date.getMonth() + 1) << 5) |
      date.getDate();

    return { time, dosDate };
  }

  function u16(n) {
    return new Uint8Array([n & 255, (n >>> 8) & 255]);
  }

  function u32(n) {
    return new Uint8Array([
      n & 255,
      (n >>> 8) & 255,
      (n >>> 16) & 255,
      (n >>> 24) & 255
    ]);
  }

  const encoder = new TextEncoder();
  const chunks = [];
  const central = [];
  let offset = 0;

  function push(part) {
    chunks.push(part);
    offset += part.length;
  }

  function extFromType(type, fallbackUrl) {
    if (type.includes("png")) return "png";
    if (type.includes("jpeg") || type.includes("jpg")) return "jpg";
    if (type.includes("webp")) return "webp";
    if (type.includes("gif")) return "gif";

    const path = new URL(fallbackUrl).pathname;
    const m = path.match(/\.([a-z0-9]+)$/i);
    return m ? m[1].toLowerCase() : "img";
  }

  for (let i = 0; i < urls.length; i++) {
    const { url, id } = urls[i];

    console.log(`Downloading ${i + 1}/${urls.length}: ${url}`);

    let res;
    try {
      res = await fetch(url, { credentials: "include" });
    } catch (err) {
      console.warn(`Fetch failed: ${url}`, err);
      continue;
    }

    if (!res.ok) {
      console.warn(`Skipped failed image: ${url}`, res.status);
      continue;
    }

    const arrayBuffer = await res.arrayBuffer();
    const data = new Uint8Array(arrayBuffer);
    const crc = crc32(data);
    const ext = extFromType(res.headers.get("content-type") || "", url);

    const originalName = decodeURIComponent(
      new URL(url).pathname.split("/").pop() || "image"
    ).replace(/[\\/:*?"<>|]/g, "_");

    const safeId = id ? `id-${id}-` : "";
    let filename = `${String(i + 1).padStart(3, "0")}-${safeId}${originalName}`;
    if (!filename.includes(".")) filename += `.${ext}`;

    const nameBytes = encoder.encode(filename);
    const { time, dosDate } = dosDateTime();
    const localOffset = offset;

    const localHeader = new Uint8Array([
      ...u32(0x04034b50),
      ...u16(20),
      ...u16(0),
      ...u16(0),
      ...u16(time),
      ...u16(dosDate),
      ...u32(crc),
      ...u32(data.length),
      ...u32(data.length),
      ...u16(nameBytes.length),
      ...u16(0),
      ...nameBytes
    ]);

    push(localHeader);
    push(data);

    const centralHeader = new Uint8Array([
      ...u32(0x02014b50),
      ...u16(20),
      ...u16(20),
      ...u16(0),
      ...u16(0),
      ...u16(time),
      ...u16(dosDate),
      ...u32(crc),
      ...u32(data.length),
      ...u32(data.length),
      ...u16(nameBytes.length),
      ...u16(0),
      ...u16(0),
      ...u16(0),
      ...u16(0),
      ...u32(0),
      ...u32(localOffset),
      ...nameBytes
    ]);

    central.push(centralHeader);

    await sleep(40);
  }

  const centralOffset = offset;
  for (const c of central) push(c);
  const centralSize = offset - centralOffset;

  const end = new Uint8Array([
    ...u32(0x06054b50),
    ...u16(0),
    ...u16(0),
    ...u16(central.length),
    ...u16(central.length),
    ...u32(centralSize),
    ...u32(centralOffset),
    ...u16(0)
  ]);

  push(end);

  const blob = new Blob(chunks, { type: "application/zip" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = zipName;
  document.body.appendChild(a);
  a.click();
  a.remove();

  setTimeout(() => URL.revokeObjectURL(a.href), 10000);

  console.log(`Done. Downloaded ${central.length} images into ${zipName}`);
})();