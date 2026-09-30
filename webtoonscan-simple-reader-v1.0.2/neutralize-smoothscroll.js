(() => {
  'use strict';

  // WebtoonScan's Madara theme loads smoothscroll.js, which replaces native
  // wheel scrolling with an animated queue and calls preventDefault().
  // Intercept its exported global at document_start and immediately invoke
  // its own cleanup method as soon as the library finishes loading.
  // This runs in the page's MAIN world, not the extension's isolated world.
  try {
    if (typeof window.SmoothScroll === 'function') {
      window.SmoothScroll.destroy?.();
      return;
    }

    let capturedValue;

    Object.defineProperty(window, 'SmoothScroll', {
      configurable: true,
      enumerable: true,
      get() {
        return capturedValue;
      },
      set(value) {
        capturedValue = value;

        // Restore a normal writable property immediately so other page code
        // can still read or replace SmoothScroll without our accessor staying
        // in the way.
        Object.defineProperty(window, 'SmoothScroll', {
          configurable: true,
          enumerable: true,
          writable: true,
          value
        });

        try {
          value?.destroy?.();
        } catch {
          // Reader operation does not depend on this cleanup succeeding.
        }
      }
    });

    // Fallback in case the theme supplied the global in an unusual order.
    window.addEventListener('load', () => {
      try {
        window.SmoothScroll?.destroy?.();
      } catch {}
    }, { once: true });
  } catch {
    // Never block the page if a browser disallows redefining the property.
  }
})();
