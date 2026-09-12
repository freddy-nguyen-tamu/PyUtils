from __future__ import annotations

import os
import sys
import time
import math
import traceback
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import numpy as np
    from PIL import Image, ImageTk, ImageOps, ImageDraw
except ImportError as exc:
    raise SystemExit("Missing dependency. Run: py -m pip install pillow numpy send2trash") from exc

try:
    from send2trash import send2trash
except ImportError as exc:
    raise SystemExit("Missing dependency. Run: py -m pip install send2trash") from exc

# Optional HEIF/HEIC support.
try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

# Ask Pillow which filename extensions this particular installation can decode
# instead of maintaining a small hard-coded whitelist. Optional plugins such as
# pillow-heif extend this registry too.
Image.init()
SUPPORTED_EXTS = {ext.lower() for ext in Image.registered_extensions()}
SUPPORTED_EXTS.update({".heic", ".heif"})

THUMB_SIZE = (220, 160)
PREVIEW_MAX = (920, 650)
CROP_THUMB = (160, 120)


@dataclass
class GridDetection:
    rows: int
    cols: int
    x_cuts: list[int]
    y_cuts: list[int]
    confidence: float
    score: float
    reason: str = ""


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
    x_cuts: list[int] = field(default_factory=list)
    y_cuts: list[int] = field(default_factory=list)
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
        # Composites are easier to reason about as RGB/RGBA.
        if im.mode not in ("RGB", "RGBA"):
            if "A" in im.getbands():
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")
        return im.copy()


def preview_copy(im: Image.Image, max_size: tuple[int, int]) -> Image.Image:
    out = im.copy()
    out.thumbnail(max_size, Image.Resampling.LANCZOS)
    return out


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    i = 2
    while True:
        p = path.with_name(f"{stem}_{i}{suffix}")
        if not p.exists():
            return p
        i += 1


