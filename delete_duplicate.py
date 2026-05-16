import os
import argparse
from dataclasses import dataclass
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor

from PIL import Image, ImageFile
import imagehash
import numpy as np

ImageFile.LOAD_TRUNCATED_IMAGES = True

SUPPORTED_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp",
    ".bmp", ".tif", ".tiff", ".heic", ".heif"
}


@dataclass
class ImgInfo:
    path: str
    width: int
    height: int
    pixels: int
    file_size: int
    sharpness: float
    phash: str


def is_image_file(path: str) -> bool:
    return os.path.splitext(path.lower())[1] in SUPPORTED_EXTS


def load_image_info(args) -> ImgInfo | None:
    """Top-level function so multiprocessing works on Windows"""
    path, hash_size = args
    try:
        file_size = os.path.getsize(path)

        with Image.open(path) as im:
            im = im.convert("RGB")
            width, height = im.size
            pixels = width * height

            ph = imagehash.phash(im, hash_size=hash_size)
            phash_str = str(ph)

            gray = np.asarray(im.convert("L"))
            sharpness = float(gray.var())

        return ImgInfo(
            path=path,
            width=width,
            height=height,
            pixels=pixels,
            file_size=file_size,
            sharpness=sharpness,
            phash=phash_str,
        )
    except Exception as e:
        print(f"[WARN] Skipping unreadable image: {path}\n       Reason: {e}")
        return None


def quality_score(info: ImgInfo) -> tuple:
    return (info.pixels, info.file_size, info.sharpness)


def find_images(folder: str, recursive: bool) -> list[str]:
    if recursive:
        paths = []
        for root, _, files in os.walk(folder):
            for f in files:
                p = os.path.join(root, f)
                if is_image_file(p):
                    paths.append(p)
        return paths
    else:
        return [
            os.path.join(folder, f)
            for f in os.listdir(folder)
            if os.path.isfile(os.path.join(folder, f)) and is_image_file(f)
        ]


def main():
    parser = argparse.ArgumentParser(
        description="Delete duplicate images and keep highest-quality version."
    )
    parser.add_argument("folder", help="Folder containing images")
    parser.add_argument("--recursive", action="store_true")
    parser.add_argument("--threshold", type=int, default=5)
    parser.add_argument("--hash-size", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    folder = os.path.abspath(args.folder)
    if not os.path.isdir(folder):
        raise SystemExit(f"Not a folder: {folder}")

    img_paths = find_images(folder, args.recursive)
    print(f"[INFO] Found {len(img_paths)} image(s). Scanning...")

    # Windows-safe multiprocessing
    work = [(p, args.hash_size) for p in img_paths]
    with ProcessPoolExecutor() as exe:
        infos = list(filter(None, exe.map(load_image_info, work)))

    print(f"[INFO] Loaded {len(infos)} image(s). Grouping by similarity...")

    buckets = defaultdict(list)
    for info in infos:
        buckets[info.phash[:4]].append(info)

    clusters = []
    used = set()

    for bucket in buckets.values():
        hash_objs = [(i, imagehash.hex_to_hash(i.phash)) for i in bucket]

        for idx, (info_i, h_i) in enumerate(hash_objs):
            if info_i.path in used:
                continue

            cluster = [info_i]
            used.add(info_i.path)

            for info_j, h_j in hash_objs[idx + 1:]:
                if info_j.path in used:
                    continue
                if (h_i - h_j) <= args.threshold:
                    cluster.append(info_j)
                    used.add(info_j.path)

            clusters.append(cluster)

    dup_clusters = [c for c in clusters if len(c) > 1]
    print(f"[INFO] Found {len(dup_clusters)} duplicate cluster(s).")

    deleted = 0
    kept = 0

    for i, cluster in enumerate(dup_clusters, 1):
        best = max(cluster, key=quality_score)
        kept += 1

        to_delete = [x for x in cluster if x.path != best.path]

        print(f"\n[CLUSTER {i}] Keeping:")
        print(f"  KEEP  {best.path}")
        print(f"        {best.width}x{best.height}, {best.file_size} bytes")

        for x in sorted(to_delete, key=quality_score, reverse=True):
            print(f"  DEL   {x.path}")

        if args.dry_run:
            deleted += len(to_delete)
        else:
            for x in to_delete:
                try:
                    os.remove(x.path)
                    deleted += 1
                except Exception as e:
                    print(f"[ERROR] Failed to delete {x.path}: {e}")

    if args.dry_run:
        print(f"\n[DRY RUN] Would delete {deleted} file(s).")
    else:
        print(f"\n[DONE] Deleted {deleted} file(s). Kept {kept} best images.")


if __name__ == "__main__":
    main()
    # python delete_duplicate.py "C:\Users\qacer\Downloads\!py\downloads" --dry-run
    # python delete_duplicate.py "C:\Users\qacer\Downloads\!py\downloads" --threshold 5
    # python delete_duplicate.py "C:\Users\qacer\Downloads\!py\downloads" --recursive --dry-run