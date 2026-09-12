from __future__ import annotations

import os
import sys
import time
import math
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import numpy as np
    from PIL import Image, ImageTk, ImageOps
except ImportError as exc:
    raise SystemExit("Missing dependency. Run: py -m pip install pillow numpy send2trash") from exc

try:
    from send2trash import send2trash
except ImportError as exc:
    raise SystemExit("Missing dependency. Run: py -m pip install send2trash") from exc

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

Image.init()
SUPPORTED_EXTS = {ext.lower() for ext in Image.registered_extensions()}
SUPPORTED_EXTS.update({".heic", ".heif"})

THUMB_SIZE = (230, 170)
PREVIEW_MAX_SIDE = 1100
CROP_THUMB = (175, 130)
Box = tuple[int, int, int, int]


@dataclass
class GridDetection:
    boxes: list[Box]
    confidence: float
    score: float
    reason: str
    mode: str = "asymmetric"
    seams: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    path: Path
    detection: GridDetection
    selected: bool = True
    thumb: Image.Image | None = None
    tk_thumb: ImageTk.PhotoImage | None = None


@dataclass
class ReviewItem:
    candidate: Candidate
    boxes: list[Box] = field(default_factory=list)
    approved: bool = True


@dataclass(frozen=True)
class ScanRequest:
    folder: Path
    threshold: float
    recursive: bool


# ------------------------------ image helpers ------------------------------

def open_image(path: Path) -> Image.Image:
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        return im.copy()


