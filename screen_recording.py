(async () => {
  const stream = await navigator.mediaDevices.getDisplayMedia({
    video: {
      frameRate: 60
    },
    audio: true,
    preferCurrentTab: true
  });

  const mimeTypes = [
    "video/webm;codecs=vp9,opus",
    "video/webm;codecs=vp8,opus",
    "video/webm"
  ];

  const mimeType =
    mimeTypes.find(t => MediaRecorder.isTypeSupported(t)) || "";

  const recorder = new MediaRecorder(
    stream,
    mimeType ? { mimeType, videoBitsPerSecond: 10000000 } : {}
  );

  const chunks = [];

  recorder.ondataavailable = e => {
    if (e.data.size > 0) chunks.push(e.data);
  };

  recorder.onstop = () => {
    const blob = new Blob(chunks, {
      type: recorder.mimeType || "video/webm"
    });

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `facebook-video-${Date.now()}.webm`;
    document.body.appendChild(a);
    a.click();
    a.remove();

    setTimeout(() => URL.revokeObjectURL(url), 10000);

    console.log("Recording downloaded.");
  };

  stream.getVideoTracks()[0].addEventListener("ended", () => {
    if (recorder.state !== "inactive") recorder.stop();
  });

  recorder.start(1000);

  window.stopFacebookRecording = () => {
    if (recorder.state !== "inactive") recorder.stop();
    stream.getTracks().forEach(t => t.stop());
  };

  console.log(`
RECORDING STARTED

1. Choose "This Tab"
2. Enable "Also share tab audio"
3. Play the video
4. When finished run:

stopFacebookRecording()

The recording will download automatically.
`);
})();