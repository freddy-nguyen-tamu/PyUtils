(async function () {
  const recordedVideos = new Set();
  const maxRecordTime = 60000;   // 60 seconds per story (fallback)

  function saveBlob(blob, name) {
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  async function recordFullVideo(video) {
    if (recordedVideos.has(video)) return;
    recordedVideos.add(video);
    const index = recordedVideos.size;
    console.log(`Recording story ${index}...`);

    // Make sure video plays
    video.muted = false;  // Some browsers require mute for autoplay
    video.play().catch(() => {});
    await new Promise(r => {
      if (video.readyState >= 2) r();
      else video.addEventListener('loadedmetadata', r, { once: true });
    });

    const stream = video.captureStream();
    const recorder = new MediaRecorder(stream, { mimeType: 'video/webm;codecs=vp9' });
    const chunks = [];
    recorder.ondataavailable = e => chunks.push(e.data);

    let stopped = false;
    const stopRecording = () => {
      if (stopped) return;
      stopped = true;
      if (recorder.state === 'recording') recorder.stop();
    };

    // Stop when video is removed from DOM (story changed)
    const observer = new MutationObserver(() => {
      if (!document.body.contains(video)) {
        console.log(`Story ${index} ended (video removed).`);
        observer.disconnect();
        clearTimeout(timeout);
        stopRecording();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // Fallback timeout
    const timeout = setTimeout(() => {
      console.log(`Timeout for story ${index}.`);
      observer.disconnect();
      stopRecording();
    }, maxRecordTime);

    recorder.start(1000); // get data every second

    // When recorder stops, save
    recorder.onstop = () => {
      observer.disconnect();
      const blob = new Blob(chunks, { type: 'video/webm' });
      saveBlob(blob, `fb_story_${index}.mp4`);
      console.log(`Saved story ${index} (${(blob.size/1024/1024).toFixed(1)} MB)`);
    };
  }

  // Watch for new <video> elements
  function scanAndRecord() {
    document.querySelectorAll('video').forEach(v => recordFullVideo(v));
  }
  scanAndRecord();

  const observer = new MutationObserver(() => scanAndRecord());
  observer.observe(document.body, { childList: true, subtree: true });

  console.log('Ready! Click through stories normally. Videos will auto‑download after each story.');
})();