def preview_copy(im: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    out = im.copy()
    out.thumbnail(max_size, Image.Resampling.LANCZOS)
    return out


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 2
    while True:
        p = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not p.exists():
            return p
        i += 1


def safe_output_extension(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        return ext
    return ".png"


def save_crop_atomic(image: Image.Image, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_path.with_name(f".{final_path.stem}.tmp{final_path.suffix}")
    params = {}
    out = image
    ext = final_path.suffix.lower()
    if ext in {".jpg", ".jpeg"}:
        if out.mode == "RGBA":
            bg = Image.new("RGB", out.size, "white")
            bg.paste(out, mask=out.getchannel("A"))
            out = bg
        else:
            out = out.convert("RGB")
        params.update(quality=96, subsampling=0, optimize=True)
    elif ext == ".png":
        params.update(optimize=True)
    out.save(tmp, **params)
    os.replace(tmp, final_path)


# ------------------------------ advanced detector ------------------------------

def _resample_rgb(im: Image.Image, max_side: int = PREVIEW_MAX_SIDE) -> tuple[np.ndarray, float, float]:
    rgb = im.convert("RGB")
    w0, h0 = rgb.size
    scale = min(1.0, max_side / max(w0, h0))
    if scale < 1.0:
        rgb = rgb.resize((max(2, round(w0 * scale)), max(2, round(h0 * scale))), Image.Resampling.LANCZOS)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    return arr, w0 / arr.shape[1], h0 / arr.shape[0]


def _robust_z(profile: np.ndarray) -> np.ndarray:
    if len(profile) == 0:
        return profile
    med = float(np.median(profile))
    mad = float(np.median(np.abs(profile - med))) + 1e-6
    return (profile - med) / (1.4826 * mad)


def _profile_for_region(region: np.ndarray, axis: str) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return seam strength, robust-z and continuity for every internal boundary."""
    if axis == "v":
        diff = np.mean(np.abs(region[:, 1:, :] - region[:, :-1, :]), axis=2)
        profile = np.percentile(diff, 68, axis=0)
        local_base = np.median(diff, axis=1, keepdims=True) + 1e-5
        # Require a visible absolute jump too. Without this, a nearly perfectly
        # smooth image can produce enormous ratios against a ~zero baseline.
        continuity = np.mean((diff > np.maximum(local_base * 1.55, 0.008)) , axis=0)
    else:
        diff = np.mean(np.abs(region[1:, :, :] - region[:-1, :, :]), axis=2)
        profile = np.percentile(diff, 68, axis=1)
        local_base = np.median(diff, axis=0, keepdims=True) + 1e-5
        continuity = np.mean((diff > np.maximum(local_base * 1.55, 0.008)) , axis=1)
    return profile.astype(np.float32), _robust_z(profile).astype(np.float32), continuity.astype(np.float32)


def _best_split(arr: np.ndarray, box: Box, depth: int) -> tuple[str, int, float, dict] | None:
    x0, y0, x1, y1 = box
    region = arr[y0:y1, x0:x1]
    h, w = region.shape[:2]
    if w < 110 or h < 110:
        return None

    options: list[tuple[str, int, float, dict]] = []
    for axis, length in (("v", w), ("h", h)):
        # Do not create tiny fragments. Unequal layouts are allowed, but a child must
        # still be large enough to plausibly be a photo tile.
        min_child = max(44, int(length * 0.10))
        if length < min_child * 2 + 2:
            continue
        prof, z, cont = _profile_for_region(region, axis)
        if len(prof) < 3:
            continue
        med = float(np.median(prof)) + 1e-6
        lo = min_child - 1
        hi = len(prof) - min_child + 1
        for i in range(lo, hi):
            raw = float(prof[i])
            ratio = raw / med
            zi = float(z[i])
            ci = float(cont[i])

            # Add a local contrast check across small windows. This helps distinguish
            # true photo-to-photo boundaries from one narrow object edge.
            c = i + 1
            win = max(2, min(8, length // 80))
            if axis == "v":
                if c - win < 0 or c + win > w:
                    continue
                a = region[:, c-win:c, :].mean(axis=1)
                b = region[:, c:c+win, :].mean(axis=1)
                local_jump = np.mean(np.abs(a - b), axis=1)
            else:
                if c - win < 0 or c + win > h:
                    continue
                a = region[c-win:c, :, :].mean(axis=0)
                b = region[c:c+win, :, :].mean(axis=0)
                local_jump = np.mean(np.abs(a - b), axis=1)
            lj_med = float(np.median(local_jump))
            lj_p70 = float(np.percentile(local_jump, 70))
            broad = min(2.0, lj_p70 / (lj_med + 1e-5))

            # Central seams get only a tiny preference. We explicitly do NOT assume
            # equal sizes; 20/80, 35/65, etc. remain viable.
            frac = c / length
            edge_penalty = 0.0 if 0.14 <= frac <= 0.86 else 0.35
            score = (
                0.95 * min(10.0, max(0.0, zi))
                + 2.2 * max(0.0, ratio - 1.0)
                + 3.0 * ci
                + 0.65 * max(0.0, broad - 1.0)
                - edge_penalty
                - 0.18 * depth
            )
            options.append((axis, c, score, {"z": zi, "ratio": ratio, "continuity": ci, "broad": broad, "raw": raw}))

    if not options:
        return None
    best = max(options, key=lambda t: t[2])
    axis, c, score, detail = best

    # A deeper recursive split must be a little stronger so normal internal edges
    # inside an already-isolated photograph are less likely to be over-segmented.
    required = 5.25 + depth * 0.35
    if detail["raw"] < 0.008 or detail["z"] < 2.25 or detail["ratio"] < 1.12 or detail["continuity"] < 0.20 or score < required:
        return None
    return best


def _recursive_partition(
    arr: np.ndarray,
    box: Box,
    depth: int,
    max_depth: int,
    max_pieces: int,
    seam_log: list[str],
    seam_scores: list[float],
) -> list[Box]:
    if depth >= max_depth:
        return [box]
    split = _best_split(arr, box, depth)
    if split is None:
        return [box]
    axis, c, score, detail = split
    x0, y0, x1, y1 = box
    if axis == "v":
        cut = x0 + c
        a, b = (x0, y0, cut, y1), (cut, y0, x1, y1)
        seam_log.append(f"V@{cut} score={score:.1f}")
    else:
        cut = y0 + c
        a, b = (x0, y0, x1, cut), (x0, cut, x1, y1)
        seam_log.append(f"H@{cut} score={score:.1f}")
    seam_scores.append(score)

    # Recursively analyze both sides. Cap total complexity afterward.
    left = _recursive_partition(arr, a, depth + 1, max_depth, max_pieces, seam_log, seam_scores)
    right = _recursive_partition(arr, b, depth + 1, max_depth, max_pieces, seam_log, seam_scores)
    pieces = left + right
    if len(pieces) > max_pieces:
        return [box]
    return pieces


def _regular_grid_hint(arr: np.ndarray, max_parts: int = 5) -> tuple[list[Box], float] | None:
    """Conservative regular-grid fallback, useful when several equal seams reinforce each other."""
    h, w = arr.shape[:2]
    vprof, vz, vcont = _profile_for_region(arr, "v")
    hprof, hz, hcont = _profile_for_region(arr, "h")

    def axis_parts(prof, z, cont, length):
        med = float(np.median(prof)) + 1e-6
        best = (1, [], 0.0)
        for parts in range(2, max_parts + 1):
            if length / parts < 60:
                continue
            cuts, vals = [], []
            radius = max(4, int(length / parts * 0.10))
            ok = True
            for k in range(1, parts):
                target = int(round(length * k / parts))
                lo, hi = max(1, target-radius), min(length-1, target+radius)
                sub = z[lo-1:hi]
                if len(sub) == 0:
                    ok = False; break
                j = int(np.argmax(sub))
                c = lo + j
                ratio = float(prof[c-1]) / med
                zz = float(sub[j])
                if float(prof[c-1]) < 0.008 or float(cont[c-1]) < 0.20 or zz < 2.4 or ratio < 1.14:
                    ok = False; break
                cuts.append(c); vals.append(zz + 2.0*(ratio-1.0))
            if ok and vals:
                score = min(vals) + 0.15 * len(vals)
                if score > best[2]:
                    best = (parts, cuts, score)
        return best

    cols, xs, xscr = axis_parts(vprof, vz, vcont, w)
    rows, ys, yscr = axis_parts(hprof, hz, hcont, h)
    if rows == 1 and cols == 1:
        return None
    X = [0] + xs + [w]
    Y = [0] + ys + [h]
    boxes = [(X[c], Y[r], X[c+1], Y[r+1]) for r in range(len(Y)-1) for c in range(len(X)-1)]
    score = xscr + yscr + (0.8 if rows > 1 and cols > 1 else 0)
    return boxes, score


def _dedupe_and_validate_boxes(boxes: list[Box], w: int, h: int) -> list[Box]:
    out: list[Box] = []
    for b in boxes:
        x0, y0, x1, y1 = map(int, b)
        x0, x1 = max(0, x0), min(w, x1)
        y0, y1 = max(0, y0), min(h, y1)
        if x1 - x0 >= 8 and y1 - y0 >= 8:
            out.append((x0, y0, x1, y1))
    # Stable reading order.
    out = list(dict.fromkeys(out))
    out.sort(key=lambda b: (b[1], b[0], b[3]-b[1], b[2]-b[0]))
    return out


def detect_photo_grid(im: Image.Image, max_depth: int = 4, max_pieces: int = 12) -> GridDetection | None:
    arr, sx, sy = _resample_rgb(im)
    h, w = arr.shape[:2]
    if w < 120 or h < 120:
        return None

    seam_log: list[str] = []
    seam_scores: list[float] = []
    recursive_boxes = _recursive_partition(arr, (0, 0, w, h), 0, max_depth, max_pieces, seam_log, seam_scores)
    regular = _regular_grid_hint(arr)

    mode = "asymmetric"
    boxes = recursive_boxes
    score = float(np.mean(seam_scores)) if seam_scores else 0.0

    # If the recursive detector saw nothing but the regular detector has multiple
    # mutually reinforcing separators, use the regular model. If both saw grids,
    # prefer the model with more supported pieces unless recursion is much weaker.
    if regular is not None:
        rboxes, rscore = regular
        if len(boxes) < 2 or (len(rboxes) > len(boxes) and rscore >= score - 0.8):
            boxes = rboxes
            score = rscore
            mode = "regular"
            seam_log = [f"regular {len(rboxes)} pieces"]

    if len(boxes) < 2:
        return None

    full_boxes = [
        (int(round(x0*sx)), int(round(y0*sy)), int(round(x1*sx)), int(round(y1*sy)))
        for x0, y0, x1, y1 in boxes
    ]
    full_boxes = _dedupe_and_validate_boxes(full_boxes, im.width, im.height)
    if len(full_boxes) < 2:
        return None

    # Confidence: seam strength + multiple-piece reinforcement. Two-piece collages
    # remain deliberately more conservative because a single natural edge can mimic one.
    base = 1.0 / (1.0 + math.exp(-(score - 5.5) / 1.5))
    if len(full_boxes) == 2:
        confidence = base * 0.78
    else:
        confidence = min(0.99, base + min(0.20, 0.045 * (len(full_boxes)-2)))

    unequal = len({(b[2]-b[0], b[3]-b[1]) for b in full_boxes}) > 1
    reason = f"{mode} layout, {len(full_boxes)} pieces"
    if unequal:
        reason += "; unequal/asymmetric tile sizes"
    if seam_log:
        reason += "; " + ", ".join(seam_log[:4])
        if len(seam_log) > 4:
            reason += f" +{len(seam_log)-4} more"
    return GridDetection(full_boxes, float(confidence), float(score), reason, mode, seam_log)


# ------------------------------ console scanner ------------------------------

def ensure_console() -> None:
    if os.name != "nt" or (sys.stdout is not None and sys.stderr is not None):
        return
    try:
        import ctypes
        ctypes.windll.kernel32.AllocConsole()
        sys.stdout = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stderr = open("CONOUT$", "w", encoding="utf-8", buffering=1)
        sys.stdin = open("CONIN$", "r", encoding="utf-8")
    except Exception:
        pass


def collect_image_paths(root: Path, recursive: bool) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.glob("*")
    paths = [p for p in iterator if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS]
    paths.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))
    return paths


def scan_folder_terminal(request: ScanRequest) -> tuple[list[Candidate], str]:
    ensure_console()
    paths = collect_image_paths(request.folder, request.recursive)
    total = len(paths)
    candidates: list[Candidate] = []
    errors = 0
    started = time.perf_counter()

    print("\n" + "="*82, flush=True)
    print("ADVANCED PHOTO GRID DETECTOR - TERMINAL SCAN", flush=True)
    print(f"Folder: {request.folder}", flush=True)
    print(f"Recursive folders: {'yes' if request.recursive else 'no'}", flush=True)
    print(f"Images to inspect: {total}", flush=True)
    print("Detects regular grids AND recursive asymmetric/T-junction layouts.", flush=True)
    print("="*82, flush=True)

    for i, path in enumerate(paths, 1):
        try:
            rel = path.relative_to(request.folder)
        except Exception:
            rel = path.name
        print(f"[{i:>{len(str(max(1,total)))}}/{total}] {rel}", end=" ... ", flush=True)
        try:
            im = open_image(path)
            det = detect_photo_grid(im)
            if det is None:
                print("not a grid", flush=True)
            elif det.confidence >= request.threshold:
                thumb = preview_copy(im, THUMB_SIZE)
                candidates.append(Candidate(path, det, True, thumb))
                print(f"LIKELY GRID | {len(det.boxes)} pieces | {det.mode} | confidence {det.confidence:.0%}", flush=True)
            else:
                print(f"grid-like {len(det.boxes)} pieces, below threshold ({det.confidence:.0%})", flush=True)
        except Exception as exc:
            errors += 1
            print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)

    candidates.sort(key=lambda c: c.detection.confidence, reverse=True)
    elapsed = time.perf_counter() - started
    msg = f"Found {len(candidates)} likely photo grids out of {total} images. Scan time: {elapsed:.1f}s."
    if errors:
        msg += f" {errors} file(s) could not be inspected."
    print("-"*82, flush=True)
    print(msg, flush=True)
    print("Detection finished. Opening visual review UI...", flush=True)
    print("="*82 + "\n", flush=True)
    return candidates, msg


# ------------------------------ UI helpers ------------------------------

class ScrollableCards(ttk.Frame):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0,0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self.window, width=e.width))


class GridSplitterApp(tk.Tk):
    def __init__(self, initial_folder=None, initial_candidates=None, initial_status=None, threshold=0.34, recursive=False):
        super().__init__()
        self.title("Advanced Photo Grid Detector & Splitter")
        self.geometry("1200x790")
        self.minsize(920, 620)
        self.folder = tk.StringVar(value=str(initial_folder) if initial_folder else "")
        self.status = tk.StringVar(value=initial_status or "Choose a folder. The UI closes while terminal scanning runs.")
        self.threshold = tk.DoubleVar(value=threshold)
        self.recursive = tk.BooleanVar(value=recursive)
        self.candidates: list[Candidate] = initial_candidates or []
        self.scan_request: ScanRequest | None = None
        self._build()
        if initial_candidates is not None:
            self._render_candidates()

    def _build(self):
        top = ttk.Frame(self, padding=8); top.pack(fill="x")
        ttk.Button(top, text="Browse Folder…", command=self.choose_folder).pack(side="left")
        ttk.Entry(top, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Checkbutton(top, text="Include subfolders", variable=self.recursive).pack(side="left", padx=(0,8))
        ttk.Button(top, text="Scan / Rescan", command=self.scan).pack(side="left")

        opts = ttk.Frame(self, padding=(8,0,8,8)); opts.pack(fill="x")
        ttk.Label(opts, text="Minimum confidence shown:").pack(side="left")
        ttk.Scale(opts, from_=0.05, to=0.9, variable=self.threshold, orient="horizontal", length=200).pack(side="left", padx=6)
        ttk.Button(opts, text="Select All", command=lambda:self._set_all(True)).pack(side="left", padx=(12,4))
        ttk.Button(opts, text="Select None", command=lambda:self._set_all(False)).pack(side="left", padx=4)
        ttk.Button(opts, text="Review Selected…", command=self.review_selected).pack(side="right")

        self.cards = ScrollableCards(self, padding=(8,0,8,0)); self.cards.pack(fill="both", expand=True)
        bottom = ttk.Frame(self, padding=8); bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)

    def choose_folder(self):
        p = filedialog.askdirectory(title="Choose folder containing images")
        if p:
            self.folder.set(p); self.scan()

    def scan(self):
        root = Path(self.folder.get().strip())
        if not root.is_dir():
            messagebox.showerror("Folder required", "Choose a valid folder first."); return
        self.scan_request = ScanRequest(root, float(self.threshold.get()), bool(self.recursive.get()))
        self.quit(); self.destroy()

    def _render_candidates(self):
        for child in self.cards.inner.winfo_children(): child.destroy()
        if not self.candidates:
            ttk.Label(self.cards.inner, text="No likely photo grids met the current threshold.", padding=18).pack(); return
        for idx, cand in enumerate(self.candidates):
            card = ttk.Frame(self.cards.inner, padding=7, relief="ridge")
            card.grid(row=idx//4, column=idx%4, sticky="nsew", padx=5, pady=5)
            self.cards.inner.columnconfigure(idx%4, weight=1)
            if cand.thumb is None:
                cand.thumb = preview_copy(open_image(cand.path), THUMB_SIZE)
            cand.tk_thumb = ImageTk.PhotoImage(cand.thumb)
            ttk.Label(card, image=cand.tk_thumb).pack()
            var = tk.BooleanVar(value=cand.selected)
            ttk.Checkbutton(card, text=cand.path.name, variable=var,
                            command=lambda c=cand,v=var:setattr(c,"selected",bool(v.get()))).pack(anchor="w", pady=(5,0))
            d = cand.detection
            ttk.Label(card, text=f"{len(d.boxes)} pieces • {d.mode} • confidence {d.confidence:.0%}").pack(anchor="w")
            ttk.Label(card, text=d.reason, foreground="#666", wraplength=220).pack(anchor="w")

    def _set_all(self, value):
        for c in self.candidates: c.selected = value
        self._render_candidates()

    def review_selected(self):
        selected = [c for c in self.candidates if c.selected]
        if not selected:
            messagebox.showinfo("Nothing selected", "Select one or more detected grids first."); return
        ReviewWindow(self, selected)


# ------------------------------ advanced review ------------------------------

class ReviewWindow(tk.Toplevel):
    def __init__(self, master: GridSplitterApp, candidates: list[Candidate]):
        super().__init__(master)
        self.title("Verify Asymmetric Splits Before Processing")
        self.geometry("1320x870")
        self.minsize(1000, 720)
        self.items = [ReviewItem(c, c.detection.boxes.copy(), True) for c in candidates]
        self.index = 0
        self.current_image: Image.Image | None = None
        self.preview_image: Image.Image | None = None
        self.preview_tk = None
        self.crop_tks = []
        self.selected_box = 0
        self.scale_x = self.scale_y = 1.0
        self.item_var = tk.StringVar()
        self.approved_var = tk.BooleanVar(value=True)
        self.status = tk.StringVar(value="Click a numbered rectangle to select it. Double-click splits it vertically; Shift+double-click splits horizontally.")
        self.equal_rows = tk.IntVar(value=2)
        self.equal_cols = tk.IntVar(value=2)
        self._build(); self._load_item(0)

    def _build(self):
        toolbar = ttk.Frame(self, padding=8); toolbar.pack(fill="x")
        ttk.Button(toolbar, text="◀ Previous", command=self.prev_item).pack(side="left")
        ttk.Button(toolbar, text="Next ▶", command=self.next_item).pack(side="left", padx=5)
        ttk.Label(toolbar, textvariable=self.item_var).pack(side="left", padx=12)
        ttk.Checkbutton(toolbar, text="Approve this grid", variable=self.approved_var, command=self._approval_changed).pack(side="right")

        ctrl = ttk.Frame(self, padding=(8,0,8,8)); ctrl.pack(fill="x")
        ttk.Button(ctrl, text="Split Selected Vertically", command=lambda:self.split_selected("v")).pack(side="left")
        ttk.Button(ctrl, text="Split Selected Horizontally", command=lambda:self.split_selected("h")).pack(side="left", padx=5)
        ttk.Button(ctrl, text="Merge Selected + Best Neighbor", command=self.merge_selected).pack(side="left", padx=5)
        ttk.Button(ctrl, text="Restore Auto Detection", command=self.restore_auto).pack(side="left", padx=(12,5))
        ttk.Label(ctrl, text="Equal grid override:").pack(side="left", padx=(16,4))
        ttk.Spinbox(ctrl, from_=1, to=8, textvariable=self.equal_rows, width=3).pack(side="left")
        ttk.Label(ctrl, text="×").pack(side="left")
        ttk.Spinbox(ctrl, from_=1, to=8, textvariable=self.equal_cols, width=3).pack(side="left")
        ttk.Button(ctrl, text="Apply", command=self.apply_equal_grid).pack(side="left", padx=5)

        panes = ttk.Panedwindow(self, orient="horizontal"); panes.pack(fill="both", expand=True, padx=8)
        left = ttk.Labelframe(panes, text="Before — numbered detected pieces", padding=6)
        right = ttk.Labelframe(panes, text="After — output pieces", padding=6)
        panes.add(left, weight=3); panes.add(right, weight=2)

        self.canvas = tk.Canvas(left, background="#202020", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e:self._redraw_before())
        self.canvas.bind("<Button-1>", self._select_at)
        self.canvas.bind("<Double-Button-1>", self._split_at_click)

        self.crop_canvas = tk.Canvas(right, highlightthickness=0)
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.crop_canvas.yview)
        self.crop_inner = ttk.Frame(self.crop_canvas)
        win = self.crop_canvas.create_window((0,0), window=self.crop_inner, anchor="nw")
        self.crop_canvas.configure(yscrollcommand=scroll.set)
        self.crop_canvas.pack(side="left", fill="both", expand=True); scroll.pack(side="right", fill="y")
        self.crop_inner.bind("<Configure>", lambda e:self.crop_canvas.configure(scrollregion=self.crop_canvas.bbox("all")))
        self.crop_canvas.bind("<Configure>", lambda e:self.crop_canvas.itemconfigure(win, width=e.width))

        bottom = ttk.Frame(self, padding=8); bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Approve Batch & Save Splits", command=self.process_batch).pack(side="right")

    def _save_current_state(self):
        if self.items: self.items[self.index].approved = bool(self.approved_var.get())

    def _approval_changed(self): self.items[self.index].approved = bool(self.approved_var.get())

    def _load_item(self, index):
        self._save_current_state(); self.index = max(0,min(index,len(self.items)-1))
        item = self.items[self.index]
        self.current_image = open_image(item.candidate.path)
        self.approved_var.set(item.approved); self.selected_box = 0
        self.item_var.set(f"{self.index+1}/{len(self.items)}  {item.candidate.path.name}  —  auto: {item.candidate.detection.mode}")
        self._redraw_before(); self._render_crops()

    def prev_item(self):
        if self.index > 0: self._load_item(self.index-1)

    def next_item(self):
        if self.index < len(self.items)-1: self._load_item(self.index+1)

    def restore_auto(self):
        self.items[self.index].boxes = self.items[self.index].candidate.detection.boxes.copy()
        self.selected_box = 0; self._redraw_before(); self._render_crops()

    def apply_equal_grid(self):
        if not self.current_image: return
        rows, cols = max(1,int(self.equal_rows.get())), max(1,int(self.equal_cols.get()))
        w,h = self.current_image.size
        xs = [round(w*i/cols) for i in range(cols+1)]
        ys = [round(h*i/rows) for i in range(rows+1)]
        self.items[self.index].boxes = [(xs[c],ys[r],xs[c+1],ys[r+1]) for r in range(rows) for c in range(cols)]
        self.selected_box = 0; self._redraw_before(); self._render_crops()

    def _preview_origin(self):
        if not self.preview_image: return (0.0,0.0)
        return ((self.canvas.winfo_width()-self.preview_image.width)/2,
                (self.canvas.winfo_height()-self.preview_image.height)/2)

    def _redraw_before(self):
        if not self.current_image or not self.canvas.winfo_exists(): return
        self.canvas.delete("all")
        cw,ch = max(100,self.canvas.winfo_width()), max(100,self.canvas.winfo_height())
        im = self.current_image.copy(); im.thumbnail((max(50,cw-20),max(50,ch-20)), Image.Resampling.LANCZOS)
        self.preview_image = im; self.preview_tk = ImageTk.PhotoImage(im)
        ox,oy = (cw-im.width)//2,(ch-im.height)//2
        self.canvas.create_image(ox,oy,anchor="nw",image=self.preview_tk)
        self.scale_x = im.width/self.current_image.width; self.scale_y = im.height/self.current_image.height
        boxes = self.items[self.index].boxes
        for i,(x0,y0,x1,y1) in enumerate(boxes):
            sx0,sy0 = ox+x0*self.scale_x, oy+y0*self.scale_y
            sx1,sy1 = ox+x1*self.scale_x, oy+y1*self.scale_y
            color = "#ffd60a" if i == self.selected_box else "#ff453a"
            width = 4 if i == self.selected_box else 2
            self.canvas.create_rectangle(sx0,sy0,sx1,sy1,outline=color,width=width)
            self.canvas.create_rectangle(sx0+3,sy0+3,sx0+29,sy0+26,fill="#000000",outline=color)
            self.canvas.create_text(sx0+16,sy0+15,text=str(i+1),fill="white",font=("Segoe UI",10,"bold"))

    def _box_at_event(self,e):
        if not self.current_image or not self.preview_image: return None, None, None
        ox,oy = self._preview_origin()
        ix,iy = (e.x-ox)/self.scale_x, (e.y-oy)/self.scale_y
        for i,b in enumerate(self.items[self.index].boxes):
            if b[0] <= ix <= b[2] and b[1] <= iy <= b[3]: return i,ix,iy
        return None,ix,iy

    def _select_at(self,e):
        i,_,_ = self._box_at_event(e)
        if i is not None:
            self.selected_box=i; self._redraw_before(); self._render_crops()

    def _split_at_click(self,e):
        i,ix,iy = self._box_at_event(e)
        if i is None: return
        self.selected_box=i
        axis = "h" if (e.state & 0x0001) else "v"
        self._split_box(i,axis,ix if axis=="v" else iy)

    def split_selected(self,axis):
        boxes=self.items[self.index].boxes
        if not boxes: return
        i=min(self.selected_box,len(boxes)-1); x0,y0,x1,y1=boxes[i]
        at=(x0+x1)/2 if axis=="v" else (y0+y1)/2
        self._split_box(i,axis,at)

    def _split_box(self,i,axis,at):
        boxes=self.items[self.index].boxes; x0,y0,x1,y1=boxes[i]
        if axis=="v":
            c=int(round(max(x0+8,min(x1-8,at))))
            if c<=x0+7 or c>=x1-7:return
            repl=[(x0,y0,c,y1),(c,y0,x1,y1)]
        else:
            c=int(round(max(y0+8,min(y1-8,at))))
            if c<=y0+7 or c>=y1-7:return
            repl=[(x0,y0,x1,c),(x0,c,x1,y1)]
        boxes[i:i+1]=repl; self.selected_box=i
        self._redraw_before(); self._render_crops()

    def merge_selected(self):
        boxes=self.items[self.index].boxes
        if len(boxes)<2:return
        i=min(self.selected_box,len(boxes)-1); a=boxes[i]
        candidates=[]
        for j,b in enumerate(boxes):
            if i==j:continue
            # Only merge if the union is exactly rectangular with no gap.
            ux0,uy0,ux1,uy1=min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])
            area_union=(ux1-ux0)*(uy1-uy0); area_sum=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])
            if area_union==area_sum:
                shared = (a[2]==b[0] or b[2]==a[0] or a[3]==b[1] or b[3]==a[1])
                if shared:candidates.append((area_union,j,(ux0,uy0,ux1,uy1)))
        if not candidates:
            messagebox.showinfo("No mergeable neighbor","The selected piece has no adjacent piece that can be merged into one rectangle.");return
        _,j,u=min(candidates,key=lambda t:t[0])
        keep=[b for k,b in enumerate(boxes) if k not in {i,j}]; keep.append(u); keep.sort(key=lambda b:(b[1],b[0]))
        self.items[self.index].boxes=keep; self.selected_box=keep.index(u)
        self._redraw_before(); self._render_crops()

    def _render_crops(self):
        if not self.current_image:return
        for child in self.crop_inner.winfo_children():child.destroy()
        self.crop_tks.clear(); boxes=self.items[self.index].boxes
        for i,b in enumerate(boxes):
            crop=self.current_image.crop(b); thumb=preview_copy(crop,CROP_THUMB); tkimg=ImageTk.PhotoImage(thumb); self.crop_tks.append(tkimg)
            card=ttk.Frame(self.crop_inner,padding=5,relief="ridge")
            card.grid(row=i//2,column=i%2,sticky="nsew",padx=4,pady=4)
            ttk.Label(card,image=tkimg).pack(); ttk.Label(card,text=f"Part {i+1}   {crop.width}×{crop.height}").pack()
            if i==self.selected_box: ttk.Label(card,text="SELECTED",foreground="#b8860b").pack()
        self.status.set(f"{len(boxes)} output images. Click a piece to select; double-click exact split position (Shift = horizontal).")

    def process_batch(self):
        self._save_current_state(); approved=[it for it in self.items if it.approved]
        if not approved:
            messagebox.showinfo("Nothing approved","No grids are approved for processing.");return
        count=sum(len(it.boxes) for it in approved)
        if not messagebox.askyesno("Final approval",f"Create {count} new images from {len(approved)} approved photo grid(s)?\n\nOnly after every piece from a grid saves successfully will its original composite be moved to the Windows Recycle Bin."):
            return
        successes=0; failures=[]; created=[]
        for it in approved:
            src=it.candidate.path; local=[]
            try:
                im=open_image(src); boxes=_dedupe_and_validate_boxes(it.boxes,im.width,im.height)
                if len(boxes)<2: raise RuntimeError("Split definition produces fewer than 2 images")
                ext=safe_output_extension(src)
                for n,b in enumerate(boxes,1):
                    crop=im.crop(b); final=unique_path(src.with_name(f"{src.stem}_part_{n:02d}{ext}")); save_crop_atomic(crop,final); local.append(final)
                send2trash(str(src)); created.extend(local); successes+=1
            except Exception as exc:
                failures.append(f"{src.name}: {exc}")
        if failures:
            messagebox.showwarning("Batch completed with issues",f"Successfully processed {successes}/{len(approved)} grids.\n\n"+"\n".join(failures[:12]))
        else:
            messagebox.showinfo("Batch complete",f"Successfully created {len(created)} images from {successes} photo grids.\nApproved source grids were moved to the Recycle Bin.")
        self.destroy(); self.master.scan()


def main():
    folder=None; candidates=None; status=None; threshold=0.34; recursive=False
    while True:
        app=GridSplitterApp(folder,candidates,status,threshold,recursive)
        app.mainloop(); request=app.scan_request
        if request is None: break
        folder=request.folder; threshold=request.threshold; recursive=request.recursive
        candidates,status=scan_folder_terminal(request)


if __name__ == "__main__":
    main()
