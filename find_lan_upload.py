import argparse
import re
import socket
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

PORT = 1234
TIMEOUT = 0.6
MAX_WORKERS = 100

PREFERRED_THIRDS = [24, 33]
DEFAULT_UPLOAD_DIR = r"C:\Users\qacer\Downloads\ytb\finished"


def generate_ips():
    seen = set()

    for third in PREFERRED_THIRDS:
        for fourth in range(10, 255):
            ip = f"10.0.{third}.{fourth}"
            seen.add(ip)
            yield ip

    for third in range(10, 100):
        if third in PREFERRED_THIRDS:
            continue
        for fourth in range(10, 255):
            ip = f"10.0.{third}.{fourth}"
            if ip not in seen:
                yield ip


def check_ip(ip):
    try:
        with socket.create_connection((ip, PORT), timeout=TIMEOUT):
            return f"http://{ip}:{PORT}/"
    except OSError:
        return None


def find_first_server():
    ip_iterator = iter(generate_ips())

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        pending = set()

        for _ in range(MAX_WORKERS):
            try:
                ip = next(ip_iterator)
            except StopIteration:
                break
            pending.add(executor.submit(check_ip, ip))

        while pending:
            done, pending = wait(pending, return_when=FIRST_COMPLETED)

            for future in done:
                result = future.result()

                if result:
                    executor.shutdown(wait=False, cancel_futures=True)
                    return result

                try:
                    ip = next(ip_iterator)
                    pending.add(executor.submit(check_ip, ip))
                except StopIteration:
                    pass

    return None


def collect_files(upload_dir, recursive=False):
    folder = Path(upload_dir)

    if not folder.exists():
        raise FileNotFoundError(f"Upload folder does not exist: {folder}")

    if not folder.is_dir():
        raise NotADirectoryError(f"Upload path is not a folder: {folder}")

    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = [str(path) for path in iterator if path.is_file()]

    if not files:
        raise FileNotFoundError(f"No files found in: {folder}")

    return files


def click_first_working(page, locators, timeout=5000):
    last_error = None

    for locator in locators:
        try:
            locator.first.click(timeout=timeout, force=True)
            return True
        except Exception as error:
            last_error = error

    if last_error:
        raise last_error

    return False


def save_debug(page, name_prefix="upload_debug"):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    html_path = Path.cwd() / f"{name_prefix}_{timestamp}.html"
    png_path = Path.cwd() / f"{name_prefix}_{timestamp}.png"

    try:
        html_path.write_text(page.content(), encoding="utf-8", errors="replace")
    except Exception:
        html_path = None

    try:
        page.screenshot(path=str(png_path), full_page=True)
    except Exception:
        png_path = None

    if html_path:
        print(f"Saved debug HTML: {html_path}")
    if png_path:
        print(f"Saved debug screenshot: {png_path}")


def attach_files(page, files):
    # Best path: after the upload menu opens, set the hidden input directly.
    file_input = page.locator('input[type="file"]').first

    try:
        file_input.wait_for(state="attached", timeout=8000)
        file_input.set_input_files(files)
        return
    except Exception:
        pass

    # Fallback: click the Add files menu item while Playwright intercepts
    # the file chooser, so the normal Windows picker will not interrupt you.
    add_files_locators = [
        page.get_by_role("menuitem", name=re.compile(r"Add\s+files", re.I)),
        page.get_by_role("button", name=re.compile(r"Add\s+files", re.I)),
        page.locator('button[aria-label="Add files"]'),
        page.locator('button:has-text("Add files")'),
        page.locator('[role="menuitem"]:has-text("Add files")'),
        page.get_by_text(re.compile(r"Add\s+files", re.I)),
    ]

    last_error = None
    for locator in add_files_locators:
        try:
            with page.expect_file_chooser(timeout=8000) as file_chooser_info:
                locator.first.click(timeout=8000, force=True)
            file_chooser_info.value.set_files(files)
            return
        except Exception as error:
            last_error = error

    raise RuntimeError(f"Could not find or use the Add files control: {last_error}")


def upload_files(url, files, headless=True):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("Playwright is not installed.")
        print("Run:")
        print("  pip install playwright")
        print("  python -m playwright install chromium")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=[
                "--disable-notifications",
                "--disable-popup-blocking",
                "--no-first-run",
            ],
        )

        context = browser.new_context(
            viewport={"width": 1280, "height": 900},
            accept_downloads=False,
        )
        page = context.new_page()
        page.set_default_timeout(30000)

        try:
            page.goto(url, wait_until="domcontentloaded")
            try:
                page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            upload_button_locators = [
                page.get_by_label(re.compile(r"Upload\s+file", re.I)),
                page.locator('button[aria-label="Upload file"]'),
                page.locator('button[aria-label*="Upload"]'),
                page.locator('button:has(mat-icon:text("file_upload"))'),
                page.locator('mat-icon:text("file_upload")').locator("xpath=ancestor::button[1]"),
            ]

            click_first_working(page, upload_button_locators)
            attach_files(page, files)

            start_upload = page.get_by_role(
                "button",
                name=re.compile(r"Start\s+upload", re.I),
            )
            start_upload.wait_for(state="visible", timeout=30000)
            start_upload.click(force=True)

            done_button = page.get_by_role(
                "button",
                name=re.compile(r"^\s*Done\s*$", re.I),
            )
            done_button.wait_for(state="visible", timeout=60 * 60 * 1000)

            try:
                done_button.click(timeout=5000, force=True)
            except Exception:
                pass

        except Exception:
            save_debug(page)
            raise
        finally:
            browser.close()


def main():
    parser = argparse.ArgumentParser(
        description="Find Android LAN file server and upload a local folder silently."
    )
    parser.add_argument(
        "--dir",
        default=DEFAULT_UPLOAD_DIR,
        help="Folder whose files should be uploaded.",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="Skip scanning and upload to this URL directly.",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="Show the browser window for debugging.",
    )
    parser.add_argument(
        "--recursive",
        action="store_true",
        help="Upload files inside subfolders too.",
    )
    args = parser.parse_args()

    files = collect_files(args.dir, recursive=args.recursive)

    url = args.url or find_first_server()
    if not url:
        print("No server found.")
        sys.exit(1)

    print(f"Found: {url}")
    print(f"Uploading {len(files)} file(s) from: {args.dir}")

    upload_files(
        url=url,
        files=files,
        headless=not args.show,
    )

    print("Upload finished.")


if __name__ == "__main__":
    main()
