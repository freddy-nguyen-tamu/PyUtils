(async () => {
    // Helper: scroll to bottom and wait for new content
    const scrollToLoad = async () => {
        let previousHeight = 0;
        let stableCount = 0;
        const maxStable = 3; // stop after 3 scrolls with no height increase
        const delay = 1500;   // wait time between scrolls (ms)

        while (stableCount < maxStable) {
            const currentHeight = document.body.scrollHeight;
            window.scrollTo(0, currentHeight);
            await new Promise(resolve => setTimeout(resolve, delay));
            const newHeight = document.body.scrollHeight;
            if (newHeight === currentHeight) {
                stableCount++;
            } else {
                stableCount = 0;
                previousHeight = newHeight;
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

    // Output in the required format
    console.log('\nvideo:');
    if (videoUrls.size === 0) {
        console.log('(no videos found)');
    } else {
        Array.from(videoUrls).forEach(url => console.log(url));
    }

    console.log('\nimage:');
    if (photoUrls.size === 0) {
        console.log('(no images found)');
    } else {
        Array.from(photoUrls).forEach(url => console.log(url));
    }

    console.log(`\nSummary: ${videoUrls.size} video(s), ${photoUrls.size} image(s).`);
})();