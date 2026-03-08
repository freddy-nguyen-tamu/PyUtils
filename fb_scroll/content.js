(function() {
  // Create main container for UI
  const panel = document.createElement('div');
  panel.style.position = 'fixed';
  panel.style.bottom = '20px';
  panel.style.right = '20px';
  panel.style.width = '400px';
  panel.style.maxHeight = '500px';
  panel.style.backgroundColor = 'white';
  panel.style.border = '1px solid #ccc';
  panel.style.borderRadius = '8px';
  panel.style.boxShadow = '0 4px 12px rgba(0,0,0,0.15)';
  panel.style.zIndex = '9999';
  panel.style.display = 'flex';
  panel.style.flexDirection = 'column';
  panel.style.fontFamily = 'Arial, sans-serif';
  panel.style.fontSize = '12px';

  // Header
  const header = document.createElement('div');
  header.style.padding = '10px';
  header.style.backgroundColor = '#1877f2';
  header.style.color = 'white';
  header.style.fontWeight = 'bold';
  header.style.borderRadius = '8px 8px 0 0';
  header.style.display = 'flex';
  header.style.justifyContent = 'space-between';
  header.style.alignItems = 'center';
  header.innerHTML = '<span>Facebook Saved Scraper</span><button id="closePanel" style="background:none; border:none; color:white; cursor:pointer; font-size:18px;">X</button>';
  panel.appendChild(header);

  // Textarea for output
  const textarea = document.createElement('textarea');
  textarea.style.width = 'calc(100% - 20px)';
  textarea.style.margin = '10px';
  textarea.style.padding = '8px';
  textarea.style.border = '1px solid #ddd';
  textarea.style.borderRadius = '4px';
  textarea.style.fontFamily = 'monospace';
  textarea.style.fontSize = '11px';
  textarea.style.resize = 'vertical';
  textarea.style.height = '300px';
  textarea.readOnly = true;
  textarea.placeholder = 'Scraped video URLs will appear here...';
  panel.appendChild(textarea);

  // Button container
  const buttonDiv = document.createElement('div');
  buttonDiv.style.padding = '0 10px 10px 10px';
  buttonDiv.style.display = 'flex';
  buttonDiv.style.gap = '10px';
  buttonDiv.style.justifyContent = 'space-between';

  const startBtn = document.createElement('button');
  startBtn.textContent = 'Start Scraping';
  startBtn.style.flex = '1';
  startBtn.style.padding = '8px';
  startBtn.style.backgroundColor = '#1877f2';
  startBtn.style.color = 'white';
  startBtn.style.border = 'none';
  startBtn.style.borderRadius = '4px';
  startBtn.style.cursor = 'pointer';

  const stopBtn = document.createElement('button');
  stopBtn.textContent = 'Stop';
  stopBtn.style.flex = '1';
  stopBtn.style.padding = '8px';
  stopBtn.style.backgroundColor = '#dc3545';
  stopBtn.style.color = 'white';
  stopBtn.style.border = 'none';
  stopBtn.style.borderRadius = '4px';
  stopBtn.style.cursor = 'pointer';
  stopBtn.disabled = true;

  const copyBtn = document.createElement('button');
  copyBtn.textContent = 'Copy to Clipboard';
  copyBtn.style.flex = '1';
  copyBtn.style.padding = '8px';
  copyBtn.style.backgroundColor = '#28a745';
  copyBtn.style.color = 'white';
  copyBtn.style.border = 'none';
  copyBtn.style.borderRadius = '4px';
  copyBtn.style.cursor = 'pointer';

  buttonDiv.appendChild(startBtn);
  buttonDiv.appendChild(stopBtn);
  buttonDiv.appendChild(copyBtn);
  panel.appendChild(buttonDiv);

  document.body.appendChild(panel);

  // Close button functionality
  document.getElementById('closePanel').addEventListener('click', () => {
    panel.style.display = 'none';
  });

  let isScraping = false;
  let shouldStop = false;
  let scrapedUrls = [];
  let scrollAttempts = 0;
  const MAX_SCROLL_ATTEMPTS = 100;
  let previousHeight = 0;

  function updateTextarea() {
    textarea.value = scrapedUrls.join('\n');
    textarea.scrollTop = textarea.scrollHeight;
  }

  function extractVideoUrls() {
    const anchors = document.querySelectorAll('a[href]');
    const urls = [];
    anchors.forEach(anchor => {
      let href = anchor.getAttribute('href');
      if (!href) return;
      if (href.startsWith('/')) {
        href = 'https://www.facebook.com' + href;
      }
      if (href.match(/facebook\.com\/(watch\/\?v=|video\.php\?v=|reel\/\d+\/|.*\?v=\d+)/)) {
        urls.push(href);
      }
    });
    return urls;
  }

  async function scrollAndWait() {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(resolve => setTimeout(resolve, 3000));
  }

  async function startScraping() {
    if (isScraping) return;
    isScraping = true;
    shouldStop = false;
    scrapedUrls = [];
    scrollAttempts = 0;
    previousHeight = 0;
    updateTextarea();

    startBtn.disabled = true;
    stopBtn.disabled = false;
    copyBtn.disabled = true;

    let previousCount = 0;
    let noNewContentCount = 0;

    while (scrollAttempts < MAX_SCROLL_ATTEMPTS && !shouldStop) {
      const currentUrls = extractVideoUrls();
      for (let url of currentUrls) {
        if (!scrapedUrls.includes(url)) {
          scrapedUrls.push(url);
          updateTextarea();
        }
      }

      console.log(`Scrolled ${scrollAttempts + 1}, found ${scrapedUrls.length} videos`);

      if (scrapedUrls.length === previousCount) {
        noNewContentCount++;
        if (noNewContentCount >= 2) break;
      } else {
        noNewContentCount = 0;
      }

      previousCount = scrapedUrls.length;
      await scrollAndWait();
      scrollAttempts++;

      const newHeight = document.body.scrollHeight;
      if (newHeight === previousHeight && scrollAttempts > 1) break;
      previousHeight = newHeight;
    }

    isScraping = false;
    startBtn.disabled = false;
    stopBtn.disabled = true;
    copyBtn.disabled = false;

    if (scrapedUrls.length === 0) {
      textarea.value = 'No video URLs found. Make sure you are on the Saved page and items have loaded.';
    } else {
      textarea.value = scrapedUrls.join('\n');
    }
  }

  function stopScraping() {
    if (!isScraping) return;
    shouldStop = true;
    stopBtn.disabled = true;
    stopBtn.textContent = 'Stopping...';
    console.log('Stop requested');
  }

  function copyToClipboard() {
    const text = textarea.value;
    if (!text.trim()) return;
    const temp = document.createElement('textarea');
    temp.value = text;
    document.body.appendChild(temp);
    temp.select();
    document.execCommand('copy');
    document.body.removeChild(temp);
    alert('Copied to clipboard!');
  }

  startBtn.addEventListener('click', startScraping);
  stopBtn.addEventListener('click', stopScraping);
  copyBtn.addEventListener('click', copyToClipboard);
})();