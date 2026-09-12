from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk
from send2trash import send2trash

# Optional HEIC/HEIF support. The app still works without this package.
try:
    from pillow_heif import register_heif_opener  # type: ignore

    register_heif_opener()
except ImportError:
    pass


SUPPORTED_PATTERNS = (
    "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff *.webp "
    "*.ico *.ppm *.pgm *.pbm *.heic *.heif"
)


@dataclass
class Piece:
    path: Path
    image: Image.Image
    x: int
    y: int
    canvas_id: int | None = None
    close_bg_id: int | None = None
    close_text_id: int | None = None
    tk_image: ImageTk.PhotoImage | None = field(default=None, repr=False)

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height


class PuzzleMerger(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Image Puzzle Merger")
        self.geometry("1250x820")
        self.minsize(850, 600)

        self.pieces: list[Piece] = []
        self.id_to_piece: dict[int, Piece] = {}
        self.close_id_to_piece: dict[int, Piece] = {}
        self.zoom = 1.0
        self.close_button_size = 18

        self.drag_piece: Piece | None = None
        self.drag_start_mouse = (0.0, 0.0)
        self.drag_start_piece = (0, 0)

        self.move_to_trash = tk.BooleanVar(value=True)
        self.status = tk.StringVar(
            value="Load images, then drag them together. Dropped pieces snap to the nearest image."
        )

        self._build_ui()
        self._bind_shortcuts()

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self, padding=8)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        ttk.Button(toolbar, text="Load Images", command=self.load_images).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(toolbar, text="Save Merged Image", command=self.save_merged).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Button(toolbar, text="Fit View", command=self.fit_view).pack(
            side=tk.LEFT, padx=(0, 6)
        )
        ttk.Button(toolbar, text="Zoom -", command=lambda: self.set_zoom(self.zoom / 1.25)).pack(
            side=tk.LEFT, padx=(0, 3)
        )
        ttk.Button(toolbar, text="Zoom +", command=lambda: self.set_zoom(self.zoom * 1.25)).pack(
            side=tk.LEFT, padx=(0, 12)
        )
        ttk.Button(toolbar, text="Restart", command=self.restart_workspace).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        ttk.Checkbutton(
            toolbar,
            text="Move source images to Recycle Bin after successful save",
            variable=self.move_to_trash,
        ).pack(side=tk.LEFT)

        canvas_frame = ttk.Frame(self)
        canvas_frame.pack(fill=tk.BOTH, expand=True)

        self.canvas = tk.Canvas(
            canvas_frame,
            background="#252525",
            highlightthickness=0,
            cursor="fleur",
        )
        xbar = ttk.Scrollbar(canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)
        ybar = ttk.Scrollbar(canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)
        self.canvas.configure(xscrollcommand=xbar.set, yscrollcommand=ybar.set)

        self.canvas.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")
        xbar.grid(row=1, column=0, sticky="ew")
        canvas_frame.rowconfigure(0, weight=1)
        canvas_frame.columnconfigure(0, weight=1)

        statusbar = ttk.Label(self, textvariable=self.status, anchor="w", padding=(8, 5))
        statusbar.pack(side=tk.BOTTOM, fill=tk.X)

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Button-3>", self.on_right_click)
        self.canvas.bind("<MouseWheel>", self.on_mousewheel)
        self.canvas.bind("<Button-4>", self.on_mousewheel_linux)
        self.canvas.bind("<Button-5>", self.on_mousewheel_linux)

    def _bind_shortcuts(self) -> None:
        self.bind("<Control-o>", lambda _e: self.load_images())
        self.bind("<Control-s>", lambda _e: self.save_merged())
        self.bind("<Control-0>", lambda _e: self.fit_view())
        self.bind("<Control-r>", lambda _e: self.restart_workspace())

    def load_images(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose image fragments",
            filetypes=[
                ("Images", SUPPORTED_PATTERNS),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("WebP", "*.webp"),
                ("TIFF", "*.tif *.tiff"),
                ("BMP", "*.bmp"),
                ("GIF", "*.gif"),
                ("HEIC/HEIF (optional pillow-heif)", "*.heic *.heif"),
                ("All files", "*.*"),
            ],
        )
        if not paths:
            return

        self.add_paths([Path(p) for p in paths])
        self.fit_view()

    def add_paths(self, paths: list[Path]) -> None:
        # Place newly loaded images in a loose grid so none are hidden behind another.
        if self.pieces:
            min_x, min_y, max_x, max_y = self.workspace_bounds()
            cursor_x = min_x
            cursor_y = max_y + 80
        else:
            cursor_x = 0
            cursor_y = 0

        row_height = 0
        row_limit = 2400
        loaded = 0
        errors: list[str] = []

        for path in paths:
            try:
                with Image.open(path) as opened:
                    # For animated formats, use the first frame.
                    try:
                        opened.seek(0)
                    except EOFError:
                        pass
                    image = opened.convert("RGBA").copy()
            except Exception as exc:
                errors.append(f"{path.name}: {exc}")
                continue

            if cursor_x > 0 and cursor_x + image.width > row_limit:
                cursor_x = 0
                cursor_y += row_height + 80
                row_height = 0

            piece = Piece(path=path.resolve(), image=image, x=cursor_x, y=cursor_y)
            self.pieces.append(piece)
            cursor_x += image.width + 80
            row_height = max(row_height, image.height)
            loaded += 1

        self.render_all()
        if loaded:
            self.status.set(f"Loaded {loaded} image(s). Drag a piece near another and release to snap.")
        if errors:
            messagebox.showwarning(
                "Some images could not be loaded",
                "\n".join(errors[:12]) + ("\n..." if len(errors) > 12 else ""),
            )

    def clear_workspace(self) -> None:
        self.pieces.clear()
        self.id_to_piece.clear()
        self.close_id_to_piece.clear()
        self.drag_piece = None
        self.drag_start_mouse = (0.0, 0.0)
        self.drag_start_piece = (0, 0)
        self.zoom = 1.0
        self.canvas.delete("all")
        self.canvas.configure(scrollregion=(0, 0, 1, 1))
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.status.set("Workspace cleared.")

    def restart_workspace(self) -> None:
        """Forget the current assembly without deleting or trashing source files."""
        self.clear_workspace()
        self.status.set("Restarted. Load images to begin a new assembly.")

    def default_output_filename(self) -> str:
        """Build name1+name2+etc.png from the image filenames in the layout."""
        stems = [piece.path.stem for piece in self.pieces]
        joined = "+".join(stems) if stems else "merged"

        # Keep the suggested filename valid on Windows while preserving '+' separators.
        invalid = '<>:"/\\|?*'
        translation = str.maketrans({char: "_" for char in invalid})
        joined = joined.translate(translation).strip(" .") or "merged"

        # Leave room for the extension and avoid impractically long filename components.
        if len(joined) > 220:
            joined = joined[:220].rstrip(" .+") or "merged"

        return f"{joined}.png"

    def set_zoom(self, new_zoom: float) -> None:
        self.zoom = max(0.05, min(4.0, float(new_zoom)))
        self.render_all()
        self.status.set(f"Zoom: {self.zoom * 100:.0f}%")

    def fit_view(self) -> None:
        if not self.pieces:
            return

        self.update_idletasks()
        min_x, min_y, max_x, max_y = self.workspace_bounds()
        width = max(1, max_x - min_x)
        height = max(1, max_y - min_y)
        view_w = max(250, self.canvas.winfo_width() - 100)
        view_h = max(250, self.canvas.winfo_height() - 100)

        self.zoom = max(0.05, min(2.0, view_w / width, view_h / height))
        self.render_all()
        self.canvas.xview_moveto(0)
        self.canvas.yview_moveto(0)
        self.status.set(f"Fit view: {self.zoom * 100:.0f}%")

    def render_all(self) -> None:
        self.canvas.delete("all")
        self.id_to_piece.clear()
        self.close_id_to_piece.clear()

        for piece in self.pieces:
            display_w = max(1, round(piece.width * self.zoom))
            display_h = max(1, round(piece.height * self.zoom))

            if display_w == piece.width and display_h == piece.height:
                display = piece.image
            else:
                display = piece.image.resize((display_w, display_h), Image.Resampling.LANCZOS)

            piece.tk_image = ImageTk.PhotoImage(display)
            piece.canvas_id = self.canvas.create_image(
                piece.x * self.zoom,
                piece.y * self.zoom,
                image=piece.tk_image,
                anchor="nw",
            )
            self.id_to_piece[piece.canvas_id] = piece
            self.create_close_button(piece)

        self.update_scrollregion()

    def create_close_button(self, piece: Piece) -> None:
        """Draw a small fixed-size × control inside the piece's top-right corner."""
        right = (piece.x + piece.width) * self.zoom
        top = piece.y * self.zoom
        size = self.close_button_size
        inset = 2
        x2 = right - inset
        y1 = top + inset
        x1 = x2 - size
        y2 = y1 + size

        piece.close_bg_id = self.canvas.create_rectangle(
            x1, y1, x2, y2,
            fill="#c62828",
            outline="#ffffff",
            width=1,
        )
        piece.close_text_id = self.canvas.create_text(
            (x1 + x2) / 2,
            (y1 + y2) / 2 - 1,
            text="×",
            fill="white",
            font=("Segoe UI", 12, "bold"),
        )
        self.close_id_to_piece[piece.close_bg_id] = piece
        self.close_id_to_piece[piece.close_text_id] = piece

    def update_piece_canvas_position(self, piece: Piece) -> None:
        """Move an image and its × control together after dragging/snapping."""
        if piece.canvas_id is not None:
            self.canvas.coords(piece.canvas_id, piece.x * self.zoom, piece.y * self.zoom)

        right = (piece.x + piece.width) * self.zoom
        top = piece.y * self.zoom
        size = self.close_button_size
        inset = 2
        x2 = right - inset
        y1 = top + inset
        x1 = x2 - size
        y2 = y1 + size

        if piece.close_bg_id is not None:
            self.canvas.coords(piece.close_bg_id, x1, y1, x2, y2)
        if piece.close_text_id is not None:
            self.canvas.coords(piece.close_text_id, (x1 + x2) / 2, (y1 + y2) / 2 - 1)

    def close_piece_under_pointer(self, event: tk.Event) -> Piece | None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x, y, x, y)
        for item_id in reversed(items):
            piece = self.close_id_to_piece.get(item_id)
            if piece is not None:
                return piece
        return None

    def remove_piece(self, piece: Piece) -> None:
        """Remove a piece from the current draft only; never touch its source file."""
        if piece not in self.pieces:
            return
        name = piece.path.name
        self.pieces.remove(piece)
        if self.drag_piece is piece:
            self.drag_piece = None
        self.render_all()
        self.status.set(f"Removed {name} from the current draft. Source file unchanged.")

    def update_scrollregion(self) -> None:
        if not self.pieces:
            self.canvas.configure(scrollregion=(0, 0, 1, 1))
            return

        min_x, min_y, max_x, max_y = self.workspace_bounds()
        pad = 120
        self.canvas.configure(
            scrollregion=(
                min_x * self.zoom - pad,
                min_y * self.zoom - pad,
                max_x * self.zoom + pad,
                max_y * self.zoom + pad,
            )
        )

    def workspace_bounds(self) -> tuple[int, int, int, int]:
        min_x = min(p.x for p in self.pieces)
        min_y = min(p.y for p in self.pieces)
        max_x = max(p.x + p.width for p in self.pieces)
        max_y = max(p.y + p.height for p in self.pieces)
        return min_x, min_y, max_x, max_y

    def piece_under_pointer(self, event: tk.Event) -> Piece | None:
        x = self.canvas.canvasx(event.x)
        y = self.canvas.canvasy(event.y)
        items = self.canvas.find_overlapping(x, y, x, y)
        for item_id in reversed(items):
            piece = self.id_to_piece.get(item_id)
            if piece is not None:
                return piece
        return None

    def on_press(self, event: tk.Event) -> None:
        close_piece = self.close_piece_under_pointer(event)
        if close_piece is not None:
            self.remove_piece(close_piece)
            return

        piece = self.piece_under_pointer(event)
        if piece is None:
            return

        self.drag_piece = piece
        self.drag_start_mouse = (
            self.canvas.canvasx(event.x),
            self.canvas.canvasy(event.y),
        )
        self.drag_start_piece = (piece.x, piece.y)

        # Last dragged piece becomes topmost for both display and export overlap order.
        self.pieces.remove(piece)
        self.pieces.append(piece)
        if piece.canvas_id is not None:
            self.canvas.tag_raise(piece.canvas_id)
        if piece.close_bg_id is not None:
            self.canvas.tag_raise(piece.close_bg_id)
        if piece.close_text_id is not None:
            self.canvas.tag_raise(piece.close_text_id)

        self.status.set(f"Dragging {piece.path.name}")

    def on_drag(self, event: tk.Event) -> None:
        piece = self.drag_piece
        if piece is None or piece.canvas_id is None:
            return

        mouse_x = self.canvas.canvasx(event.x)
        mouse_y = self.canvas.canvasy(event.y)
        dx = (mouse_x - self.drag_start_mouse[0]) / self.zoom
        dy = (mouse_y - self.drag_start_mouse[1]) / self.zoom

        piece.x = round(self.drag_start_piece[0] + dx)
        piece.y = round(self.drag_start_piece[1] + dy)
        self.update_piece_canvas_position(piece)
        self.update_scrollregion()

    def on_release(self, _event: tk.Event) -> None:
        piece = self.drag_piece
        self.drag_piece = None
        if piece is None:
            return

        others = [p for p in self.pieces if p is not piece]
        if not others:
            self.status.set("Only one image is loaded; add another image to use snapping.")
            return

        snapped_x, snapped_y, side, target = self.best_snap_to_any(piece, others)
        piece.x = snapped_x
        piece.y = snapped_y

        self.update_piece_canvas_position(piece)
        self.update_scrollregion()
        self.status.set(
            f"Snapped {piece.path.name} side-to-side on the {side} of {target.path.name}."
        )

    def best_snap_to_any(
        self, piece: Piece, others: list[Piece]
    ) -> tuple[int, int, str, Piece]:
        """Choose the best side-to-side snap against every nearby image.

        The old snapper only required a tiny amount of shared edge, which meant a sloppy
        drop could remain visibly offset.  Here every candidate first maximizes the length
        of the touching sides.  Among those full-edge-contact candidates, the position
        requiring the smallest correction from the user's drop wins.
        """
        dropped_x, dropped_y = piece.x, piece.y

        # (collision flag, correction distance, collision area, target gap,
        #  negative shared-edge length, x, y, side, target)
        scored: list[tuple[int, float, int, float, int, int, int, str, Piece]] = []

        for target in others:
            # For east/west placement, maximize vertical side contact.
            y_for_horizontal = self.position_for_max_side_overlap(
                dropped_y, piece.height, target.y, target.height
            )
            shared_vertical = self.interval_overlap_length(
                y_for_horizontal,
                y_for_horizontal + piece.height,
                target.y,
                target.y + target.height,
            )

            # For north/south placement, maximize horizontal side contact.
            x_for_vertical = self.position_for_max_side_overlap(
                dropped_x, piece.width, target.x, target.width
            )
            shared_horizontal = self.interval_overlap_length(
                x_for_vertical,
                x_for_vertical + piece.width,
                target.x,
                target.x + target.width,
            )

            candidates = [
                (
                    target.x + target.width,
                    y_for_horizontal,
                    "east",
                    shared_vertical,
                ),
                (
                    target.x - piece.width,
                    y_for_horizontal,
                    "west",
                    shared_vertical,
                ),
                (
                    x_for_vertical,
                    target.y + target.height,
                    "south",
                    shared_horizontal,
                ),
                (
                    x_for_vertical,
                    target.y - piece.height,
                    "north",
                    shared_horizontal,
                ),
            ]

            target_gap = self.rectangle_distance(piece, target)

            for x, y, side, shared_edge in candidates:
                collision_area = sum(
                    self.overlap_area_at(piece, x, y, other)
                    for other in self.pieces
                    if other is not piece and other is not target
                )
                correction = math.hypot(x - dropped_x, y - dropped_y)

                # Collision-free snaps beat colliding snaps.  Within that group, the
                # user's approximate drop direction/position is respected by choosing
                # the smallest correction.  Longer shared edges break close ties.
                scored.append(
                    (
                        0 if collision_area == 0 else 1,
                        correction,
                        collision_area,
                        target_gap,
                        -shared_edge,
                        x,
                        y,
                        side,
                        target,
                    )
                )

        scored.sort(key=lambda s: (s[0], s[1], s[2], s[3], s[4]))
        _, _, _, _, _, x, y, side, target = scored[0]
        return x, y, side, target

    @staticmethod
    def position_for_max_side_overlap(
        moving_pos: int,
        moving_size: int,
        target_pos: int,
        target_size: int,
    ) -> int:
        """Return the closest position that gives maximum possible side overlap.

        If the moving side is shorter, it is kept completely inside the target side.
        If it is longer, the target side is kept completely inside the moving side.
        In either case the entire shorter side touches, rather than allowing a tiny
        partial-edge connection.
        """
        if moving_size <= target_size:
            low = target_pos
            high = target_pos + target_size - moving_size
        else:
            low = target_pos + target_size - moving_size
            high = target_pos

        return min(max(moving_pos, low), high)

    @staticmethod
    def interval_overlap_length(a0: int, a1: int, b0: int, b1: int) -> int:
        return max(0, min(a1, b1) - max(a0, b0))

    @staticmethod
    def rectangle_distance(a: Piece, b: Piece) -> float:
        a_left, a_top = a.x, a.y
        a_right, a_bottom = a.x + a.width, a.y + a.height
        b_left, b_top = b.x, b.y
        b_right, b_bottom = b.x + b.width, b.y + b.height

        dx = max(b_left - a_right, a_left - b_right, 0)
        dy = max(b_top - a_bottom, a_top - b_bottom, 0)

        # If rectangles overlap, gap distance is zero. Add a tiny center-distance
        # tie-break so the visually nearest overlapping piece wins.
        center_ax = a_left + a.width / 2
        center_ay = a_top + a.height / 2
        center_bx = b_left + b.width / 2
        center_by = b_top + b.height / 2
        center_distance = math.hypot(center_ax - center_bx, center_ay - center_by)
        return math.hypot(dx, dy) + center_distance * 1e-6

    @staticmethod
    def overlap_area_at(piece: Piece, x: int, y: int, other: Piece) -> int:
        left = max(x, other.x)
        top = max(y, other.y)
        right = min(x + piece.width, other.x + other.width)
        bottom = min(y + piece.height, other.y + other.height)
        if right <= left or bottom <= top:
            return 0
        return (right - left) * (bottom - top)

    def on_right_click(self, event: tk.Event) -> None:
        piece = self.piece_under_pointer(event)
        if piece is None:
            return
        if messagebox.askyesno(
            "Remove from layout",
            f"Remove {piece.path.name} from this layout?\n\nThe file itself will not be deleted.",
        ):
            self.remove_piece(piece)

    def on_mousewheel(self, event: tk.Event) -> None:
        # Ctrl + wheel zooms. Plain wheel keeps normal canvas scrolling behavior.
        if event.state & 0x0004:
            factor = 1.1 if event.delta > 0 else 1 / 1.1
            self.set_zoom(self.zoom * factor)
            return
        self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    def on_mousewheel_linux(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(-1 if event.num == 4 else 1, "units")

    def save_merged(self) -> None:
        if not self.pieces:
            messagebox.showinfo("Nothing to save", "Load at least one image first.")
            return

        output = filedialog.asksaveasfilename(
            title="Save merged image",
            initialfile=self.default_output_filename(),
            defaultextension=".png",
            filetypes=[
                ("PNG image", "*.png"),
                ("JPEG image", "*.jpg *.jpeg"),
                ("WebP image", "*.webp"),
                ("TIFF image", "*.tif *.tiff"),
                ("BMP image", "*.bmp"),
            ],
        )
        if not output:
            return

        out_path = Path(output).resolve()
        source_paths = {p.path.resolve() for p in self.pieces}
        if out_path in source_paths:
            messagebox.showerror(
                "Choose a different filename",
                "The merged image cannot overwrite one of the source images, because source images may be moved to the Recycle Bin after saving.",
            )
            return

        min_x, min_y, max_x, max_y = self.workspace_bounds()
        out_w = max_x - min_x
        out_h = max_y - min_y

        if out_w <= 0 or out_h <= 0:
            messagebox.showerror("Invalid layout", "The current layout has invalid dimensions.")
            return

        # A sanity warning for accidentally gigantic layouts caused by dragging a piece far away.
        pixel_count = out_w * out_h
        if pixel_count > 250_000_000:
            proceed = messagebox.askyesno(
                "Very large output",
                f"The merged image will be {out_w:,} x {out_h:,} pixels "
                f"({pixel_count / 1_000_000:.1f} megapixels).\n\nSave anyway?",
            )
            if not proceed:
                return

        try:
            merged = Image.new("RGBA", (out_w, out_h), (0, 0, 0, 0))
            for piece in self.pieces:
                merged.alpha_composite(piece.image, (piece.x - min_x, piece.y - min_y))

            suffix = out_path.suffix.lower()
            if suffix in {".jpg", ".jpeg", ".bmp"}:
                flattened = Image.new("RGB", merged.size, "white")
                flattened.paste(merged, mask=merged.getchannel("A"))
                merged_to_save = flattened
            else:
                merged_to_save = merged

            merged_to_save.save(out_path)
        except Exception as exc:
            messagebox.showerror("Save failed", f"Could not save the merged image:\n\n{exc}")
            return

        moved: list[Path] = []
        trash_errors: list[str] = []

        if self.move_to_trash.get():
            for source in sorted(source_paths, key=lambda p: str(p).lower()):
                try:
                    if source.exists():
                        send2trash(str(source))
                        moved.append(source)
                except Exception as exc:
                    trash_errors.append(f"{source.name}: {exc}")

        details = [f"Saved:\n{out_path}"]
        if moved:
            details.append(f"Moved {len(moved)} source file(s) to the Recycle Bin.")
        elif self.move_to_trash.get():
            details.append("No source files were moved (they may already have been missing).")
        else:
            details.append("Source files were left in place.")

        if trash_errors:
            details.append(
                "Some source files could not be moved to the Recycle Bin:\n"
                + "\n".join(trash_errors[:12])
            )
            messagebox.showwarning("Saved, but cleanup was incomplete", "\n\n".join(details))
        else:
            messagebox.showinfo("Merge complete", "\n\n".join(details))

        # A completed save always ends the current drafting session.  Do not load
        # the merged output back into the editor; the next draft starts blank.
        self.clear_workspace()
        self.status.set(f"Saved {out_path.name}. Workspace reset and ready for a new draft.")


if __name__ == "__main__":
    app = PuzzleMerger()
    app.mainloop()
