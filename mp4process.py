import os
import re
from pathlib import Path

import cv2
import numpy as np

# ----------------------------
# CONFIG
# ----------------------------
ROOT = Path("C:\\Users\\qacer\\Downloads\\new\\new")
IMAGE_OUT_DIR = ROOT / "_static_firstframes"

# How many frames to sample across each video
SAMPLE_COUNT = 30

# Thresholds (tune if needed)
# Lower => more likely to classify as "static-image video"
MEDIAN_DIFF_THR = 2.0        # mean abs diff of heavily blurred frames, scale ~0..255
MEAN_STD_THR = 1.8           # mean per-pixel std of heavily blurred frames, scale ~0..255

# If video is very short, reduce samples automatically
MIN_FRAMES_REQUIRED = 12

# Save images as:
IMAGE_EXT = ".jpg"
IMAGE_QUALITY = 92  # 0..100 (for jpg)

# Try EasyOCR if installed (better detection of "least text" frame)
USE_EASYOCR_IF_AVAILABLE = True


# ----------------------------
# OPTIONAL OCR
# ----------------------------
def maybe_make_ocr_reader():
    if not USE_EASYOCR_IF_AVAILABLE:
        return None
    try:
        import easyocr  # type: ignore
        # English-only is usually fine for lyric overlays; add languages if needed
        reader = easyocr.Reader(["en"], gpu=False)
        return reader
    except Exception:
        return None


OCR_READER = maybe_make_ocr_reader()


# ----------------------------
# HELPERS
# ----------------------------
def list_mp4_files(root: Path):
    return sorted([p for p in root.iterdir() if p.is_file() and p.suffix.lower() == ".mp4"])


def next_delete_name(root: Path, stem_prefix: str = "to be delete "):
    """
    Finds next available "to be delete ###.mp4" number in the folder.
    """
    pattern = re.compile(rf"^{re.escape(stem_prefix)}(\d+)$", re.IGNORECASE)

    max_n = 0
    for p in root.iterdir():
        if p.is_file() and p.suffix.lower() == ".mp4":
            m = pattern.match(p.stem)
            if m:
                try:
                    max_n = max(max_n, int(m.group(1)))
                except ValueError:
                    pass

    n = max_n + 1
    while True:
        candidate = root / f"{stem_prefix}{n:03d}.mp4"
        if not candidate.exists():
            return candidate
        n += 1


def sample_frame_indices(frame_count: int, sample_count: int):
    if frame_count <= 0:
        return []
    sample_count = min(sample_count, frame_count)
    # evenly spaced indices including both ends
    idxs = np.linspace(0, frame_count - 1, sample_count).astype(int)
    # ensure uniqueness and sorted
    return sorted(set(int(i) for i in idxs))


def read_frame_at(cap: cv2.VideoCapture, idx: int):
    cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
    ok, frame = cap.read()
    if not ok or frame is None:
        return None
    return frame


