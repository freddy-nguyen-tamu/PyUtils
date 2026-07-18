(async () => {
  "use strict";

  const PDF_TITLE = "Pirate Ship Conversation";
  const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

  function isDocumentScroller(element) {
    return (
      element === document.documentElement ||
      element === document.body ||
      element === document.scrollingElement
    );
  }

  function findScrollableArea(element) {
    let current = element;

    while (current && current !== document.body) {
      const style = getComputedStyle(current);
      const overflow = `${style.overflow} ${style.overflowY}`;

      if (
        /(auto|scroll|overlay)/i.test(overflow) &&
        current.scrollHeight > current.clientHeight + 50
      ) {
        return current;
      }

      current = current.parentElement;
    }

    return document.scrollingElement || document.documentElement;
  }

  function chooseConversationArea() {
    return new Promise((resolve) => {
      const message = document.createElement("div");

      message.textContent =
        "Click inside the scrollable Pirate Ship conversation area";

      Object.assign(message.style, {
        position: "fixed",
        top: "18px",
        left: "50%",
        transform: "translateX(-50%)",
        zIndex: "2147483647",
        padding: "13px 20px",
        borderRadius: "9px",
        background: "#111827",
        color: "#ffffff",
        font: "600 14px system-ui, sans-serif",
        boxShadow: "0 8px 30px rgba(0,0,0,.35)",
        pointerEvents: "none"
      });

      document.body.appendChild(message);

      const onClick = (event) => {
        event.preventDefault();
        event.stopPropagation();
        event.stopImmediatePropagation();

        document.removeEventListener("click", onClick, true);
        message.remove();

        resolve(event.target);
      };

      document.addEventListener("click", onClick, true);
    });
  }

  async function loadCompleteConversation(scroller) {
    const documentScroller = isDocumentScroller(scroller);

    const getTop = () =>
      documentScroller
        ? window.scrollY
        : scroller.scrollTop;

    const setTop = (position) => {
      if (documentScroller) {
        window.scrollTo(0, position);
      } else {
        scroller.scrollTop = position;
      }
    };

    const getHeight = () =>
      documentScroller
        ? Math.max(
            document.documentElement.scrollHeight,
            document.body.scrollHeight
          )
        : scroller.scrollHeight;

    const getViewportHeight = () =>
      documentScroller
        ? window.innerHeight
        : scroller.clientHeight;

    const originalTop = getTop();

    console.log("Loading older messages…");

    let previousHeight = 0;
    let unchangedCount = 0;

    // Repeatedly visit the top to trigger loading of older messages.
    for (let attempt = 0; attempt < 40; attempt += 1) {
      setTop(2);
      await wait(100);
      setTop(0);
      await wait(750);

      const currentHeight = getHeight();

      if (Math.abs(currentHeight - previousHeight) < 10) {
        unchangedCount += 1;
      } else {
        unchangedCount = 0;
        previousHeight = currentHeight;
      }

      if (unchangedCount >= 4) break;
    }

    console.log("Loading lazy images and message content…");

    let position = 0;
    let height = getHeight();
    let safetyCounter = 0;

    while (position < height && safetyCounter < 200) {
      setTop(position);
      await wait(180);

      position += Math.max(
        300,
        Math.floor(getViewportHeight() * 0.7)
      );

      height = getHeight();
      safetyCounter += 1;
    }

    setTop(0);
    await wait(500);

    // Wait briefly for visible images to finish loading.
    const images = Array.from(scroller.querySelectorAll?.("img") || []);

    await Promise.race([
      Promise.all(
        images.map(
          (image) =>
            new Promise((resolve) => {
              if (image.complete) {
                resolve();
                return;
              }

              image.addEventListener("load", resolve, { once: true });
              image.addEventListener("error", resolve, { once: true });
            })
        )
      ),
      wait(5000)
    ]);

    return () => setTop(originalTop);
  }

  const clickedElement = await chooseConversationArea();
  const conversation = findScrollableArea(clickedElement);

  console.log("Selected conversation element:", conversation);

  const restoreScroll = await loadCompleteConversation(conversation);

  const marker = `pirate-print-${Date.now()}`;
  const changedElements = new Map();
  const markedAncestors = [];

  function preserveStyle(element) {
    if (!changedElements.has(element)) {
      changedElements.set(element, element.getAttribute("style"));
    }
  }

  conversation.setAttribute("data-pirate-print-root", marker);

  // Mark ancestors so clipping and transformed containers do not interfere.
  let ancestor = conversation.parentElement;

  while (ancestor) {
    ancestor.setAttribute("data-pirate-print-ancestor", marker);
    markedAncestors.push(ancestor);
    ancestor = ancestor.parentElement;
  }

  const elements = [
    conversation,
    ...Array.from(conversation.querySelectorAll("*"))
  ];

  for (const element of elements) {
    const style = getComputedStyle(element);

    const clipsContent =
      element === conversation ||
      element.scrollHeight > element.clientHeight + 10 ||
      /(auto|scroll|hidden|clip)/i.test(
        `${style.overflow} ${style.overflowY}`
      );

    if (clipsContent) {
      preserveStyle(element);

      element.style.setProperty("height", "auto", "important");
      element.style.setProperty("max-height", "none", "important");
      element.style.setProperty("overflow", "visible", "important");
      element.style.setProperty("overflow-y", "visible", "important");
      element.style.setProperty("contain", "none", "important");
    }

    if (style.position === "sticky" || style.position === "fixed") {
      preserveStyle(element);

      element.style.setProperty("position", "static", "important");
      element.style.setProperty("inset", "auto", "important");
      element.style.setProperty("transform", "none", "important");
    }

    element.style.setProperty(
      "-webkit-print-color-adjust",
      "exact",
      "important"
    );
    element.style.setProperty(
      "print-color-adjust",
      "exact",
      "important"
    );
  }

  const printStyles = document.createElement("style");
  printStyles.id = marker;

  printStyles.textContent = `
    @page {
      size: A4;
      margin: 10mm;
    }

    @media print {
      html,
      body {
        width: 100% !important;
        min-height: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
        background: white !important;
      }

      body * {
        visibility: hidden !important;
      }

      [data-pirate-print-root="${marker}"],
      [data-pirate-print-root="${marker}"] * {
        visibility: visible !important;
      }

      [data-pirate-print-ancestor="${marker}"] {
        height: auto !important;
        max-height: none !important;
        overflow: visible !important;
        contain: none !important;
        clip: auto !important;
        transform: none !important;
      }

      [data-pirate-print-root="${marker}"] {
        position: absolute !important;
        top: 0 !important;
        left: 0 !important;
        width: 100% !important;
        height: auto !important;
        max-height: none !important;
        margin: 0 !important;
        padding: 0 !important;
        overflow: visible !important;
        contain: none !important;
        transform: none !important;
      }

      [data-pirate-print-root="${marker}"] * {
        animation: none !important;
        transition: none !important;
        caret-color: transparent !important;
        -webkit-print-color-adjust: exact !important;
        print-color-adjust: exact !important;
      }

      img,
      svg {
        max-width: 100% !important;
      }
    }
  `;

  document.head.appendChild(printStyles);

  const previousTitle = document.title;
  document.title = PDF_TITLE;

  let cleanedUp = false;

  function cleanup() {
    if (cleanedUp) return;
    cleanedUp = true;

    document.title = previousTitle;
    printStyles.remove();

    conversation.removeAttribute("data-pirate-print-root");

    for (const element of markedAncestors) {
      element.removeAttribute("data-pirate-print-ancestor");
    }

    for (const [element, originalStyle] of changedElements) {
      if (originalStyle === null) {
        element.removeAttribute("style");
      } else {
        element.setAttribute("style", originalStyle);
      }
    }

    restoreScroll();
    console.log("Page restored.");
  }

  window.addEventListener("afterprint", cleanup, { once: true });

  console.log("Opening the browser print dialog…");

  await wait(500);
  window.print();

  // Fallback for browsers that do not fire afterprint reliably.
  setTimeout(cleanup, 120000);
})();