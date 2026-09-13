from __future__ import annotations

import os
import re
import sys
import time
import json
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

try:
    import numpy as np
    from PIL import Image, ImageTk, ImageOps
    from ultralytics import YOLO
    from send2trash import send2trash
except ImportError as exc:
    raise SystemExit(
        "Missing dependency. Run:\n"
        "  py -m pip install ultralytics pillow numpy send2trash\n"
        "Optional HEIC/HEIF support:\n"
        "  py -m pip install pillow-heif"
    ) from exc

try:
    import pillow_heif
    pillow_heif.register_heif_opener()
except Exception:
    pass

Image.init()
SUPPORTED_EXTS = {ext.lower() for ext in Image.registered_extensions()}
SUPPORTED_EXTS.update({".heic", ".heif"})
GENERATED_RE = re.compile(r"_person_\d+(?:_\d+)?$", re.IGNORECASE)
MODEL_NAME = "yolov8s-seg.pt"
DETECT_CONF = 0.20
# High IoU threshold deliberately preserves strongly-overlapping people.
DETECT_IOU = 0.88
MAX_DETECTIONS = 100
AUTO_PADDING = 0.10
MIN_BOX_SIDE = 8
HANDLE_RADIUS = 6
MEMORY_FILENAME = ".human_crop_splitter_memory.json"
MEMORY_VERSION = 2

Box = tuple[int, int, int, int]


@dataclass
class PersonCrop:
    box: Box
    confidence: float = 1.0
    source: str = "manual"


@dataclass
class Candidate:
    path: Path
    auto_crops: list[PersonCrop]
    crops: list[PersonCrop] = field(default_factory=list)
    approved: bool = False

    def __post_init__(self):
        if not self.crops:
            self.crops = [PersonCrop(tuple(c.box), c.confidence, c.source) for c in self.auto_crops]


@dataclass(frozen=True)
class ScanRequest:
    folder: Path
    recursive: bool
    ignore_history: bool = False


# ------------------------------ folder memory ------------------------------

