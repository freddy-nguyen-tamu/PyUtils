import csv
import html
import re
import time
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests
from bs4 import BeautifulSoup
from tqdm import tqdm


OUT_DIR = Path("tower_of_god_character_images")
DELAY_SECONDS = 0.15

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
    ),
    "Accept": "application/json,text/html,*/*",
    "Referer": "https://towerofgod.fandom.com/",
}


SECTIONS = [
    {
        "name": "Regulars",
        "page": "List_of_Characters",
        "extractor": "regulars",
    },
    {
        "name": "Rankers",
        "page": "List_of_Characters/Rankers",
        "extractor": "rankers",
    },
    {
        "name": "Zahard Empire",
        "page": "List_of_Characters/Zahard_Empire",
        "extractor": "zahard_empire",
    },
    {
        "name": "Occupations",
        "page": "List_of_Characters/Occupations",
        "extractor": "occupations",
    },
    {
        "name": "Organisations",
        "page": "List_of_Characters/Organisations",
        "extractor": "organisations",
    },
    {
        "name": "Special Groups",
        "page": "List_of_Characters/Special_Groups",
        "extractor": "special_groups",
    },
    {
        "name": "Urek's Spin Off",
        "page": "List_of_Characters/Urek's_Spin_Off",
        "extractor": "urek_spin_off",
    },
]


def safe_filename(text: str, max_len: int = 140) -> str:
    text = html.unescape(unquote(text or "")).strip()
    text = re.sub(r"[\\/:*?\"<>|]", "_", text)
    text = re.sub(r"\s+", " ", text)
    text = text.strip(" ._")
    return text[:max_len] or "unknown"


