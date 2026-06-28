(async function downloadAllCarouselImages() {
  console.log('Starting carousel image downloader...');

  // -------------------------------------------------------------------
  // 1. Get the main image element & the "Next" button
  // -------------------------------------------------------------------
  const img = document.getElementById('MainIMG');
  const nextBtn = document.querySelector('#btnNext, .btnNxt');

  if (!img) {
    console.error('Could not find #MainIMG.');
    return;
  }
  if (!nextBtn) {
    console.error('Could not find the "Next" button (.btnNxt / #btnNext).');
    return;
  }

  // Helper: get the absolute, current image URL
  const getCurrentSrc = () => img.src;

  // -------------------------------------------------------------------
  // 2. Optional: Try to find the total number of images from the UI
  //    (just for progress logging, we don't strictly need it)
  // -------------------------------------------------------------------
  let totalImages = 0;
  const allText = document.body.innerText;
  const match = allText.match(/(\d+)\s*[\/\-]\s*(\d+)/);
  if (match) {
    const possibleTotal = parseInt(match[2], 10);
    if (possibleTotal > 0 && possibleTotal < 100) {
      totalImages = possibleTotal;
      console.log(`ℹ️ Detected ~${totalImages} total images in the gallery.`);
    }
  }

  // -------------------------------------------------------------------
  // 3. Start capturing images by clicking "Next" until we loop back
  // -------------------------------------------------------------------
  const capturedUrls = new Set();
  let firstSrc = getCurrentSrc();
  capturedUrls.add(firstSrc);
  console.log(`[1] Captured: ${firstSrc.split('/').pop()}`);

  let clickCount = 0;
  const MAX_ITERATIONS = 50; // Safety limit to avoid infinite loops
  let isComplete = false;

  while (!isComplete && clickCount < MAX_ITERATIONS) {
    // --- Click "Next" ---
    nextBtn.click();
    clickCount++;

    // --- Wait for the new image to load ---
    // We wait 1.5 seconds to give the AJAX request and DOM update time.
    await new Promise(resolve => setTimeout(resolve, 1500));

    // --- Get the new URL ---
    const newSrc = getCurrentSrc();

    // --- Check if we've seen this URL before ---
    if (capturedUrls.has(newSrc)) {
      console.log(`URL repeated (${newSrc.split('/').pop()}). Stopping.`);
      isComplete = true;
      break;
    }

    // --- Save the new URL ---
    capturedUrls.add(newSrc);
    console.log(`[${capturedUrls.size}] Captured: ${newSrc.split('/').pop()}`);

    // Optional: if we know the total, stop when we reach it
    if (totalImages > 0 && capturedUrls.size >= totalImages) {
      console.log(`Reached the detected total (${totalImages}). Stopping.`);
      isComplete = true;
      break;
    }
  }

  if (clickCount >= MAX_ITERATIONS) {
    console.warn(`⚠️ Reached the safety limit of ${MAX_ITERATIONS} clicks. Stopping.`);
  }

  // -------------------------------------------------------------------
  // 4. Download all captured images
  // -------------------------------------------------------------------
  const finalUrls = Array.from(capturedUrls);
  console.log(`Downloading ${finalUrls.length} unique image(s)...`);

  async function downloadImage(url, index) {
    try {
      const response = await fetch(url);
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const blobUrl = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = blobUrl;
      let filename = url.split('/').pop().split('?')[0];
      if (!filename || !filename.includes('.')) {
        filename = `carousel_image_${index + 1}.jpg`;
      }
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(blobUrl);
      console.log(`Downloaded: ${filename}`);
    } catch (err) {
      console.warn(`⚠Fetch failed for ${url}, opening in new tab.`, err);
      window.open(url, '_blank');
    }
  }

  for (let i = 0; i < finalUrls.length; i++) {
    await downloadImage(finalUrls[i], i);
    // Small delay between downloads to avoid overwhelming the browser
    await new Promise(resolve => setTimeout(resolve, 500));
  }

  console.log('All done!');
})();