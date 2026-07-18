const runBtn = document.getElementById('runBtn');
const statusEl = document.getElementById('status');

function setStatus(message) {
  statusEl.textContent = message;
}

function injectedTikTokCollector() {
  return (async () => {
    const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));

    // Helper: scroll to bottom and wait for new content
    const scrollToLoad = async () => {
      let stableCount = 0;
      const maxStable = 3; // stop after 3 scrolls with no height increase
      const delay = 1500;   // wait time between scrolls (ms)

      while (stableCount < maxStable) {
        const currentHeight = document.body.scrollHeight;
        window.scrollTo(0, currentHeight);
        await sleep(delay);

        const newHeight = document.body.scrollHeight;
        if (newHeight === currentHeight) {
          stableCount++;
        } else {
          stableCount = 0;
        }
      }
    };

    console.log('Scrolling to load all content...');
    await scrollToLoad();
    console.log('Finished scrolling.');

    // Collect unique URLs
    const videoUrls = new Set();
    const photoUrls = new Set();

    // Query all anchor elements that link to video or photo pages
    const allLinks = document.querySelectorAll('a[href*="/video/"], a[href*="/photo/"]');
    allLinks.forEach(link => {
      const href = link.href;
      if (href.includes('/video/')) {
        videoUrls.add(href);
      } else if (href.includes('/photo/')) {
        photoUrls.add(href);
      }
    });

    const videos = Array.from(videoUrls);
    const photos = Array.from(photoUrls);

    const lines = [];

    // Output in the required format
    console.log('\nvideo:');
    lines.push('video:');
    if (videos.length === 0) {
      console.log('(no videos found)');
      lines.push('(no videos found)');
    } else {
      videos.forEach(url => {
        console.log(url);
        lines.push(url);
      });
    }

    console.log('\nimage:');
    lines.push('');
    lines.push('image:');
    if (photos.length === 0) {
      console.log('(no images found)');
      lines.push('(no images found)');
    } else {
      photos.forEach(url => {
        console.log(url);
        lines.push(url);
      });
    }

    const summary = `Summary: ${videos.length} video(s), ${photos.length} image(s).`;
    console.log(`\n${summary}`);
    lines.push('');
    lines.push(summary);

    return {
      text: lines.join('\n') + '\n',
      videoCount: videos.length,
      photoCount: photos.length
    };
  })();
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  return tab;
}

async function run() {
  runBtn.disabled = true;
  setStatus('Running on current TikTok tab...\nDo not close the tab while it scrolls.');

  try {
    const tab = await getActiveTab();

    if (!tab || !tab.id || !tab.url || !/^https:\/\/(www\.)?tiktok\.com\//i.test(tab.url)) {
      throw new Error('Open a TikTok profile page first, then click the extension button.');
    }

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: injectedTikTokCollector
    });

    const payload = results && results[0] && results[0].result;
    if (!payload || typeof payload.text !== 'string') {
      throw new Error('No output returned from the TikTok page. Try refreshing the page and running again.');
    }

    const blob = new Blob([payload.text], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);

    await chrome.downloads.download({
      url,
      filename: 'py/ndhang.txt',
      saveAs: false,
      conflictAction: 'overwrite'
    });

    setStatus(`Done.\nSaved to: Downloads/py/ndhang.txt\nVideos: ${payload.videoCount}\nImages: ${payload.photoCount}`);

    setTimeout(() => URL.revokeObjectURL(url), 5000);
  } catch (err) {
    setStatus(`Error:\n${err.message || String(err)}`);
  } finally {
    runBtn.disabled = false;
  }
}

runBtn.addEventListener('click', run);