def clean_name(text: str) -> str:
    text = html.unescape(unquote(text or "")).strip()
    text = re.sub(r"\.(png|jpg|jpeg|webp|gif)$", "", text, flags=re.I)
    text = text.replace("_", " ")
    text = text.replace("-Profile", "")
    text = text.replace(" Profile", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def normalize_url(url: str) -> str:
    url = html.unescape(url or "").replace("&amp;", "&").strip()

    if url.startswith("//"):
        url = "https:" + url

    return url


def extension_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    match = re.search(r"\.(png|jpg|jpeg|webp|gif)", path, re.I)

    if not match:
        return ".jpg"

    ext = match.group(1).lower()

    if ext == "jpeg":
        return ".jpg"

    return "." + ext


def guess_name_from_url(url: str) -> str:
    path = unquote(urlparse(url).path)

    for part in reversed(path.split("/")):
        if re.search(r"\.(png|jpg|jpeg|webp|gif)$", part, re.I):
            return clean_name(part)

    return "unknown"


def is_profile_image(url: str) -> bool:
    low = url.lower()

    if "static.wikia.nocookie.net/towerofgod/images" not in low:
        return False

    if "-profile" not in low:
        return False

    bad = [
        "character_page",
        "site-logo",
        "site-background",
        "favicon",
        "placeholder",
        "semi-transparent",
    ]

    return not any(x in low for x in bad)


def get_page_html_from_api(page_name: str) -> str:
    url = "https://towerofgod.fandom.com/api.php"

    params = {
        "action": "parse",
        "page": page_name,
        "prop": "text",
        "format": "json",
        "disablelimitreport": "1",
        "disableeditsection": "1",
    }

    response = requests.get(url, params=params, headers=HEADERS, timeout=60)

    if response.status_code != 200:
        raise RuntimeError(f"API failed for {page_name}: HTTP {response.status_code}")

    data = response.json()

    if "error" in data:
        raise RuntimeError(f"API error for {page_name}: {data['error']}")

    return data["parse"]["text"]["*"]


def extract_thumb_figures(source: str, section_name: str) -> list[dict]:
    soup = BeautifulSoup(source, "html.parser")

    items = []
    seen = set()

    for fig in soup.select("figure.thumb"):
        img = fig.find("img")

        if not img:
            continue

        image_url = normalize_url(img.get("data-src") or img.get("src") or "")

        if not is_profile_image(image_url):
            continue

        name = (
            img.get("alt")
            or img.get("data-image-name")
            or img.get("data-image-key")
            or ""
        )

        name = clean_name(name)

        if not name:
            caption = fig.find("figcaption")
            if caption:
                bold = caption.find("b")
                if bold:
                    name = clean_name(bold.get_text(" ", strip=True))
                else:
                    name = clean_name(caption.get_text(" ", strip=True))

        if not name:
            name = guess_name_from_url(image_url)

        if image_url in seen:
            continue

        seen.add(image_url)

        items.append({
            "section": section_name,
            "name": name,
            "image_url": image_url,
            "character_url": "",
        })

    return items


def extract_img_tags_direct(source: str, section_name: str) -> list[dict]:
    soup = BeautifulSoup(source, "html.parser")

    items = []
    seen = set()

    for img in soup.find_all("img"):
        image_url = normalize_url(img.get("data-src") or img.get("src") or "")

        if not is_profile_image(image_url):
            continue

        raw_name = (
            img.get("alt")
            or img.get("data-image-name")
            or img.get("data-image-key")
            or ""
        )

        name = clean_name(raw_name) or guess_name_from_url(image_url)

        if image_url in seen:
            continue

        seen.add(image_url)

        items.append({
            "section": section_name,
            "name": name,
            "image_url": image_url,
            "character_url": "",
        })

    return items


def extract_regex_direct(source: str, section_name: str) -> list[dict]:
    items = []
    seen = set()

    pattern = re.compile(
        r'<img\b[^>]*?(?:alt="(?P<alt>[^"]*)")?[^>]*?'
        r'(?:data-image-name="(?P<data_name>[^"]*)")?[^>]*?'
        r'data-src="(?P<src>https://static\.wikia\.nocookie\.net/towerofgod/images/[^"]*?-Profile[^"]*)"',
        re.I | re.S,
    )

    for match in pattern.finditer(source):
        image_url = normalize_url(match.group("src"))

        if not is_profile_image(image_url):
            continue

        name = clean_name(match.group("alt") or match.group("data_name") or "")

        if not name:
            name = guess_name_from_url(image_url)

        if image_url in seen:
            continue

        seen.add(image_url)

        items.append({
            "section": section_name,
            "name": name,
            "image_url": image_url,
            "character_url": "",
        })

    return items


def merge_unique(*groups: list[dict]) -> list[dict]:
    merged = []
    seen = set()

    for group in groups:
        for item in group:
            if item["image_url"] in seen:
                continue

            seen.add(item["image_url"])
            merged.append(item)

    return merged


def scrape_regulars(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Regulars"),
        extract_img_tags_direct(source, "Regulars"),
        extract_regex_direct(source, "Regulars"),
    )


def scrape_rankers(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Rankers"),
        extract_img_tags_direct(source, "Rankers"),
        extract_regex_direct(source, "Rankers"),
    )


def scrape_zahard_empire(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Zahard Empire"),
        extract_img_tags_direct(source, "Zahard Empire"),
        extract_regex_direct(source, "Zahard Empire"),
    )


def scrape_occupations(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Occupations"),
        extract_img_tags_direct(source, "Occupations"),
        extract_regex_direct(source, "Occupations"),
    )


def scrape_organisations(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Organisations"),
        extract_img_tags_direct(source, "Organisations"),
        extract_regex_direct(source, "Organisations"),
    )


def scrape_special_groups(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Special Groups"),
        extract_img_tags_direct(source, "Special Groups"),
        extract_regex_direct(source, "Special Groups"),
    )


def scrape_urek_spin_off(source: str) -> list[dict]:
    return merge_unique(
        extract_thumb_figures(source, "Urek's Spin Off"),
        extract_img_tags_direct(source, "Urek's Spin Off"),
        extract_regex_direct(source, "Urek's Spin Off"),
    )


EXTRACTORS = {
    "regulars": scrape_regulars,
    "rankers": scrape_rankers,
    "zahard_empire": scrape_zahard_empire,
    "occupations": scrape_occupations,
    "organisations": scrape_organisations,
    "special_groups": scrape_special_groups,
    "urek_spin_off": scrape_urek_spin_off,
}


def download_image(url: str, path: Path) -> str:
    if path.exists() and path.stat().st_size > 0:
        return "skipped already exists"

    path.parent.mkdir(parents=True, exist_ok=True)

    response = requests.get(url, headers=HEADERS, timeout=60)

    if response.status_code != 200:
        return f"failed HTTP {response.status_code}"

    content_type = response.headers.get("content-type", "").lower()

    if "image" not in content_type and len(response.content) < 500:
        return f"failed non-image response: {content_type}"

    path.write_bytes(response.content)

    return "downloaded"


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    all_items = []
    seen_global = set()

    debug_dir = OUT_DIR / "_debug_html"
    debug_dir.mkdir(parents=True, exist_ok=True)

    for section in SECTIONS:
        name = section["name"]
        page = section["page"]
        extractor_name = section["extractor"]
        extractor = EXTRACTORS[extractor_name]

        print(f"\nFetching {name}: {page}")

        try:
            source = get_page_html_from_api(page)
        except Exception as e:
            print(f"FAILED fetching {name}: {e}")
            continue

        debug_file = debug_dir / f"{safe_filename(name)}.html"
        debug_file.write_text(source, encoding="utf-8", errors="ignore")

        items = extractor(source)

        print(f"{name}: found {len(items)} profile images")

        for item in items:
            if item["image_url"] in seen_global:
                continue

            seen_global.add(item["image_url"])
            all_items.append(item)

        time.sleep(DELAY_SECONDS)

    print(f"\nTotal unique profile images: {len(all_items)}")

    manifest_path = OUT_DIR / "manifest.csv"

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "section",
                "name",
                "filename",
                "image_url",
                "character_url",
                "status",
            ],
        )

        writer.writeheader()

        for item in tqdm(all_items, desc="Downloading"):
            section_folder = OUT_DIR / safe_filename(item["section"])
            section_folder.mkdir(parents=True, exist_ok=True)

            ext = extension_from_url(item["image_url"])
            filename = f"{safe_filename(item['name'])}{ext}"
            file_path = section_folder / filename

            try:
                status = download_image(item["image_url"], file_path)
            except Exception as e:
                status = f"failed {type(e).__name__}: {e}"

            writer.writerow({
                "section": item["section"],
                "name": item["name"],
                "filename": str(file_path),
                "image_url": item["image_url"],
                "character_url": item["character_url"],
                "status": status,
            })

            time.sleep(DELAY_SECONDS)

    print("\nDone.")
    print(f"Images saved in: {OUT_DIR.resolve()}")
    print(f"Manifest saved at: {manifest_path.resolve()}")
    print(f"Debug HTML saved in: {debug_dir.resolve()}")


if __name__ == "__main__":
    main()