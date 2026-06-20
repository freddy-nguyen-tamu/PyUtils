import re
from pathlib import Path

import cv2
import numpy as np

try:
    from PIL import Image
    PIL_AVAILABLE = True
except Exception:
    PIL_AVAILABLE = False


# =========================
# CONFIG
# =========================
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\lequyensinger"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\khongtuquynh889"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\karishmasharma22"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\sofiavergara"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\jennierubyjane"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram\for_everyoung10"
# FOLDER = r"C:\Users\qacer\Downloads\py\fb_photos"
# FOLDER = r"C:\Users\qacer\Downloads\Instagram"
FOLDER = r"C:\Users\qacer\Downloads\Instagram\sofiavergara"

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp", ".gif", ".heic", ".heif"
}
VIDEO_EXTS = {
    ".mp4", ".m4v", ".mov", ".avi", ".mkv", ".wmv", ".webm", ".mpeg", ".mpg", ".3gp"
}

WINDOW_NAME = "Review (Right=Keep, Left=Delete, X=Exit)"

DELETE_PREFIX = "to be deleted"
KEEP_PREFIX = "keep"

KEY_LEFT = 2424832
KEY_RIGHT = 2555904
KEY_ESC = 27
KEY_SPACE = 32


# =========================
# Helpers
# =========================
def natural_key(s: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


def list_media_files(folder: str):
    p = Path(folder)
    files = []

    # Search recursively in folder + all subfolders
    for f in p.rglob("*"):
        if not f.is_file():
            continue

        # Skip already processed files
        name_lower = f.name.lower()
        if name_lower.startswith(f"{KEEP_PREFIX} ") or name_lower.startswith(f"{DELETE_PREFIX} "):
            continue

        ext = f.suffix.lower()
        if ext in IMAGE_EXTS or ext in VIDEO_EXTS:
            files.append(f)

    # Sort by relative path + filename naturally
    files.sort(key=lambda x: natural_key(str(x.relative_to(p))))
    return files


def rename_with_prefix(path: Path, prefix: str):
    original_name = path.name
    new_name = f"{prefix} {original_name}"
    new_path = path.with_name(new_name)

    i = 1
    while new_path.exists():
        stem = path.stem
        suffix = path.suffix
        new_name = f"{prefix} {stem}_{i}{suffix}"
        new_path = path.with_name(new_name)
        i += 1

    path.rename(new_path)
    return new_path


def load_image_any(path: Path):
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)

    if img is not None:
        if len(img.shape) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif len(img.shape) == 3 and img.shape[2] == 4:
            img = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        return img

    if PIL_AVAILABLE:
        with Image.open(str(path)) as im:
            im = im.convert("RGB")
            arr = np.array(im)
            return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)

    raise ValueError(f"Could not read image: {path}")


def clamp_offset(state, img_w, img_h):
    if state.scale <= state.min_scale + 1e-9:
        state.offset[:] = (0.0, 0.0)
        return

    src_w = max(1, int(state.window_size[0] / state.scale))
    src_h = max(1, int(state.window_size[1] / state.scale))

    max_x = max(0, img_w - src_w)
    max_y = max(0, img_h - src_h)

    state.offset[0] = float(np.clip(state.offset[0], 0, max_x))
    state.offset[1] = float(np.clip(state.offset[1], 0, max_y))


# =========================
# Zoom Viewer
# =========================
def fit_to_screen(img_w, img_h, win_w, win_h):
    return min(win_w / img_w, win_h / img_h)