def safe_output_extension(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        return ext
    # HEIC/GIF input is written as PNG to avoid multi-frame / encoder surprises.
    return ".png"


def save_crop_atomic(image: Image.Image, final_path: Path) -> None:
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = final_path.with_name(f".{final_path.stem}.tmp{final_path.suffix}")
    params = {}
    ext = final_path.suffix.lower()
    out = image
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


# ------------------------------ grid detector ------------------------------

def _resample_rgb(im: Image.Image, max_side: int = 1200) -> tuple[np.ndarray, float, float]:
    rgb = im.convert("RGB")
    w0, h0 = rgb.size
    scale = min(1.0, max_side / max(w0, h0))
    if scale < 1.0:
        w = max(2, round(w0 * scale))
        h = max(2, round(h0 * scale))
        rgb = rgb.resize((w, h), Image.Resampling.LANCZOS)
    arr = np.asarray(rgb, dtype=np.float32) / 255.0
    sx = w0 / arr.shape[1]
    sy = h0 / arr.shape[0]
    return arr, sx, sy


def _seam_profiles(arr: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return robust discontinuity profiles for vertical/horizontal boundaries.

    A collage boundary usually creates a discontinuity across a large fraction of the
    opposite axis. The 65th percentile makes the score less sensitive to a single
    natural object edge while still rewarding long seams.
    """
    # Per-pixel RGB jump across adjacent columns/rows.
    vd = np.mean(np.abs(arr[:, 1:, :] - arr[:, :-1, :]), axis=2)
    hd = np.mean(np.abs(arr[1:, :, :] - arr[:-1, :, :]), axis=2)

    v = np.percentile(vd, 65, axis=0)
    h = np.percentile(hd, 65, axis=1)

    # Also reward boundaries whose local statistics differ on both sides.
    # This catches seamless-looking but compositionally different adjacent photos.
    gray = np.mean(arr, axis=2)
    win = max(2, min(arr.shape[0], arr.shape[1]) // 150)

    def stat_jump_vertical() -> np.ndarray:
        out = np.zeros(arr.shape[1] - 1, dtype=np.float32)
        for x in range(win, arr.shape[1] - win):
            l = gray[:, x-win:x]
            r = gray[:, x:x+win]
            # Per-row difference in local mean; percentile rewards continuity.
            d = np.abs(l.mean(axis=1) - r.mean(axis=1))
            out[x-1] = np.percentile(d, 65)
        return out

    def stat_jump_horizontal() -> np.ndarray:
        out = np.zeros(arr.shape[0] - 1, dtype=np.float32)
        for y in range(win, arr.shape[0] - win):
            t = gray[y-win:y, :]
            b = gray[y:y+win, :]
            d = np.abs(t.mean(axis=0) - b.mean(axis=0))
            out[y-1] = np.percentile(d, 65)
        return out

    # Keep runtime reasonable for large previews: only add the heavier statistic on
    # normal-sized scan previews.
    if arr.shape[0] * arr.shape[1] <= 1_500_000:
        v2 = stat_jump_vertical()
        h2 = stat_jump_horizontal()
        v = 0.72 * v + 0.28 * v2
        h = 0.72 * h + 0.28 * h2

    return v, h


def _robust_z(profile: np.ndarray) -> np.ndarray:
    med = float(np.median(profile))
    mad = float(np.median(np.abs(profile - med))) + 1e-6
    return (profile - med) / (1.4826 * mad)


def _best_cut_near(z: np.ndarray, expected: float, radius: int) -> tuple[int, float]:
    # z index i corresponds to a cut between pixels i and i+1 => coordinate i+1.
    n = len(z) + 1
    center = int(round(expected))
    lo = max(1, center - radius)
    hi = min(n - 1, center + radius)
    if hi <= lo:
        return max(1, min(n - 1, center)), -999.0
    sub = z[lo-1:hi]
    idx = int(np.argmax(sub))
    coord = lo + idx
    return coord, float(sub[idx])


def _regularity(cuts: list[int], length: int, parts: int) -> float:
    if parts <= 1:
        return 1.0
    bounds = [0] + cuts + [length]
    sizes = np.diff(bounds).astype(np.float32)
    mean = float(np.mean(sizes))
    if mean <= 0:
        return 0.0
    cv = float(np.std(sizes) / mean)
    return max(0.0, 1.0 - cv / 0.18)


def _best_axis_partition(z: np.ndarray, raw: np.ndarray, length: int, max_parts: int) -> tuple[int, list[int], float, float, float, float]:
    """Choose 1..max_parts cells for one axis.

    Returns (parts, cuts, evidence, min_seam_z, regularity).  A real grid with N
    cells should have *all* N-1 separators supported; using the weakest separator
    in the score prevents one spectacular natural edge from making a fake 3/4-cell grid.
    """
    best_parts = 1
    best_cuts: list[int] = []
    best_score = 0.0
    best_min = 0.0
    best_reg = 1.0
    best_ratio = 1.0
    raw_med = float(np.median(raw)) + 1e-6

    for parts in range(2, max_parts + 1):
        if length / parts < 70:
            continue
        radius = max(3, int(length / parts * 0.08))
        cuts: list[int] = []
        vals: list[float] = []
        raw_vals: list[float] = []
        for i in range(1, parts):
            cut, sc = _best_cut_near(z, length * i / parts, radius)
            cuts.append(cut)
            vals.append(sc)
            raw_vals.append(float(raw[cut - 1]))
        if not vals:
            continue
        reg = _regularity(cuts, length, parts)
        weakest = float(np.min(vals))
        mean = float(np.mean(vals))
        weakest_raw = float(np.min(raw_vals))
        weakest_ratio = weakest_raw / raw_med

        # Evidence is dominated by the weakest required seam. Complexity gets a
        # small cost so 3/4-way layouts must actually show the extra boundaries.
        evidence = 0.62 * weakest + 0.23 * mean + 1.0 * reg + 1.15 * max(0.0, weakest_ratio - 1.0) - 0.28 * (parts - 2)
        if evidence > best_score:
            best_parts = parts
            best_cuts = cuts
            best_score = evidence
            best_min = weakest
            best_reg = reg
            best_ratio = weakest_ratio

    # Below this, treat the axis as unsplit. Lowering the UI confidence threshold
    # can reveal borderline *images*, but we avoid inventing unsupported cut lines.
    # Robust-z alone can exaggerate tiny variations in a smooth ordinary photo, so
    # require the separator to also be materially stronger than the image's normal
    # adjacent-column/row discontinuity.
    if best_min < 2.6 or best_reg < 0.55 or best_ratio < 1.18:
        return 1, [], 0.0, best_min, best_reg, best_ratio
    return best_parts, best_cuts, best_score, best_min, best_reg, best_ratio


def detect_regular_grid(im: Image.Image, max_parts: int = 4) -> GridDetection | None:
    """Detect common regular photo grids (1x2 through 4x4).

    Horizontal and vertical evidence are solved independently. This matters for 2x2,
    2x3, 3x3, etc.: choosing one global model often prefers only the strongest axis
    and misses the other perfectly valid set of separators.
    """
    arr, sx, sy = _resample_rgb(im)
    h, w = arr.shape[:2]
    if w < 100 or h < 100:
        return None

    vprof, hprof = _seam_profiles(arr)
    vz, hz = _robust_z(vprof), _robust_z(hprof)

    cols, xcuts, xev, xmin, xreg, xratio = _best_axis_partition(vz, vprof, w, max_parts)
    rows, ycuts, yev, ymin, yreg, yratio = _best_axis_partition(hz, hprof, h, max_parts)
    if rows == 1 and cols == 1:
        return None

    # Map scan-preview coordinates back to full-resolution pixels.
    ox = [int(round(x * sx)) for x in xcuts]
    oy = [int(round(y * sy)) for y in ycuts]

    active = []
    if cols > 1:
        active.append((xmin, xreg, xev, xratio))
    if rows > 1:
        active.append((ymin, yreg, yev, yratio))

    # Conservative confidence. A second independently-supported axis is a useful
    # signal that the image is a collage rather than a photo with one strong edge.
    axis_conf = []
    for minz, reg, ev, ratio in active:
        zc = 1.0 / (1.0 + math.exp(-(min(12.0, minz) - 3.2) / 1.5))
        rc = float(np.clip((ratio - 1.12) / 0.55, 0.0, 1.0))
        axis_conf.append(float(np.clip(zc * reg * (0.45 + 0.55 * rc), 0.0, 1.0)))
    if len(axis_conf) == 1:
        confidence = axis_conf[0] * 0.82
    else:
        confidence = 1.0 - (1.0 - axis_conf[0]) * (1.0 - axis_conf[1])
        confidence = min(1.0, confidence + 0.08)

    score = sum(a[2] for a in active) + (0.8 if len(active) == 2 else 0.0)
    pieces = rows * cols
    reason = f"{rows}×{cols} ({pieces} pieces); "
    if cols > 1:
        reason += f"V seam {xratio:.2f}× baseline "
    if rows > 1:
        reason += f"H seam {yratio:.2f}× baseline"

    return GridDetection(rows, cols, ox, oy, float(confidence), float(score), reason.strip())


# ------------------------------ crop geometry ------------------------------

def normalized_cuts(cuts: list[int], length: int) -> list[int]:
    vals = sorted({int(round(c)) for c in cuts if 2 <= c <= length - 2})
    # Avoid accidental 1-2 px slivers.
    out: list[int] = []
    for c in vals:
        if not out or c - out[-1] >= 4:
            out.append(c)
    return out


def crop_boxes(size: tuple[int, int], x_cuts: list[int], y_cuts: list[int]) -> list[tuple[int, int, int, int]]:
    w, h = size
    xs = [0] + normalized_cuts(x_cuts, w) + [w]
    ys = [0] + normalized_cuts(y_cuts, h) + [h]
    return [(xs[c], ys[r], xs[c+1], ys[r+1]) for r in range(len(ys)-1) for c in range(len(xs)-1)]


# ------------------------------ terminal-only scanner ------------------------------

def ensure_console() -> None:
    """Make terminal progress visible when launched without an attached console.

    Normal `py photo_grid_splitter.py` launches already have a console. This fallback
    mainly helps when the script is started by double-clicking with python.exe/pythonw.
    """
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
    paths = [
        p for p in iterator
        if p.is_file() and p.suffix.lower() in SUPPORTED_EXTS
    ]
    paths.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))
    return paths


def scan_folder_terminal(request: ScanRequest) -> tuple[list[Candidate], str]:
    """Scan with *no Tk window alive* and print one progress result per image."""
    ensure_console()
    root = request.folder
    paths = collect_image_paths(root, request.recursive)
    total = len(paths)
    candidates: list[Candidate] = []
    errors = 0
    started = time.perf_counter()

    print("\n" + "=" * 78, flush=True)
    print("PHOTO GRID DETECTOR - TERMINAL SCAN", flush=True)
    print(f"Folder: {root}", flush=True)
    print(f"Recursive: {'yes' if request.recursive else 'no'}", flush=True)
    print(f"Recognized image extensions: {len(SUPPORTED_EXTS)}", flush=True)
    print(f"Images to inspect: {total}", flush=True)
    print("The visual UI is closed during this phase to keep scanning responsive.", flush=True)
    print("=" * 78, flush=True)

    if not paths:
        msg = "No Pillow-readable image files were found in the chosen folder."
        print(msg, flush=True)
        print("Opening the UI again...\n", flush=True)
        return [], msg

    for i, path in enumerate(paths, 1):
        try:
            rel = path.relative_to(root)
        except Exception:
            rel = path.name
        prefix = f"[{i:>{len(str(total))}}/{total}] {rel}"
        print(prefix, end=" ... ", flush=True)
        try:
            im = open_image(path)
            det = detect_regular_grid(im)
            if det is None:
                print("not a grid", flush=True)
            elif det.confidence >= request.threshold:
                thumb = preview_copy(im, THUMB_SIZE)
                candidates.append(Candidate(path, det, selected=True, thumb=thumb))
                print(
                    f"LIKELY GRID {det.rows}x{det.cols} | confidence {det.confidence:.0%}",
                    flush=True,
                )
            else:
                print(
                    f"grid-like {det.rows}x{det.cols}, below threshold "
                    f"({det.confidence:.0%} < {request.threshold:.0%})",
                    flush=True,
                )
        except Exception as exc:
            errors += 1
            print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)

    candidates.sort(key=lambda c: c.detection.confidence, reverse=True)
    elapsed = time.perf_counter() - started
    msg = f"Found {len(candidates)} likely photo grids out of {total} images."
    if errors:
        msg += f" {errors} file(s) could not be inspected."
    msg += f" Scan time: {elapsed:.1f}s."

    print("-" * 78, flush=True)
    print(msg, flush=True)
    print("Detection finished. Opening the visual review UI again...", flush=True)
    print("=" * 78 + "\n", flush=True)
    return candidates, msg


# ------------------------------ candidate visualizer ------------------------------

class ScrollableCards(ttk.Frame):
    def __init__(self, master, *args, **kwargs):
        super().__init__(master, *args, **kwargs)
        self.canvas = tk.Canvas(self, highlightthickness=0)
        self.scroll = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas)
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.canvas.configure(yscrollcommand=self.scroll.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.scroll.pack(side="right", fill="y")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize)
        self.canvas.bind_all("<MouseWheel>", self._wheel)

    def _resize(self, e):
        self.canvas.itemconfigure(self.window, width=e.width)

    def _wheel(self, e):
        # Only scroll if pointer is over this widget tree.
        try:
            x, y = self.winfo_pointerxy()
            widget = self.winfo_containing(x, y)
            while widget is not None:
                if widget == self:
                    self.canvas.yview_scroll(int(-e.delta / 120), "units")
                    return
                widget = widget.master
        except Exception:
            pass


class GridSplitterApp(tk.Tk):
    def __init__(
        self,
        initial_folder: Path | None = None,
        initial_candidates: list[Candidate] | None = None,
        initial_status: str | None = None,
        threshold: float = 0.35,
        recursive: bool = False,
    ):
        super().__init__()
        self.title("Photo Grid Detector & Splitter")
        self.geometry("1180x780")
        self.minsize(900, 620)

        self.folder = tk.StringVar(value=str(initial_folder) if initial_folder else "")
        self.status = tk.StringVar(value=initial_status or "Choose a folder. The UI will close while the terminal scans it.")
        self.threshold = tk.DoubleVar(value=threshold)
        self.recursive = tk.BooleanVar(value=recursive)
        self.candidates: list[Candidate] = initial_candidates or []
        self.scan_request: ScanRequest | None = None

        self._build()
        if initial_candidates is not None:
            self._render_candidates()

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="Browse Folder…", command=self.choose_folder).pack(side="left")
        ttk.Entry(top, textvariable=self.folder).pack(side="left", fill="x", expand=True, padx=8)
        ttk.Checkbutton(top, text="Include subfolders", variable=self.recursive).pack(side="left", padx=(0, 8))
        ttk.Button(top, text="Scan / Rescan", command=self.scan).pack(side="left")

        opts = ttk.Frame(self, padding=(8, 0, 8, 8))
        opts.pack(fill="x")
        ttk.Label(opts, text="Minimum confidence shown:").pack(side="left")
        ttk.Scale(opts, from_=0.0, to=0.9, variable=self.threshold, orient="horizontal", length=180).pack(side="left", padx=6)
        ttk.Button(opts, text="Select All", command=lambda: self._set_all(True)).pack(side="left", padx=(12, 4))
        ttk.Button(opts, text="Select None", command=lambda: self._set_all(False)).pack(side="left", padx=4)
        ttk.Button(opts, text="Review Selected…", command=self.review_selected).pack(side="right")

        self.cards = ScrollableCards(self, padding=(8, 0, 8, 0))
        self.cards.pack(fill="both", expand=True)

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)

    def choose_folder(self):
        p = filedialog.askdirectory(title="Choose folder containing images")
        if p:
            self.folder.set(p)
            # This intentionally closes the entire Tk UI before any image decoding or
            # detection begins. main() performs the scan and creates a fresh Tk root
            # only after every image has been inspected.
            self.scan()

    def scan(self):
        root = Path(self.folder.get().strip())
        if not root.is_dir():
            messagebox.showerror("Folder required", "Choose a valid folder first.")
            return

        # Capture primitive values before destroying Tk variables/root.
        self.scan_request = ScanRequest(
            folder=root,
            threshold=float(self.threshold.get()),
            recursive=bool(self.recursive.get()),
        )
        self.quit()
        self.destroy()

    def _render_candidates(self):
        for child in self.cards.inner.winfo_children():
            child.destroy()
        if not self.candidates:
            ttk.Label(self.cards.inner, text="No likely photo grids met the current confidence threshold.", padding=18).pack()
            return

        for idx, cand in enumerate(self.candidates):
            card = ttk.Frame(self.cards.inner, padding=7, relief="ridge")
            card.grid(row=idx // 4, column=idx % 4, sticky="nsew", padx=5, pady=5)
            self.cards.inner.columnconfigure(idx % 4, weight=1)
            if cand.thumb is None:
                try:
                    cand.thumb = preview_copy(open_image(cand.path), THUMB_SIZE)
                except Exception:
                    continue
            cand.tk_thumb = ImageTk.PhotoImage(cand.thumb)
            img_label = ttk.Label(card, image=cand.tk_thumb)
            img_label.pack()
            var = tk.BooleanVar(value=cand.selected)
            chk = ttk.Checkbutton(card, text=cand.path.name, variable=var,
                                  command=lambda c=cand, v=var: setattr(c, "selected", bool(v.get())))
            chk.pack(anchor="w", pady=(5, 0))
            d = cand.detection
            ttk.Label(card, text=f"Detected {d.rows}×{d.cols}   confidence {d.confidence:.0%}").pack(anchor="w")
            ttk.Label(card, text=d.reason, foreground="#666666").pack(anchor="w")

    def _set_all(self, value: bool):
        for c in self.candidates:
            c.selected = value
        self._render_candidates()

    def review_selected(self):
        selected = [c for c in self.candidates if c.selected]
        if not selected:
            messagebox.showinfo("Nothing selected", "Select one or more detected grids first.")
            return
        ReviewWindow(self, selected)


# ------------------------------ split review visualizer ------------------------------

class ReviewWindow(tk.Toplevel):
    def __init__(self, master: GridSplitterApp, candidates: list[Candidate]):
        super().__init__(master)
        self.title("Verify Splits Before Processing")
        self.geometry("1280x860")
        self.minsize(980, 700)
        self.items = [ReviewItem(c, c.detection.x_cuts.copy(), c.detection.y_cuts.copy(), True) for c in candidates]
        self.index = 0
        self.current_image: Image.Image | None = None
        self.preview_image: Image.Image | None = None
        self.preview_tk: ImageTk.PhotoImage | None = None
        self.crop_tks: list[ImageTk.PhotoImage] = []
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.drag_axis: str | None = None
        self.drag_index: int | None = None

        self.item_var = tk.StringVar()
        self.approved_var = tk.BooleanVar(value=True)
        self.rows_var = tk.IntVar(value=1)
        self.cols_var = tk.IntVar(value=1)
        self.status = tk.StringVar(value="Drag a split line to adjust it. Double-click to add a line; right-click near a line to remove it.")

        self._build()
        self._load_item(0)

    def _build(self):
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(fill="x")
        ttk.Button(toolbar, text="◀ Previous", command=self.prev_item).pack(side="left")
        ttk.Button(toolbar, text="Next ▶", command=self.next_item).pack(side="left", padx=5)
        ttk.Label(toolbar, textvariable=self.item_var).pack(side="left", padx=12)
        ttk.Checkbutton(toolbar, text="Approve this grid", variable=self.approved_var, command=self._approval_changed).pack(side="right")

        control = ttk.Frame(self, padding=(8, 0, 8, 8))
        control.pack(fill="x")
        ttk.Label(control, text="Regular grid override:").pack(side="left")
        ttk.Label(control, text="Rows").pack(side="left", padx=(10, 3))
        ttk.Spinbox(control, from_=1, to=8, textvariable=self.rows_var, width=4).pack(side="left")
        ttk.Label(control, text="Columns").pack(side="left", padx=(10, 3))
        ttk.Spinbox(control, from_=1, to=8, textvariable=self.cols_var, width=4).pack(side="left")
        ttk.Button(control, text="Apply Equal Grid", command=self.apply_equal_grid).pack(side="left", padx=8)
        ttk.Button(control, text="Restore Auto Detection", command=self.restore_auto).pack(side="left")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)

        left = ttk.Labelframe(panes, text="Before — detected split lines", padding=6)
        right = ttk.Labelframe(panes, text="After — resulting images", padding=6)
        panes.add(left, weight=3)
        panes.add(right, weight=2)

        self.canvas = tk.Canvas(left, background="#202020", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw_before())
        self.canvas.bind("<ButtonPress-1>", self._start_drag)
        self.canvas.bind("<B1-Motion>", self._drag_line)
        self.canvas.bind("<ButtonRelease-1>", self._end_drag)
        self.canvas.bind("<Double-Button-1>", self._add_line)
        self.canvas.bind("<Button-3>", self._remove_line)

        self.crop_canvas = tk.Canvas(right, highlightthickness=0)
        self.crop_scroll = ttk.Scrollbar(right, orient="vertical", command=self.crop_canvas.yview)
        self.crop_inner = ttk.Frame(self.crop_canvas)
        self.crop_window = self.crop_canvas.create_window((0, 0), window=self.crop_inner, anchor="nw")
        self.crop_canvas.configure(yscrollcommand=self.crop_scroll.set)
        self.crop_canvas.pack(side="left", fill="both", expand=True)
        self.crop_scroll.pack(side="right", fill="y")
        self.crop_inner.bind("<Configure>", lambda e: self.crop_canvas.configure(scrollregion=self.crop_canvas.bbox("all")))
        self.crop_canvas.bind("<Configure>", lambda e: self.crop_canvas.itemconfigure(self.crop_window, width=e.width))

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status).pack(side="left", fill="x", expand=True)
        ttk.Button(bottom, text="Approve Batch & Save Splits", command=self.process_batch).pack(side="right")

    def _save_current_state(self):
        if 0 <= self.index < len(self.items):
            self.items[self.index].approved = bool(self.approved_var.get())

    def _approval_changed(self):
        self.items[self.index].approved = bool(self.approved_var.get())

    def _load_item(self, index: int):
        self._save_current_state()
        self.index = max(0, min(index, len(self.items)-1))
        item = self.items[self.index]
        self.current_image = open_image(item.candidate.path)
        self.approved_var.set(item.approved)
        self.rows_var.set(len(item.y_cuts) + 1)
        self.cols_var.set(len(item.x_cuts) + 1)
        self.item_var.set(f"{self.index + 1}/{len(self.items)}  {item.candidate.path.name}")
        self._redraw_before()
        self._render_crops()

    def prev_item(self):
        if self.index > 0:
            self._load_item(self.index - 1)

    def next_item(self):
        if self.index < len(self.items) - 1:
            self._load_item(self.index + 1)

    def apply_equal_grid(self):
        if not self.current_image:
            return
        rows = max(1, int(self.rows_var.get()))
        cols = max(1, int(self.cols_var.get()))
        w, h = self.current_image.size
        item = self.items[self.index]
        item.x_cuts = [round(w * i / cols) for i in range(1, cols)]
        item.y_cuts = [round(h * i / rows) for i in range(1, rows)]
        self._redraw_before()
        self._render_crops()

    def restore_auto(self):
        d = self.items[self.index].candidate.detection
        self.items[self.index].x_cuts = d.x_cuts.copy()
        self.items[self.index].y_cuts = d.y_cuts.copy()
        self.rows_var.set(d.rows)
        self.cols_var.set(d.cols)
        self._redraw_before()
        self._render_crops()

    def _redraw_before(self):
        if not self.current_image or not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        im = self.current_image.copy()
        im.thumbnail((max(50, cw - 20), max(50, ch - 20)), Image.Resampling.LANCZOS)
        self.preview_image = im
        self.preview_tk = ImageTk.PhotoImage(im)
        px = (cw - im.width) // 2
        py = (ch - im.height) // 2
        self.canvas.create_image(px, py, anchor="nw", image=self.preview_tk, tags="photo")
        self.scale_x = im.width / self.current_image.width
        self.scale_y = im.height / self.current_image.height

        item = self.items[self.index]
        for i, x in enumerate(item.x_cuts):
            sx = px + x * self.scale_x
            self.canvas.create_line(sx, py, sx, py + im.height, fill="#ff3b30", width=3, tags=("split", f"vx{i}"))
        for i, y in enumerate(item.y_cuts):
            sy = py + y * self.scale_y
            self.canvas.create_line(px, sy, px + im.width, sy, fill="#34c759", width=3, tags=("split", f"hy{i}"))

    def _preview_origin(self) -> tuple[float, float]:
        if not self.preview_image:
            return 0.0, 0.0
        return ((self.canvas.winfo_width() - self.preview_image.width) / 2,
                (self.canvas.winfo_height() - self.preview_image.height) / 2)

    def _nearest_line(self, cx: float, cy: float, radius: float = 10) -> tuple[str | None, int | None]:
        if not self.preview_image:
            return None, None
        ox, oy = self._preview_origin()
        item = self.items[self.index]
        best = (radius + 1, None, None)
        if oy <= cy <= oy + self.preview_image.height:
            for i, x in enumerate(item.x_cuts):
                d = abs(cx - (ox + x * self.scale_x))
                if d < best[0]:
                    best = (d, "x", i)
        if ox <= cx <= ox + self.preview_image.width:
            for i, y in enumerate(item.y_cuts):
                d = abs(cy - (oy + y * self.scale_y))
                if d < best[0]:
                    best = (d, "y", i)
        return best[1], best[2]

    def _start_drag(self, e):
        axis, idx = self._nearest_line(e.x, e.y)
        self.drag_axis, self.drag_index = axis, idx

    def _drag_line(self, e):
        if self.drag_axis is None or self.drag_index is None or not self.current_image or not self.preview_image:
            return
        ox, oy = self._preview_origin()
        item = self.items[self.index]
        if self.drag_axis == "x":
            x = int(round((e.x - ox) / self.scale_x))
            x = max(3, min(self.current_image.width - 3, x))
            item.x_cuts[self.drag_index] = x
            item.x_cuts.sort()
        else:
            y = int(round((e.y - oy) / self.scale_y))
            y = max(3, min(self.current_image.height - 3, y))
            item.y_cuts[self.drag_index] = y
            item.y_cuts.sort()
        self._redraw_before()

    def _end_drag(self, e):
        if self.drag_axis is not None:
            self.drag_axis = self.drag_index = None
            self.rows_var.set(len(self.items[self.index].y_cuts) + 1)
            self.cols_var.set(len(self.items[self.index].x_cuts) + 1)
            self._render_crops()

    def _add_line(self, e):
        if not self.current_image or not self.preview_image:
            return
        ox, oy = self._preview_origin()
        if not (ox <= e.x <= ox + self.preview_image.width and oy <= e.y <= oy + self.preview_image.height):
            return
        # Add along the axis whose normalized location is closer to an interior division.
        nx = (e.x - ox) / self.preview_image.width
        ny = (e.y - oy) / self.preview_image.height
        item = self.items[self.index]
        # Choose vertical when click is closer to left/right half boundaries; Shift forces horizontal.
        if e.state & 0x0001:  # Shift
            axis = "y"
        else:
            dx = min(abs(nx - q) for q in (0.25, 0.333, 0.5, 0.667, 0.75))
            dy = min(abs(ny - q) for q in (0.25, 0.333, 0.5, 0.667, 0.75))
            axis = "x" if dx <= dy else "y"
        if axis == "x":
            item.x_cuts.append(int(round((e.x - ox) / self.scale_x)))
            item.x_cuts = normalized_cuts(item.x_cuts, self.current_image.width)
        else:
            item.y_cuts.append(int(round((e.y - oy) / self.scale_y)))
            item.y_cuts = normalized_cuts(item.y_cuts, self.current_image.height)
        self.rows_var.set(len(item.y_cuts) + 1)
        self.cols_var.set(len(item.x_cuts) + 1)
        self._redraw_before()
        self._render_crops()

    def _remove_line(self, e):
        axis, idx = self._nearest_line(e.x, e.y, radius=14)
        if axis is None or idx is None:
            return
        item = self.items[self.index]
        if axis == "x":
            item.x_cuts.pop(idx)
        else:
            item.y_cuts.pop(idx)
        self.rows_var.set(len(item.y_cuts) + 1)
        self.cols_var.set(len(item.x_cuts) + 1)
        self._redraw_before()
        self._render_crops()

    def _render_crops(self):
        if not self.current_image:
            return
        for child in self.crop_inner.winfo_children():
            child.destroy()
        self.crop_tks.clear()
        item = self.items[self.index]
        boxes = crop_boxes(self.current_image.size, item.x_cuts, item.y_cuts)
        for i, box in enumerate(boxes, 1):
            crop = self.current_image.crop(box)
            thumb = preview_copy(crop, CROP_THUMB)
            tkimg = ImageTk.PhotoImage(thumb)
            self.crop_tks.append(tkimg)
            card = ttk.Frame(self.crop_inner, padding=5, relief="ridge")
            card.grid(row=(i-1)//2, column=(i-1)%2, sticky="nsew", padx=4, pady=4)
            ttk.Label(card, image=tkimg).pack()
            ttk.Label(card, text=f"Part {i}   {crop.width}×{crop.height}").pack(anchor="center")
        self.status.set(f"{len(boxes)} output images. Drag red/green lines to correct them before approval.")

    def process_batch(self):
        self._save_current_state()
        approved = [it for it in self.items if it.approved]
        if not approved:
            messagebox.showinfo("Nothing approved", "No grids are approved for processing.")
            return
        count_parts = 0
        for it in approved:
            im = open_image(it.candidate.path)
            count_parts += len(crop_boxes(im.size, it.x_cuts, it.y_cuts))
        if not messagebox.askyesno(
            "Final approval",
            f"Create {count_parts} new images from {len(approved)} approved photo grid(s)?\n\n"
            "After each grid is split successfully, its original composite will be moved to the Windows Recycle Bin."
        ):
            return

        successes = 0
        failures: list[str] = []
        created_paths: list[Path] = []
        for it in approved:
            src = it.candidate.path
            local_created: list[Path] = []
            try:
                im = open_image(src)
                boxes = crop_boxes(im.size, it.x_cuts, it.y_cuts)
                if len(boxes) < 2:
                    raise RuntimeError("Split definition produces fewer than 2 images")
                ext = safe_output_extension(src)
                for n, box in enumerate(boxes, 1):
                    crop = im.crop(box)
                    final = unique_path(src.with_name(f"{src.stem}_part_{n:02d}{ext}"))
                    save_crop_atomic(crop, final)
                    local_created.append(final)
                # Only trash the old composite after all pieces have been saved.
                send2trash(str(src))
                created_paths.extend(local_created)
                successes += 1
            except Exception as exc:
                # Do not delete any successful split outputs automatically; they may be useful,
                # but clearly report that the original was not intentionally removed on failure.
                failures.append(f"{src.name}: {exc}")

        if failures:
            messagebox.showwarning(
                "Batch completed with issues",
                f"Successfully processed {successes}/{len(approved)} grids.\n\n" + "\n".join(failures[:12])
            )
        else:
            messagebox.showinfo(
                "Batch complete",
                f"Successfully created {len(created_paths)} images from {successes} photo grids.\n"
                "The approved source grids were moved to the Recycle Bin."
            )
        self.destroy()
        self.master.scan()


def main():
    # The app intentionally alternates between two mutually exclusive phases:
    #   1) Tk visual review UI
    #   2) terminal-only scanning with no Tk root/window alive
    # This prevents Tk repaint/event processing from competing with CPU-heavy detection.
    folder: Path | None = None
    candidates: list[Candidate] | None = None
    status: str | None = None
    threshold = 0.35
    recursive = False

    while True:
        app = GridSplitterApp(
            initial_folder=folder,
            initial_candidates=candidates,
            initial_status=status,
            threshold=threshold,
            recursive=recursive,
        )
        app.mainloop()
        request = app.scan_request
        if request is None:
            break

        folder = request.folder
        threshold = request.threshold
        recursive = request.recursive
        candidates, status = scan_folder_terminal(request)


if __name__ == "__main__":
    main()