def preprocess_for_background_compare(frame_bgr: np.ndarray):
    """
    Reduce sensitivity to lyric overlays:
    - grayscale
    - downscale
    - heavy gaussian blur (wipes out text edges)
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (160, 90), interpolation=cv2.INTER_AREA)
    blurred = cv2.GaussianBlur(small, (21, 21), 0)
    return blurred.astype(np.float32)


def mean_abs_diff(a: np.ndarray, b: np.ndarray):
    return float(np.mean(np.abs(a - b)))


def compute_static_scores(sampled_bg_frames: list[np.ndarray]):
    """
    Returns:
      median_diff: median of mean abs diffs between consecutive sampled frames
      mean_std: mean per-pixel std across sampled frames
    """
    if len(sampled_bg_frames) < 2:
        return float("inf"), float("inf")

    diffs = []
    for i in range(1, len(sampled_bg_frames)):
        diffs.append(mean_abs_diff(sampled_bg_frames[i - 1], sampled_bg_frames[i]))
    median_diff = float(np.median(diffs))

    stack = np.stack(sampled_bg_frames, axis=0)  # (N,H,W)
    per_pixel_std = np.std(stack, axis=0)
    mean_std = float(np.mean(per_pixel_std))

    return median_diff, mean_std


def edge_overlay_score(frame_bgr: np.ndarray):
    """
    Heuristic "text/overlay amount" score:
    - looks at edge density primarily in bottom 40% and top 20% (common lyric areas)
    Lower is better (less overlay).
    """
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape[:2]

    # Regions likely to contain lyric overlays
    top = gray[0:int(0.20 * h), :]
    bottom = gray[int(0.60 * h):, :]

    def region_score(region):
        # mild blur to reduce noise, then edges
        r = cv2.GaussianBlur(region, (5, 5), 0)
        edges = cv2.Canny(r, 60, 140)
        return float(np.mean(edges > 0))  # proportion of edge pixels

    return region_score(top) + 2.0 * region_score(bottom)


def ocr_text_score(frame_bgr: np.ndarray, reader):
    """
    Uses OCR to estimate how much text is on screen.
    Lower is better.
    """
    if reader is None:
        return None
    try:
        # EasyOCR expects RGB
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        results = reader.readtext(rgb, detail=1, paragraph=False)
        # Score = sum of (bbox area * confidence) for detected text
        # (this heavily penalizes large lyric blocks)
        score = 0.0
        h, w = rgb.shape[:2]
        for bbox, text, conf in results:
            if not text:
                continue
            # bbox = [ [x1,y1],[x2,y2],[x3,y3],[x4,y4] ]
            xs = [p[0] for p in bbox]
            ys = [p[1] for p in bbox]
            area = max(0.0, (max(xs) - min(xs))) * max(0.0, (max(ys) - min(ys)))
            score += float(area) * float(conf)
        # normalize by frame area to make it resolution-independent
        return score / max(1.0, float(h * w))
    except Exception:
        return None


def choose_best_frame(frames_bgr: list[np.ndarray]):
    """
    Picks the frame with the least lyric/text overlay.
    Prefers OCR score if available; otherwise uses edge overlay heuristic.
    """
    best_i = 0
    best_score = float("inf")

    for i, fr in enumerate(frames_bgr):
        score = None
        if OCR_READER is not None:
            score = ocr_text_score(fr, OCR_READER)
        if score is None:
            score = edge_overlay_score(fr)

        if score < best_score:
            best_score = score
            best_i = i

    return best_i, best_score


def safe_imwrite_jpg(path: Path, frame_bgr: np.ndarray):
    path.parent.mkdir(parents=True, exist_ok=True)
    params = [int(cv2.IMWRITE_JPEG_QUALITY), int(IMAGE_QUALITY)]
    ok = cv2.imwrite(str(path), frame_bgr, params)
    return ok


def analyze_one_video(path: Path):
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        return {"status": "error", "reason": "cannot_open"}

    frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) or 0
    if frame_count < MIN_FRAMES_REQUIRED:
        # Still try, but with fewer samples
        pass

    idxs = sample_frame_indices(frame_count, SAMPLE_COUNT)
    if len(idxs) < 2:
        cap.release()
        return {"status": "error", "reason": "too_few_frames"}

    sampled_frames_bgr = []
    sampled_bg = []

    for idx in idxs:
        fr = read_frame_at(cap, idx)
        if fr is None:
            continue
        sampled_frames_bgr.append(fr)
        sampled_bg.append(preprocess_for_background_compare(fr))

    cap.release()

    if len(sampled_bg) < 2:
        return {"status": "error", "reason": "read_failed"}

    median_diff, mean_std = compute_static_scores(sampled_bg)

    is_static = (median_diff < MEDIAN_DIFF_THR) and (mean_std < MEAN_STD_THR)

    return {
        "status": "ok",
        "median_diff": median_diff,
        "mean_std": mean_std,
        "is_static": is_static,
        "sampled_frames_bgr": sampled_frames_bgr,
    }


def main():
    if not ROOT.exists():
        raise FileNotFoundError(f"Folder not found: {ROOT}")

    IMAGE_OUT_DIR.mkdir(parents=True, exist_ok=True)

    mp4s = list_mp4_files(ROOT)
    if not mp4s:
        print(f"No .mp4 files found in: {ROOT}")
        return

    print(f"Found {len(mp4s)} mp4 files in: {ROOT}")
    print(f"Saving extracted frames to: {IMAGE_OUT_DIR}")
    print("")

    renamed = 0
    kept = 0
    errors = 0

    for p in mp4s:
        print(f"---\nAnalyzing: {p.name}")
        result = analyze_one_video(p)

        if result["status"] != "ok":
            print(f"  [ERROR] {result.get('reason')}")
            errors += 1
            continue

        median_diff = result["median_diff"]
        mean_std = result["mean_std"]
        is_static = result["is_static"]

        print(f"  Scores: median_diff={median_diff:.3f}, mean_std={mean_std:.3f}")
        if not is_static:
            print("  => Looks like a real video. Leaving as-is.")
            kept += 1
            continue

        frames = result["sampled_frames_bgr"]
        best_i, best_score = choose_best_frame(frames)
        best_frame = frames[best_i]

        # Save image (name tied to original file)
        out_img = IMAGE_OUT_DIR / f"{p.stem}{IMAGE_EXT}"
        ok = safe_imwrite_jpg(out_img, best_frame)
        if ok:
            print(f"  Saved preview frame: {out_img.name} (overlay_score={best_score:.6f})")
        else:
            print("  [WARN] Failed to save preview frame.")

        # Rename mp4 to "to be delete ###.mp4"
        new_path = next_delete_name(ROOT)
        try:
            p.rename(new_path)
            print(f"  Renamed to: {new_path.name}")
            renamed += 1
        except Exception as e:
            print(f"  [ERROR] Rename failed: {e}")
            errors += 1

    print("\n=== Summary ===")
    print(f"Kept (real videos): {kept}")
    print(f"Renamed (static-image videos): {renamed}")
    print(f"Errors: {errors}")


if __name__ == "__main__":
    main()