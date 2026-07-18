from pathlib import Path
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

URL = "https://chatgpt.com/share/6a4f860c-e0bc-83ea-9c05-f44318fcd4fe"
OUTPUT_FILE = "chatgpt_conversation.txt"

MESSAGE_SELECTOR = "[data-message-author-role]"


def clean_text(text: str) -> str:
    lines = [line.rstrip() for line in text.splitlines()]
    lines = [line for line in lines if line.strip()]
    return "\n".join(lines)


def setup_scroll_helper(page):
    page.evaluate("""
        () => {
            window.__findChatScrollContainer = () => {
                const candidates = Array.from(document.querySelectorAll("*"))
                    .filter(el => {
                        const style = window.getComputedStyle(el);
                        const canScroll =
                            style.overflowY === "auto" ||
                            style.overflowY === "scroll" ||
                            style.overflow === "auto" ||
                            style.overflow === "scroll";

                        return canScroll && el.scrollHeight > el.clientHeight + 200;
                    })
                    .sort((a, b) => b.scrollHeight - a.scrollHeight);

                return candidates[0] || document.scrollingElement || document.documentElement;
            };

            window.__scrollInfo = () => {
                const el = window.__findChatScrollContainer();
                return {
                    tag: el.tagName,
                    className: el.className || "",
                    scrollTop: el.scrollTop,
                    scrollHeight: el.scrollHeight,
                    clientHeight: el.clientHeight
                };
            };

            window.__scrollToBottom = () => {
                const el = window.__findChatScrollContainer();
                el.scrollTop = el.scrollHeight;
            };

            window.__scrollToTop = () => {
                const el = window.__findChatScrollContainer();
                el.scrollTop = 0;
            };

            window.__slowScrollUp = async () => {
                const el = window.__findChatScrollContainer();
                const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

                let lastTop = -1;

                while (el.scrollTop > 0 && el.scrollTop !== lastTop) {
                    lastTop = el.scrollTop;
                    el.scrollTop = Math.max(0, el.scrollTop - Math.floor(el.clientHeight * 0.45));
                    await delay(700);
                }

                el.scrollTop = 0;
                await delay(2500);
            };

            window.__slowScrollDown = async () => {
                const el = window.__findChatScrollContainer();
                const delay = ms => new Promise(resolve => setTimeout(resolve, ms));

                let lastTop = -1;

                while (
                    el.scrollTop + el.clientHeight < el.scrollHeight &&
                    el.scrollTop !== lastTop
                ) {
                    lastTop = el.scrollTop;
                    el.scrollTop = Math.min(
                        el.scrollHeight,
                        el.scrollTop + Math.floor(el.clientHeight * 0.45)
                    );
                    await delay(700);
                }

                await delay(2500);
            };
        }
    """)


def get_conversation_snapshot(page):
    messages = page.locator(MESSAGE_SELECTOR)
    count = messages.count()

    combined_text = ""

    for i in range(count):
        try:
            combined_text += messages.nth(i).inner_text(timeout=5000)
        except Exception:
            pass

    return {
        "count": count,
        "length": len(combined_text),
        "head": combined_text[:3000],
        "tail": combined_text[-3000:],
    }


def wait_until_full_conversation_loaded(page, stable_rounds=10, max_rounds=120):
    setup_scroll_helper(page)

    print("Detected scroll container:")
    print(page.evaluate("window.__scrollInfo()"))

    print("Moving inside ChatGPT scroll container to bottom...")
    page.evaluate("window.__scrollToBottom()")
    page.wait_for_timeout(5000)

    last_snapshot = None
    stable_count = 0

    for round_num in range(max_rounds):
        print(f"\nRound {round_num + 1}: scrolling upward to load older messages...")

        before_info = page.evaluate("window.__scrollInfo()")
        print("Before scroll:", before_info)

        page.evaluate("window.__slowScrollUp()")
        page.wait_for_timeout(5000)

        after_info = page.evaluate("window.__scrollInfo()")
        print("After scroll:", after_info)

        snapshot = get_conversation_snapshot(page)

        print(
            f"Messages: {snapshot['count']}, "
            f"chars: {snapshot['length']}, "
            f"stable: {stable_count}/{stable_rounds}"
        )

        if snapshot == last_snapshot:
            stable_count += 1
        else:
            stable_count = 0

        if stable_count >= stable_rounds:
            print("\nConversation appears fully loaded.")
            return

        last_snapshot = snapshot

        # Move down again, then up again.
        # This helps trigger lazy loading / virtualized rendering.
        page.evaluate("window.__scrollToBottom()")
        page.wait_for_timeout(4000)

    print("\nWarning: max wait reached. Conversation may still be incomplete.")


def export_conversation(page):
    setup_scroll_helper(page)

    print("\nPreparing final export pass...")

    # Start from top.
    page.evaluate("window.__scrollToTop()")
    page.wait_for_timeout(4000)

    # Slowly scroll downward once so rendered text settles.
    page.evaluate("window.__slowScrollDown()")
    page.wait_for_timeout(4000)

    # Then return to top before reading messages in order.
    page.evaluate("window.__scrollToTop()")
    page.wait_for_timeout(3000)

    messages = page.locator(MESSAGE_SELECTOR)
    transcript_parts = []

    count = messages.count()
    print(f"Final rendered message count: {count}")

    for i in range(count):
        msg = messages.nth(i)

        role = msg.get_attribute("data-message-author-role") or "unknown"
        text = clean_text(msg.inner_text(timeout=15000))

        if text:
            transcript_parts.append(f"{role.upper()}:\n{text}")

    transcript = ("\n\n" + "=" * 80 + "\n\n").join(transcript_parts)

    Path(OUTPUT_FILE).write_text(transcript, encoding="utf-8")

    print(f"\nSaved {len(transcript_parts)} messages to {OUTPUT_FILE}")


def save_debug_files(page):
    Path("debug_page_text.txt").write_text(page.locator("body").inner_text(), encoding="utf-8")
    Path("debug_page_html.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="debug_screenshot.png", full_page=True)
    print("Saved debug_page_text.txt, debug_page_html.html, and debug_screenshot.png")


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,
            slow_mo=200
        )

        page = browser.new_page(
            viewport={"width": 1400, "height": 1800}
        )

        print("Opening shared ChatGPT conversation...")
        page.goto(URL, wait_until="domcontentloaded", timeout=60000)

        try:
            page.wait_for_selector(MESSAGE_SELECTOR, timeout=30000)
        except PlaywrightTimeoutError:
            save_debug_files(page)
            browser.close()
            raise RuntimeError(
                "Could not find conversation messages. "
                "The page may require login, or the page structure changed."
            )

        print("Loading full conversation by scrolling upward inside the real container...")
        wait_until_full_conversation_loaded(page)

        print("Exporting conversation...")
        export_conversation(page)

        save_debug_files(page)

        browser.close()


if __name__ == "__main__":
    main()