class ZoomPanState:
    def __init__(self):
        self.scale = 1.0
        self.min_scale = 0.1
        self.max_scale = 20.0

        self.offset = np.array([0.0, 0.0], dtype=np.float32)

        self.dragging = False
        self.last_mouse = None

        self.window_size = (1920, 1080)

        self.base_scale = 1.0
        self.base_offset = np.array([0.0, 0.0], dtype=np.float32)

        self.current_img_shape = None

    def set_base_fit(self, img_w, img_h):
        try:
            _, _, win_w, win_h = cv2.getWindowImageRect(WINDOW_NAME)
            if win_w > 0 and win_h > 0:
                self.window_size = (win_w, win_h)
        except Exception:
            pass

        win_w, win_h = self.window_size
        fit = fit_to_screen(img_w, img_h, win_w, win_h)

        self.base_scale = max(fit, 0.01)
        self.min_scale = self.base_scale
        self.base_offset[:] = (0.0, 0.0)
        self.current_img_shape = (img_h, img_w)

        self.reset()

    def update_window_size(self):
        try:
            _, _, win_w, win_h = cv2.getWindowImageRect(WINDOW_NAME)
            if win_w > 0 and win_h > 0:
                self.window_size = (win_w, win_h)
        except Exception:
            pass

    def reset(self):
        self.scale = float(self.base_scale)
        self.offset[:] = self.base_offset
        self.dragging = False
        self.last_mouse = None

    def zoom_at(self, mouse_x, mouse_y, factor):
        if self.current_img_shape is None:
            return

        img_h, img_w = self.current_img_shape
        old_scale = self.scale
        new_scale = float(np.clip(old_scale * factor, self.min_scale, self.max_scale))

        if abs(new_scale - old_scale) < 1e-12:
            return

        if old_scale <= self.min_scale + 1e-9:
            src_x = img_w / 2.0
            src_y = img_h / 2.0
        else:
            src_x = self.offset[0] + mouse_x / old_scale
            src_y = self.offset[1] + mouse_y / old_scale

        self.scale = new_scale

        if self.scale <= self.min_scale + 1e-9:
            self.offset[:] = (0.0, 0.0)
        else:
            self.offset[0] = src_x - mouse_x / self.scale
            self.offset[1] = src_y - mouse_y / self.scale
            clamp_offset(self, img_w, img_h)


