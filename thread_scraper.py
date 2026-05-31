(async function getAllThreadsPostLinks() {
  const postLinks = new Set();
  let previousHeight = 0;
  let noNewPostsCount = 0;
  const maxNoNewPostsAttempts = 3;
  const scrollDelay = 2000;

  function extractLinks() {
    const anchors = document.querySelectorAll('a[href*="/post/"]');
    for (let a of anchors) {
      let href = a.href;
      // Remove tracking parameters (optional)
      // href = href.split('?')[0];
      postLinks.add(href);
    }
    return postLinks.size;
  }

  let lastCount = extractLinks();
  console.log(`Initial posts found: ${lastCount}`);

  while (true) {
    window.scrollTo(0, document.body.scrollHeight);
    await new Promise(resolve => setTimeout(resolve, scrollDelay));

    const newCount = extractLinks();
    const currentHeight = document.body.scrollHeight;

    if (newCount === lastCount && currentHeight === previousHeight) {
      noNewPostsCount++;
      if (noNewPostsCount >= maxNoNewPostsAttempts) break;
    } else {
      noNewPostsCount = 0;
    }
    previousHeight = currentHeight;
    lastCount = newCount;
  }

  // Print one URL per line (no JSON, no commas)
  for (let url of postLinks) {
    console.log(url);
  }

  // Optional: copy to clipboard as plain text (each URL on new line)
  copy(Array.from(postLinks).join('\n'));
})();