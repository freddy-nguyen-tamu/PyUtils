
(async () => {
  const sleep = ms => new Promise(r => setTimeout(r, ms));

  const waitFor = async (fn, timeout = 4000) => {
    const start = Date.now();
    while (Date.now() - start < timeout) {
      const result = fn();
      if (result) return result;
      await sleep(80);
    }
    throw new Error("Timed out waiting for GitHub UI");
  };

  const panel = () =>
    [...document.querySelectorAll('[role="dialog"]')]
      .find(e => e.textContent.includes("Select repository permissions"));

  const addButton = () =>
    document.querySelector('button[aria-label="Add permissions"]');

  const rows = () =>
    [...document.querySelectorAll(
      '[data-listview-component="items-list"] > li'
    )];

  const rowName = row =>
    row.querySelector("h3")?.textContent.trim() || "";

  const rowByName = name =>
    rows().find(r => rowName(r) === name);

  const accessButton = row =>
    row?.querySelector('button[data-component="ActionMenu.Button"]');

  const accessText = row =>
    accessButton(row)?.textContent.replace(/\s+/g, " ").trim() || "";

  // STEP 1: Open Add permissions

  console.log("STEP 1: Selecting all repository permissions...");

  if (!panel()) {
    (await waitFor(addButton)).click();
  }

  let dialog = await waitFor(panel);

  // Save names rather than elements because React re-renders the DOM.

  const permissionNames = [
    ...dialog.querySelectorAll('li[role="option"][data-id]')
  ].map(e => e.getAttribute("data-id"));

  let added = 0;
  const selectFailures = [];

  for (const name of permissionNames) {
    try {
      if (!panel()) {
        (await waitFor(addButton)).click();
      }

      dialog = await waitFor(panel);

      const option = [
        ...dialog.querySelectorAll('li[role="option"][data-id]')
      ].find(e => e.getAttribute("data-id") === name);

      if (!option) continue;

      // Do not accidentally deselect existing permissions.
      if (option.getAttribute("aria-selected") === "true") {
        continue;
      }

      option.click();

      await waitFor(() => {
        const current = [
          ...(panel()?.querySelectorAll('li[role="option"][data-id]') || [])
        ].find(e => e.getAttribute("data-id") === name);

        return current?.getAttribute("aria-selected") === "true" ||
          (!current && !!rowByName(name));
      });

      added++;
      console.log("Selected:", name);

    } catch (error) {
      selectFailures.push(name);
      console.warn("Could not confirm selection:", name, error);
    }
  }

  console.log(`Selected ${added} additional permissions.`);

  // STEP 2: Close Add permissions panel.

  console.log("STEP 2: Closing permission selector...");

  if (panel()) {
    addButton()?.click();
    await sleep(200);

    if (panel()) {
      const target =
        panel().querySelector('input[aria-label="Search"]') ||
        panel();

      target.dispatchEvent(new KeyboardEvent("keydown", {
        key: "Escape",
        code: "Escape",
        bubbles: true,
        cancelable: true
      }));
    }

    await waitFor(() => !panel());
  }

  await sleep(300);

  // STEP 3: Change every available Read-only to Read and write.

  console.log("STEP 3: Changing permissions to Read and write...");

  let changed = 0;
  const skipped = new Set();
  const changedNames = [];

  for (let attempt = 0; attempt < 150; attempt++) {

    // Re-query on every iteration to handle React re-rendering.
    const row = rows().find(r =>
      /^Access:\s*Read-only$/i.test(accessText(r)) &&
      !skipped.has(rowName(r))
    );

    if (!row) break;

    const name = rowName(row);
    const button = accessButton(row);

    if (!button) {
      skipped.add(name);
      continue;
    }

    console.log("Changing:", name);

    button.click();

    // Find the actual GitHub ActionList option.
    const writeOption = await waitFor(() => {
      return [
        ...document.querySelectorAll(
          '[data-component="ActionList.Item.Label"]'
        )
      ].find(e =>
        e.textContent.trim() === "Read and write" &&
        e.getClientRects().length > 0
      );
    }, 1800).catch(() => null);

    if (!writeOption) {
      console.warn("Read and write unavailable:", name);
      skipped.add(name);

      // Dismiss this dropdown before continuing.
      accessButton(rowByName(name))?.click();
      await sleep(120);
      continue;
    }

    // Click its parent option, not just its text.
    const target =
      writeOption.closest(
        'li, [role="menuitemradio"], [role="menuitem"], [role="option"]'
      ) || writeOption;

    target.click();

    // Verify the permission actually changed.
    const success = await waitFor(() => {
      const current = rowByName(name);
      return /^Access:\s*Read and write$/i.test(accessText(current));
    }, 3000).catch(() => false);

    if (success) {
      changed++;
      changedNames.push(name);
      console.log("✓ Read and write:", name);
    } else {
      skipped.add(name);
      console.warn("Could not confirm update:", name);

      // Close any dropdown left open.
      const currentButton = accessButton(rowByName(name));
      if (currentButton?.getAttribute("aria-expanded") === "true") {
        currentButton.click();
      }
    }

    await sleep(100);
  }

  // STEP 4: Final verification.

  const remaining = rows()
    .filter(r => /^Access:\s*Read-only$/i.test(accessText(r)))
    .map(rowName);

  console.log("========== FINISHED ==========");
  console.log("New permissions selected:", added);
  console.log("Changed to Read and write:", changed);
  console.log("Selection failures:", selectFailures);
  console.log("Remaining Read-only:", remaining);
  console.log("Updated permissions:", changedNames);

  console.table(
    rows().map(r => ({
      Permission: rowName(r),
      Access: accessText(r)
    }))
  );

  console.log("Review the settings before generating your token.");
})();