class FolderMemory:
    """Persistent per-folder history used to skip unchanged files on later runs."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / MEMORY_FILENAME
        self.data = {"version": MEMORY_VERSION, "entries": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if (isinstance(loaded, dict) and
                    loaded.get("version") == MEMORY_VERSION and
                    isinstance(loaded.get("entries"), dict)):
                self.data = loaded
        except Exception:
            # A damaged memory file should never prevent image processing.
            pass

    def _key(self, path: Path) -> str:
        try:
            rel = path.relative_to(self.folder).as_posix()
        except Exception:
            rel = path.name
        return rel.lower() if os.name == "nt" else rel

    @staticmethod
    def _signature(path: Path) -> dict:
        st = path.stat()
        return {"size": int(st.st_size), "mtime_ns": int(st.st_mtime_ns)}

    def lookup_unchanged(self, path: Path) -> dict | None:
        entry = self.data["entries"].get(self._key(path))
        if not entry:
            return None
        try:
            if entry.get("signature") == self._signature(path):
                return entry
        except OSError:
            pass
        return None

    def remember(self, path: Path, status: str, **extra) -> None:
        key = self._key(path)
        entry = self.data["entries"].get(key, {})
        try:
            entry["signature"] = self._signature(path)
        except OSError:
            # The source may already have been moved to the Recycle Bin. Preserve
            # its previous signature so its history is still meaningful.
            pass
        entry.update({
            "name": path.name,
            "status": status,
            "updated_at": time.time(),
        })
        entry.update(extra)
        self.data["entries"][key] = entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.path)

    def forget(self) -> None:
        self.data = {"version": MEMORY_VERSION, "entries": {}}
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            self.save()


def memory_status_text(entry: dict) -> str:
    status = entry.get("status", "seen")
    mapping = {
        "review_candidate": "already detected/review candidate",
        "review_rejected": "reviewed/rejected",
        "reviewed_no_crops": "reviewed with no crops",
        "saved_and_trashed": "already cropped and source trashed",
        "save_failed": "save previously failed",
        "scan_error": "scan error previously",
    }
    return mapping.get(status, status.replace("_", " "))


# ------------------------------ image I/O ------------------------------

def clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def open_image(path: Path) -> Image.Image:
    with Image.open(path) as opened:
        img = ImageOps.exif_transpose(opened)
        img.load()
        return img.copy()


def get_save_metadata(img: Image.Image) -> dict:
    metadata: dict = {}
    for key in ("exif", "icc_profile", "dpi"):
        value = img.info.get(key)
        if value:
            metadata[key] = value
    return metadata


def safe_output_extension(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".jpg", ".jpeg", ".jfif", ".png", ".webp", ".bmp", ".tif", ".tiff"}:
        return ext
    # Inputs can be any Pillow-decodable image. For formats we cannot reliably
    # preserve on write, use lossless PNG instead of failing.
    return ".png"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem}_{n}{path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def save_cropped_image(cropped: Image.Image, out_path: Path, metadata: dict) -> None:
    ext = out_path.suffix.lower()
    args: dict = {}
    output = cropped

    if ext in {".jpg", ".jpeg", ".jfif"}:
        if output.mode not in {"RGB", "L", "CMYK"}:
            output = output.convert("RGB")
        args.update(format="JPEG", quality=100, subsampling=0, optimize=True)
        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                args[key] = metadata[key]
    elif ext == ".png":
        args.update(format="PNG", compress_level=6, optimize=False)
        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                args[key] = metadata[key]
        if "transparency" in cropped.info:
            args["transparency"] = cropped.info["transparency"]
    elif ext == ".webp":
        if output.mode not in {"RGB", "RGBA"}:
            output = output.convert("RGBA" if "A" in output.getbands() else "RGB")
        args.update(format="WEBP", lossless=True, quality=100, method=6)
        for key in ("exif", "icc_profile"):
            if key in metadata:
                args[key] = metadata[key]
    elif ext in {".tif", ".tiff"}:
        args.update(format="TIFF", compression="tiff_deflate")
        for key in ("exif", "icc_profile", "dpi"):
            if key in metadata:
                args[key] = metadata[key]
    elif ext == ".bmp":
        if output.mode not in {"RGB", "RGBA", "L", "P"}:
            output = output.convert("RGB")
        args["format"] = "BMP"
    else:
        raise ValueError(f"Unsupported output format: {ext}")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp = out_path.with_name(f".{out_path.stem}.temporary{out_path.suffix}")
    try:
        output.save(temp, **args)
        os.replace(temp, out_path)
    finally:
        if temp.exists():
            try:
                temp.unlink()
            except OSError:
                pass


# ------------------------------ detection ------------------------------

def expand_box(box: Box, width: int, height: int, ratio: float = AUTO_PADDING) -> Box:
    x0, y0, x1, y1 = box
    bw = max(1, x1 - x0)
    bh = max(1, y1 - y0)
    px = int(round(bw * ratio))
    py = int(round(bh * ratio))
    return (
        clamp(x0 - px, 0, width),
        clamp(y0 - py, 0, height),
        clamp(x1 + px, 0, width),
        clamp(y1 + py, 0, height),
    )


def box_iou(a: Box, b: Box) -> float:
    ix0, iy0 = max(a[0], b[0]), max(a[1], b[1])
    ix1, iy1 = min(a[2], b[2]), min(a[3], b[3])
    iw, ih = max(0, ix1 - ix0), max(0, iy1 - iy0)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    aa = max(1, (a[2] - a[0]) * (a[3] - a[1]))
    ab = max(1, (b[2] - b[0]) * (b[3] - b[1]))
    return inter / float(aa + ab - inter)


def dedupe_near_identical(crops: list[PersonCrop]) -> list[PersonCrop]:
    """Remove only near-duplicate detections; legitimate overlapping people stay."""
    kept: list[PersonCrop] = []
    for crop in sorted(crops, key=lambda c: c.confidence, reverse=True):
        duplicate = False
        for old in kept:
            if box_iou(crop.box, old.box) >= 0.96:
                duplicate = True
                break
        if not duplicate:
            kept.append(crop)
    kept.sort(key=lambda c: ((c.box[0] + c.box[2]) / 2, (c.box[1] + c.box[3]) / 2))
    return kept


def detect_people(model: YOLO, img: Image.Image) -> list[PersonCrop]:
    model_image = np.array(img.convert("RGB"))
    results = model.predict(
        source=model_image,
        conf=DETECT_CONF,
        iou=DETECT_IOU,
        classes=[0],
        max_det=MAX_DETECTIONS,
        verbose=False,
    )
    if not results:
        return []

    result = results[0]
    boxes = result.boxes
    if boxes is None or len(boxes) == 0:
        return []

    polygons = result.masks.xy if result.masks is not None else []
    found: list[PersonCrop] = []

    for i in range(len(boxes)):
        cls_val = boxes.cls[i]
        cls_id = int(cls_val.item()) if hasattr(cls_val, "item") else int(cls_val)
        if cls_id != 0:
            continue

        conf_val = boxes.conf[i] if boxes.conf is not None else 1.0
        confidence = float(conf_val.item()) if hasattr(conf_val, "item") else float(conf_val)

        source = "box"
        bbox: Box | None = None

        # Match the supplied working script's strategy: prefer segmentation
        # geometry, but fall back to detector boxes when a mask is unavailable.
        if i < len(polygons):
            poly = polygons[i]
            if poly is not None and len(poly) > 0:
                xs = poly[:, 0]
                ys = poly[:, 1]
                bbox = (
                    int(np.floor(np.min(xs))),
                    int(np.floor(np.min(ys))),
                    int(np.ceil(np.max(xs))),
                    int(np.ceil(np.max(ys))),
                )
                source = "mask"

        if bbox is None:
            x0, y0, x1, y1 = boxes.xyxy[i].tolist()
            bbox = (int(np.floor(x0)), int(np.floor(y0)), int(np.ceil(x1)), int(np.ceil(y1)))

        bbox = expand_box(bbox, img.width, img.height, AUTO_PADDING)
        if bbox[2] - bbox[0] >= MIN_BOX_SIDE and bbox[3] - bbox[1] >= MIN_BOX_SIDE:
            found.append(PersonCrop(bbox, confidence, source))

    return dedupe_near_identical(found)


def collect_image_paths(root: Path, recursive: bool) -> list[Path]:
    iterator = root.rglob("*") if recursive else root.glob("*")
    paths = []
    for path in iterator:
        if not path.is_file():
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            continue
        if GENERATED_RE.search(path.stem):
            continue
        paths.append(path)
    paths.sort(key=lambda p: (str(p.parent).lower(), p.name.lower()))
    return paths


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


def scan_folder_terminal(request: ScanRequest) -> tuple[list[Candidate], str]:
    ensure_console()
    memory = FolderMemory(request.folder)
    paths = collect_image_paths(request.folder, request.recursive)
    total = len(paths)
    width = len(str(max(1, total)))
    candidates: list[Candidate] = []
    zero_detected = 0
    one_detected = 0
    multi_detected = 0
    failures = 0
    skipped = 0
    scanned = 0
    started = time.perf_counter()

    print("\n" + "=" * 86, flush=True)
    print("HUMAN CROP DETECTOR - TERMINAL SCAN", flush=True)
    print(f"Folder: {request.folder}", flush=True)
    print(f"Recursive: {'yes' if request.recursive else 'no'}", flush=True)
    print(f"Images found: {total}", flush=True)
    if request.ignore_history:
        print(f"Memory: {memory.path.name} (IGNORED FOR THIS RUN ONLY; saved history is not deleted)", flush=True)
    else:
        print(f"Memory: {memory.path.name} (unchanged previously-seen images are skipped)", flush=True)
    print(f"Model: {MODEL_NAME} | confidence {DETECT_CONF:.2f} | overlap-friendly IoU {DETECT_IOU:.2f}", flush=True)
    print("NO person-count restriction: 0, 1, or many detections all go to manual review.", flush=True)
    print("The review UI will reopen only after every new/modified image has been checked.", flush=True)
    print("=" * 86, flush=True)

    # Do not load YOLO at all if every source image is already remembered, unless
    # the user explicitly chose to ignore history for this run. That option does
    # NOT delete the memory file; it simply bypasses lookups once.
    unseen: list[Path] = []
    cached_for_path: dict[Path, dict] = {}
    for path in paths:
        cached = None if request.ignore_history else memory.lookup_unchanged(path)
        if cached is None:
            unseen.append(path)
        else:
            cached_for_path[path] = cached

    model = None
    if unseen:
        print(f"Loading {MODEL_NAME} for {len(unseen)} new/modified image(s) ...", end=" ", flush=True)
        model = YOLO(MODEL_NAME)
        print("ready", flush=True)
    else:
        print("No new or modified images. YOLO model load skipped.", flush=True)

    for i, path in enumerate(paths, 1):
        try:
            rel = path.relative_to(request.folder)
        except Exception:
            rel = path.name
        print(f"[{i:>{width}}/{total}] {rel}", end=" ... ", flush=True)

        cached = cached_for_path.get(path)
        if cached is not None:
            skipped += 1
            print(f"SKIP remembered: {memory_status_text(cached)}", flush=True)
            continue

        scanned += 1
        try:
            img = open_image(path)
            crops = detect_people(model, img)
            n = len(crops)
            candidates.append(Candidate(path, crops))

            overlap_pairs = 0
            for a in range(n):
                for b in range(a + 1, n):
                    if box_iou(crops[a].box, crops[b].box) > 0:
                        overlap_pairs += 1

            memory.remember(
                path,
                "review_candidate",
                people=n,
                overlap_pairs=overlap_pairs,
                boxes=[list(c.box) for c in crops],
            )

            if n == 0:
                zero_detected += 1
                print("0 detected people -> REVIEW anyway; manual selections allowed", flush=True)
            elif n == 1:
                one_detected += 1
                print("1 detected person -> REVIEW", flush=True)
            else:
                multi_detected += 1
                extra = f" | {overlap_pairs} overlapping pair(s)" if overlap_pairs else ""
                print(f"{n} detected people -> REVIEW{extra}", flush=True)
        except Exception as exc:
            failures += 1
            memory.remember(path, "scan_error", error=f"{type(exc).__name__}: {exc}")
            print(f"ERROR: {type(exc).__name__}: {exc}", flush=True)

    memory.save()
    elapsed = time.perf_counter() - started
    scan_label = "image(s) with history ignored" if request.ignore_history else "new/modified image(s)"
    msg = (
        f"Images for review: {len(candidates)}. "
        f"Scanned {scanned} {scan_label}; skipped {skipped} remembered unchanged image(s). "
        f"Detections: zero={zero_detected}, one={one_detected}, multiple={multi_detected}. "
        f"Failures: {failures}. Scan time: {elapsed:.1f}s."
    )
    print("-" * 86, flush=True)
    print(msg, flush=True)
    print("Opening manual review UI...", flush=True)
    print("=" * 86 + "\n", flush=True)
    return candidates, msg


# ------------------------------ initial folder chooser ------------------------------

class FolderChooser(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Human Cropper")
        self.geometry("800x210")
        self.resizable(True, False)
        self.folder = tk.StringVar()
        self.recursive = tk.BooleanVar(value=False)
        self.request: ScanRequest | None = None

        frame = ttk.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        ttk.Label(
            frame,
            text="Choose an image directory. The GUI will close completely while detection runs in the terminal.",
            wraplength=700,
        ).pack(anchor="w", pady=(0, 10))

        row = ttk.Frame(frame)
        row.pack(fill="x")
        ttk.Entry(row, textvariable=self.folder).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse…", command=self.browse).pack(side="left", padx=(8, 0))

        opts = ttk.Frame(frame)
        opts.pack(fill="x", pady=12)
        ttk.Checkbutton(opts, text="Include subfolders", variable=self.recursive).pack(side="left")
        ttk.Button(opts, text="Forget Folder Memory", command=self.forget_memory).pack(side="right", padx=(8, 0))
        ttk.Button(opts, text="Ignore History This Run", command=self.start_ignore_history).pack(side="right", padx=(8, 0))
        ttk.Button(opts, text="Start Detection", command=self.start).pack(side="right")

        ttk.Label(
            frame,
            text="Start Detection uses history. Ignore History This Run rescans everything once without deleting that history.",
            foreground="#666666",
        ).pack(anchor="w")

    def browse(self):
        chosen = filedialog.askdirectory(title="Choose directory containing images")
        if chosen:
            self.folder.set(chosen)
            self.start()

    def forget_memory(self):
        folder = Path(self.folder.get().strip())
        if not folder.is_dir():
            messagebox.showerror("Invalid folder", "Choose a valid directory first.")
            return
        memory = FolderMemory(folder)
        count = len(memory.data.get("entries", {}))
        if not messagebox.askyesno(
            "Forget folder memory",
            f"Forget {count} remembered image(s) for this folder?\n\n"
            "The next run will inspect all eligible images again.",
        ):
            return
        memory.forget()
        messagebox.showinfo("Memory cleared", "Folder memory was cleared. The next scan will inspect everything again.")

    def _start_with_history_mode(self, ignore_history: bool):
        folder = Path(self.folder.get().strip())
        if not folder.is_dir():
            messagebox.showerror("Invalid folder", "Choose a valid directory first.")
            return
        self.request = ScanRequest(folder, bool(self.recursive.get()), ignore_history=ignore_history)
        self.quit()
        self.destroy()

    def start(self):
        self._start_with_history_mode(False)

    def start_ignore_history(self):
        # This button is usable directly even before Browse: choose a folder if
        # needed, then bypass history for this scan without deleting it.
        folder = Path(self.folder.get().strip())
        if not folder.is_dir():
            chosen = filedialog.askdirectory(title="Choose directory to rescan while ignoring history")
            if not chosen:
                return
            self.folder.set(chosen)
        self._start_with_history_mode(True)


# ------------------------------ review UI ------------------------------

class ReviewApp(tk.Tk):
    def __init__(self, folder: Path, candidates: list[Candidate], scan_summary: str):
        super().__init__()
        self.title("Review Human Crops")
        self.geometry("1450x900")
        self.minsize(1050, 700)
        self.folder = folder
        self.candidates = candidates
        self.scan_summary = scan_summary
        self.index = 0
        self.current_image: Image.Image | None = None
        self.preview_image: Image.Image | None = None
        self.preview_tk: ImageTk.PhotoImage | None = None
        self.crop_tks: list[ImageTk.PhotoImage] = []
        self.selected = 0
        self.scale = 1.0
        self.ox = 0.0
        self.oy = 0.0
        self.drag_mode: str | None = None
        self.drag_start_img = (0.0, 0.0)
        self.drag_start_box: Box | None = None
        self.add_mode = False
        self.add_start: tuple[float, float] | None = None
        self.temp_add: tuple[float, float] | None = None
        self.action: str | None = None

        self.file_var = tk.StringVar()
        self.status_var = tk.StringVar(value=scan_summary)
        self.approval_var = tk.StringVar()
        self._build()
        self._bind_shortcuts()
        if candidates:
            self.load_index(0)
        else:
            self.after(100, self._nothing_found)

    def _build(self):
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        ttk.Button(top, text="← Previous (P / ←)", command=self.previous).pack(side="left")
        ttk.Label(top, textvariable=self.file_var, font=("Segoe UI", 10, "bold")).pack(side="left", padx=12)

        # Requested convenience: approval and next are directly adjacent.
        nav = ttk.Frame(top)
        nav.pack(side="right")
        self.approve_btn = ttk.Button(nav, text="✓ Approve This Image (A)", command=self.approve_current)
        self.approve_btn.pack(side="left")
        ttk.Button(nav, text="Next (N / →)  ›", command=self.next_image).pack(side="left", padx=(5, 0))

        tools = ttk.Frame(self, padding=(8, 0, 8, 8))
        tools.pack(fill="x")
        ttk.Button(tools, text="New Selection (Insert)", command=self.begin_add).pack(side="left")
        ttk.Button(tools, text="Delete Selection (Del)", command=self.delete_selected).pack(side="left", padx=5)
        ttk.Button(tools, text="Reset Auto Crops (R)", command=self.reset_auto).pack(side="left", padx=5)
        ttk.Button(tools, text="Unapprove", command=self.unapprove_current).pack(side="left", padx=(12, 5))
        ttk.Label(tools, textvariable=self.approval_var).pack(side="left", padx=12)
        ttk.Button(tools, text="Save All Approved…", command=self.finish_and_save).pack(side="right")

        panes = ttk.Panedwindow(self, orient="horizontal")
        panes.pack(fill="both", expand=True, padx=8)
        left = ttk.Labelframe(panes, text="Original — Paint-like crop selections (overlap allowed)", padding=5)
        right = ttk.Labelframe(panes, text="Independent person outputs", padding=5)
        panes.add(left, weight=4)
        panes.add(right, weight=2)

        self.canvas = tk.Canvas(left, background="#202020", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self.redraw())
        self.canvas.bind("<ButtonPress-1>", self.mouse_down)
        self.canvas.bind("<B1-Motion>", self.mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.mouse_up)

        self.crop_canvas = tk.Canvas(right, highlightthickness=0)
        scroll = ttk.Scrollbar(right, orient="vertical", command=self.crop_canvas.yview)
        self.crop_inner = ttk.Frame(self.crop_canvas)
        self.crop_window = self.crop_canvas.create_window((0, 0), window=self.crop_inner, anchor="nw")
        self.crop_canvas.configure(yscrollcommand=scroll.set)
        self.crop_canvas.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.crop_inner.bind("<Configure>", lambda e: self.crop_canvas.configure(scrollregion=self.crop_canvas.bbox("all")))
        self.crop_canvas.bind("<Configure>", lambda e: self.crop_canvas.itemconfigure(self.crop_window, width=e.width))

        # Mouse-wheel scrolling is active only while the pointer is over the
        # independent-output pane (including its dynamically-created cards).
        self.bind_all("<MouseWheel>", self._crop_mousewheel, add="+")
        self.bind_all("<Button-4>", self._crop_mousewheel_linux, add="+")
        self.bind_all("<Button-5>", self._crop_mousewheel_linux, add="+")

        bottom = ttk.Frame(self, padding=8)
        bottom.pack(fill="x")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="left", fill="x", expand=True)
        ttk.Label(
            bottom,
            text="A = approve   N/→ = next   P/← = previous   Del = delete box   Insert = add box   R = reset",
            foreground="#666666",
        ).pack(side="right")

    def _widget_is_in_crop_pane(self, widget) -> bool:
        current = widget
        while current is not None:
            if current in (self.crop_canvas, self.crop_inner):
                return True
            try:
                parent_name = current.winfo_parent()
                if not parent_name:
                    break
                current = current._nametowidget(parent_name)
            except Exception:
                break
        return False

    def _pointer_over_crop_pane(self, event) -> bool:
        try:
            widget = self.winfo_containing(event.x_root, event.y_root)
        except Exception:
            widget = None
        return widget is not None and self._widget_is_in_crop_pane(widget)

    def _crop_mousewheel(self, event):
        if not self._pointer_over_crop_pane(event):
            return None
        delta = int(getattr(event, "delta", 0))
        if delta == 0:
            return "break"
        # Windows normally reports +/-120 per notch; trackpads may report
        # smaller values, so always move at least one unit.
        steps = max(1, abs(delta) // 120)
        direction = -1 if delta > 0 else 1
        self.crop_canvas.yview_scroll(direction * steps * 3, "units")
        return "break"

    def _crop_mousewheel_linux(self, event):
        if not self._pointer_over_crop_pane(event):
            return None
        direction = -1 if getattr(event, "num", None) == 4 else 1
        self.crop_canvas.yview_scroll(direction * 3, "units")
        return "break"

    def _bind_shortcuts(self):
        for seq in ("<a>", "<A>"):
            self.bind_all(seq, lambda e: self.approve_current())
        for seq in ("<n>", "<N>", "<Right>"):
            self.bind_all(seq, lambda e: self.next_image())
        for seq in ("<p>", "<P>", "<Left>"):
            self.bind_all(seq, lambda e: self.previous())
        self.bind_all("<Delete>", lambda e: self.delete_selected())
        self.bind_all("<Insert>", lambda e: self.begin_add())
        for seq in ("<r>", "<R>"):
            self.bind_all(seq, lambda e: self.reset_auto())
        self.bind_all("<Escape>", lambda e: self.cancel_edit())

    def _nothing_found(self):
        messagebox.showinfo("Nothing new to review", self.scan_summary)
        self.destroy()

    @property
    def item(self) -> Candidate:
        return self.candidates[self.index]

    def load_index(self, idx: int):
        if not self.candidates:
            return
        self.index = clamp(idx, 0, len(self.candidates) - 1)
        self.current_image = open_image(self.item.path)
        self.selected = min(self.selected, max(0, len(self.item.crops) - 1))
        self.add_mode = False
        self.drag_mode = None
        self.file_var.set(
            f"{self.index + 1}/{len(self.candidates)}  {self.item.path.name}  —  {len(self.item.crops)} crop selection(s)"
        )
        self._update_approval_label()
        self.redraw()
        self.render_crop_previews()

    def _update_approval_label(self):
        self.approval_var.set("APPROVED" if self.item.approved else "Not approved")

    def approve_current(self):
        if not self.candidates:
            return
        # No minimum or maximum crop count. Zero selections is a valid reviewed
        # state; it will be remembered but the source will be kept because no
        # replacement output exists to justify recycling it.
        self.item.approved = True
        self._update_approval_label()
        if self.item.crops:
            self.status_var.set(
                f"Approved {self.item.path.name} with {len(self.item.crops)} crop(s). Press N or → for the next image."
            )
        else:
            self.status_var.set(
                f"Approved {self.item.path.name} with 0 crops. It will be remembered, but the original will be kept."
            )

    def unapprove_current(self):
        if self.candidates:
            self.item.approved = False
            self._update_approval_label()

    def next_image(self):
        if self.index < len(self.candidates) - 1:
            self.load_index(self.index + 1)
        else:
            self.status_var.set("This is the last image. Use Save All Approved when you are finished reviewing.")

    def previous(self):
        if self.index > 0:
            self.load_index(self.index - 1)

    def reset_auto(self):
        self.item.crops = [PersonCrop(tuple(c.box), c.confidence, c.source) for c in self.item.auto_crops]
        self.selected = 0
        self.redraw()
        self.render_crop_previews()
        self.status_var.set("Restored the detector's original person selections.")

    def begin_add(self):
        self.add_mode = True
        self.add_start = None
        self.temp_add = None
        self.status_var.set("New Selection mode: drag a rectangle around a missed person. Esc cancels.")

    def cancel_edit(self):
        self.add_mode = False
        self.add_start = None
        self.temp_add = None
        self.drag_mode = None
        self.redraw()

    def delete_selected(self):
        if not self.item.crops:
            return
        if 0 <= self.selected < len(self.item.crops):
            del self.item.crops[self.selected]
            self.selected = min(self.selected, max(0, len(self.item.crops) - 1))
            self.redraw()
            self.render_crop_previews()

    # ----- coordinate helpers -----
    def _fit_preview(self):
        if self.current_image is None:
            return
        cw = max(100, self.canvas.winfo_width())
        ch = max(100, self.canvas.winfo_height())
        margin = 16
        self.scale = min((cw - 2 * margin) / self.current_image.width, (ch - 2 * margin) / self.current_image.height)
        self.scale = max(0.01, self.scale)
        pw = max(1, int(round(self.current_image.width * self.scale)))
        ph = max(1, int(round(self.current_image.height * self.scale)))
        self.preview_image = self.current_image.resize((pw, ph), Image.Resampling.LANCZOS)
        self.preview_tk = ImageTk.PhotoImage(self.preview_image)
        self.ox = (cw - pw) / 2
        self.oy = (ch - ph) / 2

    def image_to_canvas(self, x: float, y: float) -> tuple[float, float]:
        return self.ox + x * self.scale, self.oy + y * self.scale

    def canvas_to_image(self, x: float, y: float) -> tuple[float, float]:
        if self.current_image is None:
            return 0.0, 0.0
        ix = (x - self.ox) / self.scale
        iy = (y - self.oy) / self.scale
        return (
            max(0.0, min(float(self.current_image.width), ix)),
            max(0.0, min(float(self.current_image.height), iy)),
        )

    def redraw(self):
        if self.current_image is None or not self.canvas.winfo_exists():
            return
        self.canvas.delete("all")
        self._fit_preview()
        self.canvas.create_image(self.ox, self.oy, anchor="nw", image=self.preview_tk)

        for i, crop in enumerate(self.item.crops):
            self._draw_crop(i, crop.box, selected=(i == self.selected))

        if self.add_mode and self.add_start is not None and self.temp_add is not None:
            x0, y0 = self.add_start
            x1, y1 = self.temp_add
            cx0, cy0 = self.image_to_canvas(x0, y0)
            cx1, cy1 = self.image_to_canvas(x1, y1)
            self.canvas.create_rectangle(cx0, cy0, cx1, cy1, outline="#00e5ff", width=2, dash=(5, 3))

    def _draw_crop(self, index: int, box: Box, selected: bool):
        x0, y0, x1, y1 = box
        cx0, cy0 = self.image_to_canvas(x0, y0)
        cx1, cy1 = self.image_to_canvas(x1, y1)
        color = "#ffd60a" if selected else "#ff453a"
        width = 3 if selected else 2
        self.canvas.create_rectangle(cx0, cy0, cx1, cy1, outline=color, width=width, dash=(7, 4))
        self.canvas.create_rectangle(cx0 + 3, cy0 + 3, cx0 + 30, cy0 + 26, fill="#000000", outline=color)
        self.canvas.create_text(cx0 + 16, cy0 + 15, text=str(index + 1), fill="white", font=("Segoe UI", 10, "bold"))

        if selected:
            for _, (hx, hy) in self._handle_points(box).items():
                ccx, ccy = self.image_to_canvas(hx, hy)
                r = HANDLE_RADIUS
                self.canvas.create_rectangle(ccx - r, ccy - r, ccx + r, ccy + r, fill="white", outline="#111111")

    def _handle_points(self, box: Box) -> dict[str, tuple[float, float]]:
        x0, y0, x1, y1 = box
        mx, my = (x0 + x1) / 2, (y0 + y1) / 2
        return {
            "nw": (x0, y0), "n": (mx, y0), "ne": (x1, y0),
            "w": (x0, my),                         "e": (x1, my),
            "sw": (x0, y1), "s": (mx, y1), "se": (x1, y1),
        }

    def _hit_handle(self, ix: float, iy: float) -> str | None:
        if not self.item.crops or not (0 <= self.selected < len(self.item.crops)):
            return None
        threshold = max(8.0, (HANDLE_RADIUS + 4) / self.scale)
        for name, (hx, hy) in self._handle_points(self.item.crops[self.selected].box).items():
            if abs(ix - hx) <= threshold and abs(iy - hy) <= threshold:
                return name
        return None

    def _hit_boxes(self, ix: float, iy: float) -> list[int]:
        hits = []
        for i, crop in enumerate(self.item.crops):
            x0, y0, x1, y1 = crop.box
            if x0 <= ix <= x1 and y0 <= iy <= y1:
                hits.append(i)
        # For overlapping people, prefer the smaller/more-specific rectangle.
        hits.sort(key=lambda i: (self.item.crops[i].box[2] - self.item.crops[i].box[0]) * (self.item.crops[i].box[3] - self.item.crops[i].box[1]))
        return hits

    # ----- mouse editing -----
    def mouse_down(self, event):
        ix, iy = self.canvas_to_image(event.x, event.y)

        if self.add_mode:
            self.add_start = (ix, iy)
            self.temp_add = (ix, iy)
            return

        handle = self._hit_handle(ix, iy)
        if handle is not None:
            self.drag_mode = handle
            self.drag_start_img = (ix, iy)
            self.drag_start_box = self.item.crops[self.selected].box
            return

        hits = self._hit_boxes(ix, iy)
        if hits:
            # If the currently selected overlapping rectangle is under the pointer,
            # keep it; otherwise choose the smallest hit. Crop previews on the right
            # give a second exact way to select boxes when overlap is heavy.
            if self.selected not in hits:
                self.selected = hits[0]
                self.redraw()
                self.render_crop_previews()
            self.drag_mode = "move"
            self.drag_start_img = (ix, iy)
            self.drag_start_box = self.item.crops[self.selected].box
        else:
            self.drag_mode = None

    def mouse_move(self, event):
        ix, iy = self.canvas_to_image(event.x, event.y)
        if self.add_mode and self.add_start is not None:
            self.temp_add = (ix, iy)
            self.redraw()
            return

        if self.drag_mode is None or self.drag_start_box is None or self.current_image is None:
            return

        sx, sy = self.drag_start_img
        x0, y0, x1, y1 = self.drag_start_box
        dx, dy = ix - sx, iy - sy

        if self.drag_mode == "move":
            w, h = x1 - x0, y1 - y0
            nx0 = int(round(x0 + dx))
            ny0 = int(round(y0 + dy))
            nx0 = clamp(nx0, 0, max(0, self.current_image.width - w))
            ny0 = clamp(ny0, 0, max(0, self.current_image.height - h))
            new_box = (nx0, ny0, nx0 + w, ny0 + h)
        else:
            nx0, ny0, nx1, ny1 = x0, y0, x1, y1
            mode = self.drag_mode
            if "w" in mode:
                nx0 = int(round(ix))
            if "e" in mode:
                nx1 = int(round(ix))
            if "n" in mode:
                ny0 = int(round(iy))
            if "s" in mode:
                ny1 = int(round(iy))

            nx0 = clamp(nx0, 0, self.current_image.width - MIN_BOX_SIDE)
            nx1 = clamp(nx1, MIN_BOX_SIDE, self.current_image.width)
            ny0 = clamp(ny0, 0, self.current_image.height - MIN_BOX_SIDE)
            ny1 = clamp(ny1, MIN_BOX_SIDE, self.current_image.height)
            if nx1 - nx0 < MIN_BOX_SIDE:
                if "w" in mode:
                    nx0 = nx1 - MIN_BOX_SIDE
                else:
                    nx1 = nx0 + MIN_BOX_SIDE
            if ny1 - ny0 < MIN_BOX_SIDE:
                if "n" in mode:
                    ny0 = ny1 - MIN_BOX_SIDE
                else:
                    ny1 = ny0 + MIN_BOX_SIDE
            new_box = (nx0, ny0, nx1, ny1)

        self.item.crops[self.selected].box = new_box
        self.redraw()  # Do not regenerate crop thumbnails during live dragging.

    def mouse_up(self, event):
        if self.current_image is None:
            return
        ix, iy = self.canvas_to_image(event.x, event.y)

        if self.add_mode and self.add_start is not None:
            x0, y0 = self.add_start
            x1, y1 = ix, iy
            bx0, bx1 = sorted((int(round(x0)), int(round(x1))))
            by0, by1 = sorted((int(round(y0)), int(round(y1))))
            bx0, bx1 = clamp(bx0, 0, self.current_image.width), clamp(bx1, 0, self.current_image.width)
            by0, by1 = clamp(by0, 0, self.current_image.height), clamp(by1, 0, self.current_image.height)
            if bx1 - bx0 >= MIN_BOX_SIDE and by1 - by0 >= MIN_BOX_SIDE:
                self.item.crops.append(PersonCrop((bx0, by0, bx1, by1), 1.0, "manual"))
                self.selected = len(self.item.crops) - 1
            self.add_mode = False
            self.add_start = None
            self.temp_add = None

        self.drag_mode = None
        self.drag_start_box = None
        self.redraw()
        self.render_crop_previews()

    # ----- crop preview pane -----
    def render_crop_previews(self):
        if self.current_image is None:
            return
        for child in self.crop_inner.winfo_children():
            child.destroy()
        self.crop_tks.clear()

        for i, crop in enumerate(self.item.crops):
            x0, y0, x1, y1 = crop.box
            piece = self.current_image.crop((x0, y0, x1, y1))
            thumb = piece.copy()
            thumb.thumbnail((260, 240), Image.Resampling.LANCZOS)
            tkimg = ImageTk.PhotoImage(thumb)
            self.crop_tks.append(tkimg)

            card = ttk.Frame(self.crop_inner, padding=6, relief="ridge")
            card.pack(fill="x", padx=4, pady=4)
            image_label = ttk.Label(card, image=tkimg, cursor="hand2")
            image_label.pack()
            image_label.bind("<Button-1>", lambda e, n=i: self.select_crop(n))
            title = f"Person {i + 1}   {x1-x0}×{y1-y0}"
            if crop.source != "manual":
                title += f"   detector {crop.confidence:.0%}"
            ttk.Label(card, text=title).pack(anchor="w", pady=(4, 0))
            if i == self.selected:
                ttk.Label(card, text="SELECTED", foreground="#b8860b").pack(anchor="w")

        self.status_var.set(
            f"{len(self.item.crops)} independent crops. Overlap between rectangles is allowed and preserved. "
            "Drag inside a selection to move it; drag white handles to resize."
        )

    def select_crop(self, index: int):
        if 0 <= index < len(self.item.crops):
            self.selected = index
            self.redraw()
            self.render_crop_previews()

    def finish_and_save(self):
        approved = [c for c in self.candidates if c.approved]
        if not approved:
            messagebox.showinfo("Nothing approved", "Approve at least one image first.")
            return
        total_crops = sum(len(c.crops) for c in approved)
        if not messagebox.askyesno(
            "Save approved person crops",
            f"Save {total_crops} crop image(s) from {len(approved)} approved source image(s)?\n\n"
            "There is no crop-count restriction. Each crop is read independently from the full-resolution original, so overlaps are safe.\n"
            "Sources with 1+ crops go to the Windows Recycle Bin only after ALL their crops save successfully.\n"
            "An approved source with 0 crops is remembered but kept unchanged.",
        ):
            return
        self.action = "save"
        self.quit()
        self.destroy()


def save_approved_terminal(folder: Path, candidates: list[Candidate]) -> tuple[int, int, int, int, list[str]]:
    ensure_console()
    memory = FolderMemory(folder)
    approved = [c for c in candidates if c.approved]
    rejected = [c for c in candidates if not c.approved]

    # An image intentionally left unapproved is still considered reviewed/seen,
    # so it remains skipped on later runs unless its source file changes.
    for candidate in rejected:
        memory.remember(
            candidate.path, "review_rejected", people=len(candidate.crops),
            boxes=[list(c.box) for c in candidate.crops],
        )

    print("\n" + "=" * 86, flush=True)
    print("SAVING APPROVED HUMAN CROPS", flush=True)
    print("There is NO minimum or maximum number of crop selections.", flush=True)
    print("Each crop is taken independently from the original image; overlaps are intentionally allowed.", flush=True)
    print("An original is recycled ONLY after every one of its 1+ crops saves successfully.", flush=True)
    print("Approved images with 0 crops are remembered and left untouched.", flush=True)
    print("=" * 86, flush=True)
    created = 0
    trashed = 0
    kept_no_crops = 0
    failures: list[str] = []

    for i, candidate in enumerate(approved, 1):
        print(f"[{i}/{len(approved)}] {candidate.path.name} ...", flush=True)

        if len(candidate.crops) == 0:
            kept_no_crops += 1
            memory.remember(candidate.path, "reviewed_no_crops", people=0, boxes=[])
            print("    [0 CROPS] Reviewed/approved with no selections; original kept unchanged.", flush=True)
            continue

        outputs_this_source: list[Path] = []
        try:
            img = open_image(candidate.path)
            metadata = get_save_metadata(img)
            ext = safe_output_extension(candidate.path)
            for n, person in enumerate(candidate.crops, 1):
                x0, y0, x1, y1 = person.box
                x0 = clamp(int(x0), 0, img.width)
                x1 = clamp(int(x1), 0, img.width)
                y0 = clamp(int(y0), 0, img.height)
                y1 = clamp(int(y1), 0, img.height)
                if x1 - x0 < MIN_BOX_SIDE or y1 - y0 < MIN_BOX_SIDE:
                    raise RuntimeError(f"Crop {n} selection is too small")
                crop = img.crop((x0, y0, x1, y1))
                out = unique_path(candidate.path.with_name(f"{candidate.path.stem}_person_{n:02d}{ext}"))
                save_cropped_image(crop, out, metadata)
                outputs_this_source.append(out)
                print(f"    [OK] Crop {n}: {out.name}  ({x1-x0}x{y1-y0})", flush=True)

            # Every requested output now exists. Only now recycle the source.
            send2trash(str(candidate.path))
            trashed += 1
            created += len(outputs_this_source)
            memory.remember(
                candidate.path, "saved_and_trashed", people=len(candidate.crops),
                output_count=len(outputs_this_source),
                outputs=[p.name for p in outputs_this_source],
                boxes=[list(c.box) for c in candidate.crops],
            )
            print("    [RECYCLE BIN] Original source moved to trash", flush=True)
        except Exception as exc:
            # If anything fails before recycling, remove only outputs created
            # during this attempt so the source remains the authoritative copy.
            for out in outputs_this_source:
                try:
                    if out.exists():
                        out.unlink()
                except OSError:
                    pass
            failures.append(f"{candidate.path.name}: {exc}")
            memory.remember(
                candidate.path, "save_failed", people=len(candidate.crops),
                error=f"{type(exc).__name__}: {exc}",
                boxes=[list(c.box) for c in candidate.crops],
            )
            print(f"    [ERROR] {type(exc).__name__}: {exc}", flush=True)
            print("    [SAFE] Original source was NOT recycled; partial outputs from this attempt were removed.", flush=True)

    memory.save()
    print("-" * 86, flush=True)
    print(f"Created: {created} crop image(s)", flush=True)
    print(f"Original source images moved to Recycle Bin: {trashed}", flush=True)
    print(f"Approved with 0 crops and kept unchanged: {kept_no_crops}", flush=True)
    if failures:
        print(f"Failures: {len(failures)}", flush=True)
        for line in failures:
            print(f"  - {line}", flush=True)
    else:
        print("Failures: 0", flush=True)
    print(f"Output directory: {folder}", flush=True)
    print(f"Memory file: {memory.path}", flush=True)
    print("=" * 86 + "\n", flush=True)
    return created, len(approved), trashed, kept_no_crops, failures


def show_done(created: int, approved_count: int, trashed: int, kept_no_crops: int, failures: list[str]):
    root = tk.Tk()
    root.withdraw()
    summary = (
        f"Created {created} crop image(s) from {approved_count} approved source image(s).\n"
        f"Moved {trashed} successfully processed original(s) to the Windows Recycle Bin.\n"
        f"Kept {kept_no_crops} approved source image(s) unchanged because they had 0 crop selections."
    )
    if failures:
        messagebox.showwarning(
            "Finished with issues",
            summary + "\n\n" + "\n".join(failures[:10]),
        )
    else:
        messagebox.showinfo(
            "Finished",
            summary + "\n\nTheir history is remembered, and unchanged old images will be skipped on later runs.",
        )
    root.destroy()


def main():
    chooser = FolderChooser()
    chooser.mainloop()
    request = chooser.request
    if request is None:
        return

    # At this point the initial Tk window is fully destroyed. Scanning is terminal-only.
    candidates, summary = scan_folder_terminal(request)

    review = ReviewApp(request.folder, candidates, summary)
    review.mainloop()
    if review.action == "save":
        created, approved_count, trashed, kept_no_crops, failures = save_approved_terminal(request.folder, candidates)
        show_done(created, approved_count, trashed, kept_no_crops, failures)


if __name__ == "__main__":
    main()
