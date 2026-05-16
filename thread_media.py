(function downloadAllThreadsImages() {
  // Set to store unique image URLs
  const imageUrls = new Set();
  let lastScrollHeight = 0;
  let noNewImagesCount = 0;

  // Function to extract image URLs from current DOM
  function extractImageUrls() {
    // Select all <img> elements that belong to post images (not avatars)
    // Targets images with class containing 'xl1xv1r' and src containing Instagram CDN pattern
    const images = document.querySelectorAll('img.xl1xv1r[src*="cdninstagram.com"]');
    let newFound = false;
    images.forEach(img => {
      // Prefer the 'src' attribute (usually the highest quality - 4096w variant)
      let url = img.src;
      // If src is a placeholder or missing, try srcset
      if (!url || url.includes('blur') || url.includes('placeholder')) {
        const srcset = img.srcset;
        if (srcset) {
          // Get the last (largest) URL from srcset
          const candidates = srcset.split(',').map(s => s.trim().split(' ')[0]);
          url = candidates[candidates.length - 1];
        }
      }
      if (url && url.startsWith('https://') && !imageUrls.has(url)) {
        imageUrls.add(url);
        newFound = true;
        console.log(`Found image: ${url}`);
      }
    });
    return newFound;
  }

  // Function to scroll to bottom and wait for new content
  async function scrollAndWait() {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(resolve => setTimeout(resolve, 2000)); // wait for lazy load
    const newHeight = document.body.scrollHeight;
    if (newHeight === lastScrollHeight) {
      noNewImagesCount++;
    } else {
      noNewImagesCount = 0;
      lastScrollHeight = newHeight;
    }
  }

  // Function to download a file from URL
  async function downloadImage(url, index) {
    try {
      const response = await fetch(url, { mode: 'cors' });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const link = document.createElement('a');
      const objectUrl = URL.createObjectURL(blob);
      link.href = objectUrl;
      // Extract filename from URL or use index + extension
      const urlParts = url.split('/');
      let filename = urlParts.pop().split('?')[0] || `image_${index}.jpg`;
      if (!filename.match(/\.(jpg|jpeg|png|webp|mp4)$/i)) filename += '.jpg';
      link.download = filename;
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      URL.revokeObjectURL(objectUrl);
      console.log(`Downloaded: ${filename}`);
    } catch (err) {
      console.error(`Failed to download ${url}:`, err);
    }
  }

  // Main async function
  (async () => {
    console.log('Starting image extraction. Scrolling to load all posts...');
    // Keep scrolling until no new images for 3 consecutive scrolls
    while (noNewImagesCount < 3) {
      const before = imageUrls.size;
      await scrollAndWait();
      const foundNew = extractImageUrls();
      if (!foundNew && before === imageUrls.size) {
        // no new images found in this scroll cycle
        console.log(`No new images after scroll #${noNewImagesCount + 1}`);
      } else {
        console.log(`Found ${imageUrls.size - before} new images. Total: ${imageUrls.size}`);
      }
    }

    console.log(`Finished scrolling. Found ${imageUrls.size} unique images. Starting downloads...`);
    const urls = Array.from(imageUrls);
    for (let i = 0; i < urls.length; i++) {
      await downloadImage(urls[i], i + 1);
      // small delay to avoid overwhelming the network
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    console.log('All downloads completed.');
  })();
})();