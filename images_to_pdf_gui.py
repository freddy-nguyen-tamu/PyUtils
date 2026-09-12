#!/usr/bin/env python3
"""
Images -> PDF desktop GUI.

Features:
- Choose multiple local image files at once.
- Drag/drop multiple image files at once.
- Preserves the exact initial order supplied by the OS/file picker.
- Thumbnail/page preview for every image.
- Drag rows to reorder pages.
- Move up/down, remove, and clear controls.
- Large preview of the selected page.
- Export all pages to one PDF.

Install:
    python -m pip install pillow tkinterdnd2

Run:
    python images_to_pdf_gui.py
"""

from __future__ import annotations

import os
import sys
import uuid
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError as exc:
    raise SystemExit(
        "Pillow is required.\n"
        "Install it with:\n"
        "  python -m pip install pillow tkinterdnd2"
    ) from exc

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    HAS_DND = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    HAS_DND = False


SUPPORTED_EXTS = {
    ".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff", ".gif"
}


def flatten_for_pdf(image: Image.Image) -> Image.Image:
    """Return an RGB image suitable for Pillow's PDF encoder."""
    image = ImageOps.exif_transpose(image)

    # Animated images are represented by their first frame.
    try:
        image.seek(0)
    except (AttributeError, EOFError):
        pass

    if image.mode in ("RGBA", "LA"):
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg

    if image.mode == "P" and "transparency" in image.info:
        rgba = image.convert("RGBA")
        bg = Image.new("RGB", rgba.size, "white")
        bg.paste(rgba, mask=rgba.getchannel("A"))
        return bg

    return image.convert("RGB")


class ImagesToPdfApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Images → PDF")
        self.root.geometry("1180x760")
        self.root.minsize(900, 600)

        # iid -> absolute file path
        self.paths: dict[str, str] = {}

        # Keep Tk PhotoImage objects alive.
        self.thumb_refs: dict[str, ImageTk.PhotoImage] = {}
        self.preview_ref: ImageTk.PhotoImage | None = None

        self.drag_iid: str | None = None
        self.preview_after_id: str | None = None

        self._build_ui()
        self._setup_dnd()

    def _build_ui(self):
        self.root.columnconfigure(0, weight=3)
        self.root.columnconfigure(1, weight=5)
        self.root.rowconfigure(1, weight=1)

        toolbar = ttk.Frame(self.root, padding=(10, 10, 10, 6))
        toolbar.grid(row=0, column=0, columnspan=2, sticky="ew")

        ttk.Button(
            toolbar, text="Add images…", command=self.choose_images
        ).pack(side="left")
        ttk.Button(
            toolbar, text="Move up", command=lambda: self.move_selected(-1)
        ).pack(side="left", padx=(8, 0))
        ttk.Button(
            toolbar, text="Move down", command=lambda: self.move_selected(1)
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar, text="Remove", command=self.remove_selected
        ).pack(side="left", padx=(6, 0))
        ttk.Button(
            toolbar, text="Clear", command=self.clear_all
        ).pack(side="left", padx=(6, 0))

        ttk.Button(
            toolbar, text="Export PDF…", command=self.export_pdf
        ).pack(side="right")

        # Left: ordered page list.
        left = ttk.Frame(self.root, padding=(10, 4, 5, 10))
        left.grid(row=1, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)

        self.drop_hint = ttk.Label(
            left,
            text=(
                "Drop multiple images here, or use Add images…\n"
                "Drag page rows to rearrange them."
            ),
            anchor="center",
            justify="center",
            padding=10,
        )
        self.drop_hint.grid(row=0, column=0, sticky="ew", pady=(0, 8))

        style = ttk.Style()
        style.configure("Pages.Treeview", rowheight=100)

        tree_frame = ttk.Frame(left)
        tree_frame.grid(row=1, column=0, sticky="nsew")
        tree_frame.columnconfigure(0, weight=1)
        tree_frame.rowconfigure(0, weight=1)

        self.tree = ttk.Treeview(
            tree_frame,
            columns=("page", "name"),
            show="tree headings",
            style="Pages.Treeview",
            selectmode="extended",
        )
        self.tree.heading("#0", text="Preview")
        self.tree.heading("page", text="Page")
        self.tree.heading("name", text="File")
        self.tree.column("#0", width=112, minwidth=100, stretch=False)
        self.tree.column("page", width=65, minwidth=55, anchor="center", stretch=False)
        self.tree.column("name", width=310, minwidth=150, anchor="w")

        ybar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=ybar.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        ybar.grid(row=0, column=1, sticky="ns")

        # Selection and drag-to-reorder.
        self.tree.bind("<<TreeviewSelect>>", self.on_selection_changed)
        self.tree.bind("<ButtonPress-1>", self.on_tree_press, add="+")
        self.tree.bind("<B1-Motion>", self.on_tree_drag, add="+")
        self.tree.bind("<ButtonRelease-1>", self.on_tree_release, add="+")

        # Right: selected page preview.
        right = ttk.Frame(self.root, padding=(5, 4, 10, 10))
        right.grid(row=1, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)

        self.preview_title = ttk.Label(
            right, text="Page preview", font=("", 12, "bold")
        )
        self.preview_title.grid(row=0, column=0, sticky="w", pady=(0, 8))

        preview_border = ttk.Frame(right, relief="solid", borderwidth=1)
        preview_border.grid(row=1, column=0, sticky="nsew")
        preview_border.columnconfigure(0, weight=1)
        preview_border.rowconfigure(0, weight=1)

        self.preview_label = ttk.Label(
            preview_border,
            text="Select a page to preview it",
            anchor="center",
            justify="center",
        )
        self.preview_label.grid(row=0, column=0, sticky="nsew", padx=12, pady=12)
        self.preview_label.bind("<Configure>", self.on_preview_resize)

        self.status = ttk.Label(
            self.root,
            text="Ready",
            anchor="w",
            padding=(10, 5),
            relief="sunken",
        )
        self.status.grid(row=2, column=0, columnspan=2, sticky="ew")

    def _setup_dnd(self):
        if not HAS_DND:
            self.drop_hint.configure(
                text=(
                    "Add multiple images with Add images…\n"
                    "Drag/drop is disabled until tkinterdnd2 is installed."
                )
            )
            return

        # Register both the hint and the tree as drop targets.
        for widget in (self.drop_hint, self.tree):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", self.on_drop)

    def choose_images(self):
        files = filedialog.askopenfilenames(
            parent=self.root,
            title="Choose images in page order",
            filetypes=[
                (
                    "Image files",
                    "*.jpg *.jpeg *.png *.webp *.bmp *.tif *.tiff *.gif",
                ),
                ("JPEG", "*.jpg *.jpeg"),
                ("PNG", "*.png"),
                ("WebP", "*.webp"),
                ("TIFF", "*.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )

        # IMPORTANT: intentionally do not sort this tuple.
        # We preserve the exact sequence returned by the native file picker.
        if files:
            self.add_files(list(files))

    def on_drop(self, event):
        try:
            # splitlist correctly handles paths containing spaces/braces.
            files = list(self.root.tk.splitlist(event.data))
        except Exception:
            files = [event.data]

        # IMPORTANT: intentionally do not sort.
        # We preserve the exact sequence supplied by the OS drag/drop payload.
        self.add_files(files)
        return "break"

    def add_files(self, files: list[str]):
        added = 0
        skipped = 0
        first_new_iid = None

        for raw in files:
            path = os.path.abspath(os.path.expanduser(str(raw)))

            if not os.path.isfile(path):
                skipped += 1
                continue

            if Path(path).suffix.lower() not in SUPPORTED_EXTS:
                skipped += 1
                continue

            try:
                with Image.open(path) as im:
                    thumb = ImageOps.exif_transpose(im.copy())
                    try:
                        thumb.seek(0)
                    except (AttributeError, EOFError):
                        pass
                    thumb.thumbnail((92, 92), Image.Resampling.LANCZOS)
                    if thumb.mode not in ("RGB", "RGBA"):
                        thumb = thumb.convert("RGBA")
                    photo = ImageTk.PhotoImage(thumb)
            except Exception:
                skipped += 1
                continue

            iid = uuid.uuid4().hex
            self.paths[iid] = path
            self.thumb_refs[iid] = photo

            self.tree.insert(
                "",
                "end",
                iid=iid,
                image=photo,
                values=("", os.path.basename(path)),
            )

            if first_new_iid is None:
                first_new_iid = iid

            added += 1

        self.renumber_pages()

        if first_new_iid:
            self.tree.selection_set(first_new_iid)
            self.tree.focus(first_new_iid)
            self.tree.see(first_new_iid)
            self.show_preview(first_new_iid)

        count = len(self.tree.get_children())
        msg = f"{count} page{'s' if count != 1 else ''} loaded"
        if added:
            msg += f" · added {added}"
        if skipped:
            msg += f" · skipped {skipped} unsupported/unreadable item(s)"
        self.status.configure(text=msg)

    def renumber_pages(self):
        for index, iid in enumerate(self.tree.get_children(), start=1):
            values = list(self.tree.item(iid, "values"))
            filename = values[1] if len(values) > 1 else os.path.basename(self.paths[iid])
            self.tree.item(iid, values=(index, filename))

    def on_selection_changed(self, _event=None):
        selection = self.tree.selection()
        if selection:
            self.show_preview(selection[0])

    def show_preview(self, iid: str | None):
        if not iid or iid not in self.paths:
            self.preview_ref = None
            self.preview_label.configure(image="", text="Select a page to preview it")
            self.preview_title.configure(text="Page preview")
            return

        path = self.paths[iid]

        try:
            with Image.open(path) as im:
                preview = ImageOps.exif_transpose(im.copy())
                try:
                    preview.seek(0)
                except (AttributeError, EOFError):
                    pass

                w = max(self.preview_label.winfo_width() - 30, 250)
                h = max(self.preview_label.winfo_height() - 30, 250)
                preview.thumbnail((w, h), Image.Resampling.LANCZOS)

                if preview.mode not in ("RGB", "RGBA"):
                    preview = preview.convert("RGBA")

                self.preview_ref = ImageTk.PhotoImage(preview)
                self.preview_label.configure(image=self.preview_ref, text="")

            page_num = self.tree.item(iid, "values")[0]
            self.preview_title.configure(
                text=f"Page {page_num} — {os.path.basename(path)}"
            )
        except Exception as exc:
            self.preview_ref = None
            self.preview_label.configure(
                image="", text=f"Could not preview this image:\n{exc}"
            )

    def on_preview_resize(self, _event=None):
        if self.preview_after_id:
            self.root.after_cancel(self.preview_after_id)
        self.preview_after_id = self.root.after(120, self._refresh_preview)

    def _refresh_preview(self):
        self.preview_after_id = None
        selection = self.tree.selection()
        if selection:
            self.show_preview(selection[0])

    def on_tree_press(self, event):
        iid = self.tree.identify_row(event.y)
        self.drag_iid = iid or None

    def on_tree_drag(self, event):
        if not self.drag_iid:
            return

        target = self.tree.identify_row(event.y)
        if not target or target == self.drag_iid:
            return

        target_bbox = self.tree.bbox(target)
        if not target_bbox:
            return

        _, y, _, height = target_bbox
        target_index = self.tree.index(target)
        current_index = self.tree.index(self.drag_iid)

        # Put the dragged row before/after the target depending on pointer position.
        desired_index = target_index + (1 if event.y > y + height / 2 else 0)

        # Treeview index changes after the moving row is removed.
        if current_index < desired_index:
            desired_index -= 1

        desired_index = max(0, min(desired_index, len(self.tree.get_children()) - 1))
        self.tree.move(self.drag_iid, "", desired_index)
        self.renumber_pages()
        self.tree.selection_set(self.drag_iid)
        self.tree.focus(self.drag_iid)

    def on_tree_release(self, _event=None):
        if self.drag_iid:
            iid = self.drag_iid
            self.drag_iid = None
            self.renumber_pages()
            self.show_preview(iid)

    def move_selected(self, delta: int):
        selected = list(self.tree.selection())
        if not selected:
            return

        children = list(self.tree.get_children())

        if delta < 0:
            # Moving up: process top to bottom.
            for iid in selected:
                idx = self.tree.index(iid)
                if idx > 0:
                    self.tree.move(iid, "", idx - 1)
        else:
            # Moving down: process bottom to top.
            for iid in reversed(selected):
                idx = self.tree.index(iid)
                if idx < len(children) - 1:
                    self.tree.move(iid, "", idx + 1)

        self.renumber_pages()
        focus = selected[0]
        self.tree.see(focus)
        self.show_preview(focus)

    def remove_selected(self):
        selected = list(self.tree.selection())
        if not selected:
            return

        for iid in selected:
            self.tree.delete(iid)
            self.paths.pop(iid, None)
            self.thumb_refs.pop(iid, None)

        self.renumber_pages()

        children = self.tree.get_children()
        if children:
            iid = children[0]
            self.tree.selection_set(iid)
            self.tree.focus(iid)
            self.show_preview(iid)
        else:
            self.show_preview(None)

        count = len(children)
        self.status.configure(
            text=f"{count} page{'s' if count != 1 else ''} loaded"
        )

    def clear_all(self):
        if not self.tree.get_children():
            return

        for iid in self.tree.get_children():
            self.tree.delete(iid)

        self.paths.clear()
        self.thumb_refs.clear()
        self.show_preview(None)
        self.status.configure(text="Ready")

    def export_pdf(self):
        order = list(self.tree.get_children())
        if not order:
            messagebox.showwarning(
                "No images",
                "Add at least one image before exporting.",
                parent=self.root,
            )
            return

        out_path = filedialog.asksaveasfilename(
            parent=self.root,
            title="Save PDF",
            defaultextension=".pdf",
            filetypes=[("PDF document", "*.pdf")],
            initialfile="images.pdf",
        )
        if not out_path:
            return

        pages: list[Image.Image] = []

        self.status.configure(text="Building PDF…")
        self.root.update_idletasks()

        try:
            for iid in order:
                path = self.paths[iid]
                with Image.open(path) as im:
                    page = flatten_for_pdf(im.copy())
                    pages.append(page)

            first, *rest = pages

            # 150 DPI keeps page dimensions reasonable while preserving image detail.
            first.save(
                out_path,
                "PDF",
                save_all=True,
                append_images=rest,
                resolution=150.0,
                quality=95,
            )

            self.status.configure(
                text=f"Saved {len(pages)} page(s) → {out_path}"
            )
            messagebox.showinfo(
                "PDF created",
                f"Created {len(pages)}-page PDF:\n\n{out_path}",
                parent=self.root,
            )

        except Exception as exc:
            self.status.configure(text="PDF export failed")
            messagebox.showerror(
                "Export failed",
                f"Could not create the PDF:\n\n{exc}",
                parent=self.root,
            )
        finally:
            for page in pages:
                try:
                    page.close()
                except Exception:
                    pass


def main():
    if HAS_DND:
        root = TkinterDnD.Tk()
    else:
        root = tk.Tk()

    app = ImagesToPdfApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