def render_zoom_pan(img, state):
    state.update_window_size()

    win_w, win_h = state.window_size
    h, w = img.shape[:2]

    state.current_img_shape = (h, w)
    state.scale = float(np.clip(state.scale, state.min_scale, state.max_scale))

    if state.scale <= state.min_scale + 1e-9:
        disp_w = max(1, int(w * state.scale))
        disp_h = max(1, int(h * state.scale))

        resized = cv2.resize(img, (disp_w, disp_h))

        canvas = np.zeros((win_h, win_w, 3), dtype=np.uint8)

        x = max(0, (win_w - disp_w) // 2)
        y = max(0, (win_h - disp_h) // 2)

        y2 = min(win_h, y + disp_h)
        x2 = min(win_w, x + disp_w)

        canvas[y:y2, x:x2] = resized[: y2 - y, : x2 - x]

        state.offset[:] = (0.0, 0.0)
        return canvas

    src_w = max(1, int(win_w / state.scale))
    src_h = max(1, int(win_h / state.scale))

    max_x = max(0, w - src_w)
    max_y = max(0, h - src_h)

    x0 = int(np.clip(state.offset[0], 0, max_x))
    y0 = int(np.clip(state.offset[1], 0, max_y))

    roi = img[y0:y0 + src_h, x0:x0 + src_w]
    return cv2.resize(roi, (win_w, win_h))


def add_hud(frame, lines):
    y = 30
    for line in lines:
        cv2.putText(
            frame,
            line,
            (20, y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2,
            cv2.LINE_AA,
        )
        y += 32


def get_key(delay):
    return cv2.waitKeyEx(delay)


def on_mouse(event, x, y, flags, param):
    state = param
    if state.current_img_shape is None:
        return

    img_h, img_w = state.current_img_shape

    if event == cv2.EVENT_LBUTTONDOWN:
        state.dragging = True
        state.last_mouse = (x, y)

    elif event == cv2.EVENT_LBUTTONUP:
        state.dragging = False
        state.last_mouse = None

    elif event == cv2.EVENT_MOUSEMOVE and state.dragging and state.last_mouse is not None:
        if state.scale > state.min_scale + 1e-9:
            dx = x - state.last_mouse[0]
            dy = y - state.last_mouse[1]

            state.offset[0] -= dx / state.scale
            state.offset[1] -= dy / state.scale
            clamp_offset(state, img_w, img_h)

        state.last_mouse = (x, y)

    elif event == cv2.EVENT_MOUSEWHEEL:
        delta = cv2.getMouseWheelDelta(flags)
        if delta > 0:
            state.zoom_at(x, y, 1.20)
        elif delta < 0:
            state.zoom_at(x, y, 1 / 1.20)


# =========================
# Video Player
# =========================
def play_video(path: Path, state: ZoomPanState, index: int, total: int):
    cap = cv2.VideoCapture(str(path))

    if not cap.isOpened():
        raise ValueError(f"Could not open video: {path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if fps is None or fps <= 1 or fps > 240:
        fps = 30.0

    delay = max(1, int(1000 / fps))
    paused = False
    frame = None
    first_frame = True

    while True:
        if not paused or frame is None:
            ok, frame = cap.read()
            if not ok:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                ok, frame = cap.read()
                if not ok:
                    break

            if first_frame:
                state.set_base_fit(frame.shape[1], frame.shape[0])
                first_frame = False

        display = render_zoom_pan(frame, state)

        add_hud(display, [
            f"{path.relative_to(Path(FOLDER))} ({index + 1}/{total}) [VIDEO]",
            "Right Arrow = KEEP | Left Arrow = DELETE | ESC = Exit",
            "Space = Pause/Play | Mouse Wheel = Zoom | Drag = Pan | R = Reset",
        ])

        cv2.imshow(WINDOW_NAME, display)

        key = get_key(delay if not paused else 15)

        if key == KEY_SPACE:
            paused = not paused
        elif key == KEY_ESC:
            cap.release()
            return "exit"
        elif key == KEY_RIGHT:
            cap.release()
            return "keep"
        elif key == KEY_LEFT:
            cap.release()
            return "delete"
        elif key in (ord("r"), ord("R")):
            state.reset()


# =========================
# Main
# =========================
def main():
    files = list_media_files(FOLDER)

    if not files:
        print("No media files found.")
        return

    print(f"Found {len(files)} media files (including subfolders).")

    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.setWindowProperty(
        WINDOW_NAME,
        cv2.WND_PROP_FULLSCREEN,
        cv2.WINDOW_FULLSCREEN,
    )

    state = ZoomPanState()
    cv2.setMouseCallback(WINDOW_NAME, on_mouse, state)

    i = 0

    while i < len(files):
        path = files[i]

        if not path.exists():
            i += 1
            continue

        ext = path.suffix.lower()

        try:
            if ext in VIDEO_EXTS:
                action = play_video(path, state, i, len(files))

                if action == "exit":
                    cv2.destroyAllWindows()
                    return
                elif action == "keep":
                    newp = rename_with_prefix(path, KEEP_PREFIX)
                    files[i] = newp
                    i += 1
                    continue
                elif action == "delete":
                    newp = rename_with_prefix(path, DELETE_PREFIX)
                    files[i] = newp
                    i += 1
                    continue

            else:
                img = load_image_any(path)

        except Exception as e:
            print(e)
            i += 1
            continue

        state.set_base_fit(img.shape[1], img.shape[0])

        while True:
            frame = render_zoom_pan(img, state)

            add_hud(frame, [
                f"{path.relative_to(Path(FOLDER))} ({i + 1}/{len(files)}) [IMAGE]",
                "Right Arrow = KEEP | Left Arrow = DELETE | ESC = Exit",
                "Mouse Wheel = Zoom | Drag = Pan | R = Reset",
            ])

            cv2.imshow(WINDOW_NAME, frame)

            key = get_key(15)

            if key == KEY_RIGHT:
                newp = rename_with_prefix(path, KEEP_PREFIX)
                files[i] = newp
                i += 1
                break
            elif key == KEY_LEFT:
                newp = rename_with_prefix(path, DELETE_PREFIX)
                files[i] = newp
                i += 1
                break
            elif key == KEY_ESC:
                cv2.destroyAllWindows()
                return
            elif key in (ord("r"), ord("R")):
                state.reset()

    cv2.destroyAllWindows()


if __name__ == "__main__":
    main()