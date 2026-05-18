(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));

  const safeName = (urlOrName, index) => {
    try {
      const u = new URL(urlOrName, location.href);
      const path = u.pathname.split("/").filter(Boolean).pop() || `style-${index}.css`;
      return `${String(index).padStart(2, "0")}-${path.replace(/[^a-zA-Z0-9._-]/g, "_")}`;
    } catch {
      return `${String(index).padStart(2, "0")}-${String(urlOrName).replace(/[^a-zA-Z0-9._-]/g, "_")}.css`;
    }
  };

  const downloadText = async (filename, text) => {
    const blob = new Blob([text], { type: "text/css;charset=utf-8" });
    const url = URL.createObjectURL(blob);

    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();

    await sleep(300);

    a.remove();
    URL.revokeObjectURL(url);
  };

  const cssLinks = [...document.querySelectorAll('link[rel~="stylesheet"][href]')]
    .map(link => link.href);

  const inlineStyles = [...document.querySelectorAll("style")]
    .map((style, i) => ({
      filename: `${String(i + 1).padStart(2, "0")}-inline-style.css`,
      css: style.textContent || ""
    }))
    .filter(item => item.css.trim());

  const manifest = {
    page: location.href,
    extractedAt: new Date().toISOString(),
    externalStylesheets: cssLinks,
    inlineStyleCount: inlineStyles.length
  };

  let index = 1;

  for (const href of cssLinks) {
    try {
      const response = await fetch(href, { credentials: "include" });

      if (!response.ok) {
        console.warn(`Failed to fetch ${href}: ${response.status}`);
        continue;
      }

      const css = await response.text();
      const filename = safeName(href, index++);

      await downloadText(filename, `/* Source: ${href} */\n\n${css}`);
      console.log(`Downloaded: ${filename}`);
    } catch (err) {
      console.warn(`Could not download CSS: ${href}`, err);
    }
  }

  for (const item of inlineStyles) {
    await downloadText(item.filename, `/* Source: inline <style> from ${location.href} */\n\n${item.css}`);
    console.log(`Downloaded: ${item.filename}`);
  }

  await downloadText(
    "css-extraction-manifest.json",
    JSON.stringify(manifest, null, 2)
  );

  console.log("Done extracting CSS.");
})();