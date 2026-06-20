(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const urls = [...document.querySelectorAll("img.draggable-filler")]
    .map(img => img.src)
    .filter(Boolean);

  const uniqueUrls = [...new Set(urls)];

  console.log(`Found ${uniqueUrls.length} images`);

  for (let i = 0; i < uniqueUrls.length; i++) {
    const url = uniqueUrls[i];
    const originalName = decodeURIComponent(url.split("/").pop().split("?")[0]);
    const safeName = originalName.replace(/[\\/:*?"<>|]+/g, "_");
    const filename = `${String(i + 1).padStart(3, "0")}_${safeName}`;

    try {
      console.log(`[${i + 1}/${uniqueUrls.length}] downloading ${filename}`);

      const res = await fetch(url, { credentials: "include" });
      if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);

      const blob = await res.blob();
      const objectUrl = URL.createObjectURL(blob);

      const a = document.createElement("a");
      a.href = objectUrl;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      a.remove();

      URL.revokeObjectURL(objectUrl);

      await sleep(250);
    } catch (err) {
      console.error(`Failed: ${url}`, err);
    }
  }

  console.log("Done.");
})();