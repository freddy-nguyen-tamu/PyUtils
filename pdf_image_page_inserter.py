#!/usr/bin/env python3
"""
PDF Image Page Inserter
-----------------------
Desktop GUI for inserting one or more local images as NEW pages inside an
existing PDF, using a visual thumbnail strip with "+" insertion buttons.

Required:
    pip install PyMuPDF Pillow

Optional, for drag-and-drop:
    pip install tkinterdnd2

Run:
    python pdf_image_page_inserter.py
"""

from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union

try:
    import fitz  # PyMuPDF
except ImportError:
    raise SystemExit(
        "Missing dependency: PyMuPDF\n"
        "Install it with:\n"
        "  pip install PyMuPDF Pillow tkinterdnd2"
    )

try:
    from PIL import Image, ImageOps, ImageTk
except ImportError:
    raise SystemExit(
        "Missing dependency: Pillow\n"
        "Install it with:\n"
        "  pip install PyMuPDF Pillow tkinterdnd2"
    )

import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

# tkinterdnd2 is optional. Browsing still works without it.
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_AVAILABLE = True
except ImportError:
    DND_FILES = None
    TkinterDnD = None
    DND_AVAILABLE = False


APP_TITLE = "PDF Image Page Inserter"
THUMB_MAX_W = 170
THUMB_MAX_H = 225
CARD_W = 205
CARD_H = 300
PLUS_W = 54

IMAGE_FILETYPES = (
    ("Image files", "*.png *.jpg *.jpeg *.webp *.bmp *.tif *.tiff"),
    ("PNG", "*.png"),
    ("JPEG", "*.jpg *.jpeg"),
    ("WebP", "*.webp"),
    ("BMP", "*.bmp"),
    ("TIFF", "*.tif *.tiff"),
    ("All files", "*.*"),
)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}


@dataclass(frozen=True)
class OriginalPage:
    index: int


@dataclass(frozen=True)
class ImagePage:
    path: str


PageItem = Union[OriginalPage, ImagePage]


class PDFImageInserterApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1180x680")
        self.root.minsize(760, 520)

        self.pdf_path: Optional[str] = None
        self.pdf_password: Optional[str] = None
        self.pdf_page_sizes: List[tuple[float, float]] = []
        self.items: List[PageItem] = []
        self.selected_gap: int = 0
        self.thumbnail_refs: List[ImageTk.PhotoImage] = []
        self.render_generation = 0

        self._setup_style()
        self._build_ui()
        self._setup_dnd()

    # ----------------------------- UI setup -----------------------------

    def _setup_style(self):
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure("Title.TLabel", font=("Segoe UI", 17, "bold"))
        style.configure("Heading.TLabel", font=("Segoe UI", 10, "bold"))
        style.configure("Muted.TLabel", foreground="#666666")
        style.configure("Primary.TButton", font=("Segoe UI", 10, "bold"), padding=(14, 9))
        style.configure("Normal.TButton", padding=(12, 8))
        style.configure("Small.TButton", padding=(8, 5))

    def _build_ui(self):
        outer = ttk.Frame(self.root, padding=16)
        outer.pack(fill="both", expand=True)

        # Top bar
        top = ttk.Frame(outer)
        top.pack(fill="x")

        title_area = ttk.Frame(top)
        title_area.pack(side="left", fill="x", expand=True)

        ttk.Label(title_area, text="Insert images as PDF pages", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            title_area,
            text="Choose a PDF, then use any + button to insert one or more images at that exact position.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 0))

        button_area = ttk.Frame(top)
        button_area.pack(side="right")

        self.open_btn = ttk.Button(
            button_area, text="Choose PDF", style="Normal.TButton", command=self.choose_pdf
        )
        self.open_btn.pack(side="left", padx=(0, 8))

        self.export_btn = ttk.Button(
            button_area, text="Export New PDF", style="Primary.TButton", command=self.export_pdf
        )
        self.export_btn.pack(side="left")
        self.export_btn.state(["disabled"])

        # Status / current file row
        info = ttk.Frame(outer)
        info.pack(fill="x", pady=(14, 10))

        self.file_var = tk.StringVar(value="No PDF selected")
        self.count_var = tk.StringVar(value="")
        ttk.Label(info, textvariable=self.file_var, style="Heading.TLabel").pack(side="left")
        ttk.Label(info, textvariable=self.count_var, style="Muted.TLabel").pack(side="right")

        # Main visualizer
        viewer_border = ttk.Frame(outer, relief="solid", borderwidth=1)
        viewer_border.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            viewer_border,
            highlightthickness=0,
            background="#f4f5f7",
        )
        self.hscroll = ttk.Scrollbar(viewer_border, orient="horizontal", command=self.canvas.xview)
        self.canvas.configure(xscrollcommand=self.hscroll.set)

        self.canvas.pack(fill="both", expand=True)
        self.hscroll.pack(fill="x")

        self.strip = tk.Frame(self.canvas, bg="#f4f5f7")
        self.strip_window = self.canvas.create_window((0, 0), window=self.strip, anchor="nw")

        self.strip.bind("<Configure>", self._on_strip_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.canvas.bind("<Shift-MouseWheel>", self._on_shift_wheel)
        self.canvas.bind("<MouseWheel>", self._on_mousewheel)
        self.canvas.bind("<Button-4>", lambda e: self.canvas.xview_scroll(-3, "units"))
        self.canvas.bind("<Button-5>", lambda e: self.canvas.xview_scroll(3, "units"))

        # Empty state content
        self.empty_state = ttk.Frame(self.strip, padding=28)
        self.empty_state.pack(padx=30, pady=70)
        ttk.Label(
            self.empty_state,
            text="Drop a PDF here" if DND_AVAILABLE else "Choose a PDF to begin",
            style="Title.TLabel",
        ).pack()
        ttk.Label(
            self.empty_state,
            text=(
                "You can also browse local files with the Choose PDF button."
                if DND_AVAILABLE
                else "Drag-and-drop is available if you install tkinterdnd2."
            ),
            style="Muted.TLabel",
        ).pack(pady=(7, 0))

        # Bottom instructions/status
        bottom = ttk.Frame(outer)
        bottom.pack(fill="x", pady=(10, 0))

        self.drop_hint_var = tk.StringVar(
            value=(
                "Tip: click a + to insert images there. You can also drop images after selecting a +."
                if DND_AVAILABLE
                else "Tip: click any + button to browse for one or more images."
            )
        )
        ttk.Label(bottom, textvariable=self.drop_hint_var, style="Muted.TLabel").pack(side="left")

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="right")

    def _setup_dnd(self):
        if not DND_AVAILABLE:
            return

        # Root-wide drop target: PDF opens; images insert at selected gap.
        try:
            self.root.drop_target_register(DND_FILES)
            self.root.dnd_bind("<<Drop>>", self._on_drop)
        except Exception:
            pass

    # ----------------------------- File loading -----------------------------

    def choose_pdf(self):
        path = filedialog.askopenfilename(
            title="Choose PDF",
            filetypes=(("PDF files", "*.pdf"), ("All files", "*.*")),
        )
        if path:
            self.load_pdf(path)

    def load_pdf(self, path: str):
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            messagebox.showerror("File not found", f"Could not find:\n{path}")
            return
        if Path(path).suffix.lower() != ".pdf":
            messagebox.showerror("Not a PDF", "Please choose a .pdf file.")
            return

        try:
            doc = fitz.open(path)
        except Exception as exc:
            messagebox.showerror("Cannot open PDF", str(exc))
            return

        password = None
        if doc.needs_pass:
            password = simpledialog.askstring(
                "Password required",
                "This PDF is password-protected. Enter the password:",
                show="*",
                parent=self.root,
            )
            if not password:
                doc.close()
                return
            if not doc.authenticate(password):
                doc.close()
                messagebox.showerror("Wrong password", "The PDF password was not accepted.")
                return

        if doc.page_count == 0:
            doc.close()
            messagebox.showerror("Empty PDF", "The selected PDF contains no pages.")
            return

        try:
            sizes = [(float(doc[i].rect.width), float(doc[i].rect.height)) for i in range(doc.page_count)]
            page_count = doc.page_count
        finally:
            doc.close()

        self.pdf_path = path
        self.pdf_password = password
        self.pdf_page_sizes = sizes
        self.items = [OriginalPage(i) for i in range(page_count)]
        self.selected_gap = len(self.items)
        self.render_generation += 1

        self.file_var.set(os.path.basename(path))
        self.status_var.set("PDF loaded")
        self.export_btn.state(["!disabled"])
        self.refresh_visualizer()

    # ----------------------------- Visualizer -----------------------------

    def refresh_visualizer(self):
        self.render_generation += 1
        generation = self.render_generation

        for child in self.strip.winfo_children():
            child.destroy()
        self.thumbnail_refs.clear()

        if not self.pdf_path:
            self.empty_state = ttk.Frame(self.strip, padding=28)
            self.empty_state.pack(padx=30, pady=70)
            ttk.Label(
                self.empty_state,
                text="Drop a PDF here" if DND_AVAILABLE else "Choose a PDF to begin",
                style="Title.TLabel",
            ).pack()
            return

        inserted_count = sum(isinstance(x, ImagePage) for x in self.items)
        original_count = sum(isinstance(x, OriginalPage) for x in self.items)
        self.count_var.set(
            f"{original_count} original page{'s' if original_count != 1 else ''}"
            + (f" + {inserted_count} inserted" if inserted_count else "")
        )

        # Build placeholders immediately. Thumbnails are rendered incrementally.
        self._add_gap_widget(0)

        for pos, item in enumerate(self.items):
            if isinstance(item, OriginalPage):
                card, image_label = self._make_page_card(
                    final_pos=pos,
                    title=f"Page {pos + 1}",
                    subtitle=f"Original page {item.index + 1}",
                    removable=False,
                )
                self.root.after(
                    1 + pos * 6,
                    lambda p=pos, it=item, lbl=image_label, gen=generation:
                        self._render_original_thumbnail(p, it, lbl, gen)
                )
            else:
                card, image_label = self._make_page_card(
                    final_pos=pos,
                    title=f"Page {pos + 1}",
                    subtitle=os.path.basename(item.path),
                    removable=True,
                )
                self.root.after(
                    1 + pos * 6,
                    lambda p=pos, it=item, lbl=image_label, gen=generation:
                        self._render_image_thumbnail(p, it, lbl, gen)
                )

            card.pack(side="left", padx=8, pady=18)
            self._add_gap_widget(pos + 1)

        self.strip.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._scroll_selected_gap_into_view()

    def _make_page_card(self, final_pos: int, title: str, subtitle: str, removable: bool):
        card = tk.Frame(
            self.strip,
            width=CARD_W,
            height=CARD_H,
            bg="white",
            highlightbackground="#d5d8dc",
            highlightcolor="#d5d8dc",
            highlightthickness=1,
        )
        card.pack_propagate(False)

        preview = tk.Frame(card, bg="#eceff2", width=THUMB_MAX_W + 12, height=THUMB_MAX_H + 12)
        preview.pack(padx=11, pady=(11, 8))
        preview.pack_propagate(False)

        image_label = tk.Label(
            preview,
            text="Loading…",
            bg="#eceff2",
            fg="#70757a",
            font=("Segoe UI", 9),
        )
        image_label.pack(fill="both", expand=True)

        ttk.Label(card, text=title, style="Heading.TLabel").pack(anchor="w", padx=11)

        # Keep long image names from stretching the strip.
        short_subtitle = subtitle
        if len(short_subtitle) > 29:
            short_subtitle = short_subtitle[:26] + "..."
        ttk.Label(card, text=short_subtitle, style="Muted.TLabel").pack(anchor="w", padx=11, pady=(2, 0))

        if removable:
            ttk.Button(
                card,
                text="Remove",
                style="Small.TButton",
                command=lambda p=final_pos: self.remove_inserted_page(p),
            ).pack(anchor="e", padx=10, pady=(8, 8))

        return card, image_label

    def _add_gap_widget(self, gap_index: int):
        holder = tk.Frame(self.strip, width=PLUS_W, height=CARD_H, bg="#f4f5f7")
        holder.pack_propagate(False)
        holder.pack(side="left", padx=1, pady=18)

        line_top = tk.Frame(holder, width=2, height=90, bg="#c5c9ce")
        line_top.pack(pady=(30, 0))

        selected = gap_index == self.selected_gap
        btn = tk.Button(
            holder,
            text="+",
            font=("Segoe UI", 18, "bold"),
            width=2,
            height=1,
            relief="flat",
            cursor="hand2",
            bg="#1f6feb" if selected else "#ffffff",
            fg="#ffffff" if selected else "#1f6feb",
            activebackground="#185abc",
            activeforeground="#ffffff",
            highlightbackground="#1f6feb",
            highlightthickness=1,
            command=lambda g=gap_index: self.add_images_at_gap(g),
        )
        btn.pack(pady=3)

        line_bottom = tk.Frame(holder, width=2, height=90, bg="#c5c9ce")
        line_bottom.pack()

        tip = "Selected insertion point" if selected else "Insert image page(s) here"
        btn.configure(takefocus=True)
        self._attach_tooltip(btn, tip)

        # Allow image files to be dropped directly on a specific + when DnD is available.
        if DND_AVAILABLE:
            try:
                btn.drop_target_register(DND_FILES)
                btn.dnd_bind("<<Drop>>", lambda e, g=gap_index: self._on_gap_drop(e, g))
            except Exception:
                pass

    def _render_original_thumbnail(self, final_pos, item: OriginalPage, label: tk.Label, generation: int):
        if generation != self.render_generation or not self.pdf_path or not label.winfo_exists():
            return
        try:
            doc = fitz.open(self.pdf_path)
            if doc.needs_pass and self.pdf_password:
                doc.authenticate(self.pdf_password)
            page = doc[item.index]
            # About 120 dpi, then clamp with Pillow.
            pix = page.get_pixmap(matrix=fitz.Matrix(1.35, 1.35), alpha=False)
            mode = "RGB"
            img = Image.frombytes(mode, [pix.width, pix.height], pix.samples)
            doc.close()
            img.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.Resampling.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            self.thumbnail_refs.append(photo)
            if generation == self.render_generation and label.winfo_exists():
                label.configure(image=photo, text="", bg="white")
        except Exception as exc:
            if label.winfo_exists():
                label.configure(text=f"Preview error\n{exc}", image="")

    def _render_image_thumbnail(self, final_pos, item: ImagePage, label: tk.Label, generation: int):
        if generation != self.render_generation or not label.winfo_exists():
            return
        try:
            with Image.open(item.path) as im:
                im = ImageOps.exif_transpose(im)
                # Convert uncommon modes so ImageTk can display them reliably.
                if im.mode not in ("RGB", "RGBA", "L"):
                    im = im.convert("RGB")
                im.thumbnail((THUMB_MAX_W, THUMB_MAX_H), Image.Resampling.LANCZOS)
                preview = im.copy()
            photo = ImageTk.PhotoImage(preview)
            self.thumbnail_refs.append(photo)
            if generation == self.render_generation and label.winfo_exists():
                label.configure(image=photo, text="", bg="white")
        except Exception as exc:
            if label.winfo_exists():
                label.configure(text=f"Image error\n{exc}", image="")

    # ----------------------------- Insertion / removal -----------------------------

    def add_images_at_gap(self, gap_index: int):
        self.selected_gap = max(0, min(gap_index, len(self.items)))
        paths = filedialog.askopenfilenames(
            title="Choose image page(s)",
            filetypes=IMAGE_FILETYPES,
        )
        if paths:
            self.insert_image_paths(list(paths), self.selected_gap)
        else:
            # Still visibly select the clicked insertion point.
            self.refresh_visualizer()

    def insert_image_paths(self, paths: List[str], gap_index: Optional[int] = None):
        if not self.pdf_path:
            messagebox.showinfo("Choose a PDF first", "Please choose the PDF before adding images.")
            return

        valid = []
        invalid = []
        for p in paths:
            p = os.path.abspath(p)
            if os.path.isfile(p) and Path(p).suffix.lower() in IMAGE_SUFFIXES:
                valid.append(p)
            else:
                invalid.append(p)

        if not valid:
            messagebox.showerror("No supported images", "No supported image files were selected.")
            return

        if gap_index is None:
            gap_index = self.selected_gap
        gap_index = max(0, min(gap_index, len(self.items)))

        new_items = [ImagePage(p) for p in valid]
        self.items[gap_index:gap_index] = new_items

        # Keep the insertion point after the newly inserted images.
        self.selected_gap = gap_index + len(new_items)
        self.status_var.set(
            f"Inserted {len(new_items)} image page{'s' if len(new_items) != 1 else ''}"
        )
        self.refresh_visualizer()

        if invalid:
            messagebox.showwarning(
                "Some files skipped",
                "These files were not supported images:\n\n" + "\n".join(invalid[:10]),
            )

    def remove_inserted_page(self, final_pos: int):
        if not (0 <= final_pos < len(self.items)):
            return
        if isinstance(self.items[final_pos], OriginalPage):
            return
        del self.items[final_pos]
        self.selected_gap = min(final_pos, len(self.items))
        self.status_var.set("Inserted image removed")
        self.refresh_visualizer()

    # ----------------------------- Export -----------------------------

    def export_pdf(self):
        if not self.pdf_path:
            messagebox.showinfo("Choose a PDF first", "Please choose a PDF first.")
            return

        inserted = [x for x in self.items if isinstance(x, ImagePage)]
        if not inserted:
            proceed = messagebox.askyesno(
                "No images inserted",
                "No image pages have been inserted. Export a copy of the original PDF anyway?",
            )
            if not proceed:
                return

        source = Path(self.pdf_path)
        default_name = f"{source.stem}_with_images.pdf"

        out_path = filedialog.asksaveasfilename(
            title="Save new PDF",
            defaultextension=".pdf",
            initialfile=default_name,
            filetypes=(("PDF files", "*.pdf"),),
        )
        if not out_path:
            return

        out_path = os.path.abspath(out_path)
        if os.path.abspath(self.pdf_path) == out_path:
            messagebox.showerror(
                "Choose a new filename",
                "Please save to a different file so the original PDF is not overwritten.",
            )
            return

        self.export_btn.state(["disabled"])
        self.status_var.set("Exporting…")
        self.root.update_idletasks()

        try:
            self._write_output_pdf(out_path)
        except Exception as exc:
            messagebox.showerror("Export failed", f"Could not create the PDF:\n\n{exc}")
            self.status_var.set("Export failed")
            return
        finally:
            self.export_btn.state(["!disabled"])

        self.status_var.set("Export complete")
        messagebox.showinfo(
            "PDF created",
            f"Created successfully:\n\n{out_path}",
        )

    def _write_output_pdf(self, out_path: str):
        assert self.pdf_path is not None

        doc = fitz.open(self.pdf_path)
        if doc.needs_pass:
            if not self.pdf_password or not doc.authenticate(self.pdf_password):
                doc.close()
                raise RuntimeError("Could not authenticate the password-protected PDF.")

        original_count = len(self.pdf_page_sizes)

        # Group inserted images by which ORIGINAL page they appear before.
        # group[0] = before original page 1
        # group[k] = before original page k+1
        # group[n] = after the last original page
        groups: List[List[str]] = [[] for _ in range(original_count + 1)]
        next_original = 0

        for item in self.items:
            if isinstance(item, ImagePage):
                groups[next_original].append(item.path)
            else:
                # Original pages remain fixed and ordered.
                if item.index != next_original:
                    doc.close()
                    raise RuntimeError("Internal page ordering error.")
                next_original += 1

        if next_original != original_count:
            doc.close()
            raise RuntimeError("One or more original PDF pages are missing.")

        # Insert from the end toward the beginning so original page indices remain stable.
        for slot in range(original_count, -1, -1):
            image_paths = groups[slot]
            if not image_paths:
                continue

            ref_w, ref_h = self._reference_page_size_for_slot(slot)

            # Reverse insertion at the same page index preserves the user's image order.
            for image_path in reversed(image_paths):
                pno = slot if slot < original_count else doc.page_count
                page = doc.new_page(pno=pno, width=ref_w, height=ref_h)

                # Give the inserted page an explicit white background.
                page.draw_rect(page.rect, color=None, fill=(1, 1, 1), overlay=False)

                image_bytes, img_w, img_h = self._image_to_png_bytes(image_path)
                target = self._fit_rect(ref_w, ref_h, img_w, img_h, margin=18)
                page.insert_image(target, stream=image_bytes, keep_proportion=True, overlay=True)

        # Preserve document metadata where possible.
        metadata = doc.metadata
        if metadata:
            try:
                doc.set_metadata(metadata)
            except Exception:
                pass

        # garbage=4 cleans unused objects; deflate compresses streams.
        doc.save(out_path, garbage=4, deflate=True)
        doc.close()

    def _reference_page_size_for_slot(self, slot: int) -> tuple[float, float]:
        n = len(self.pdf_page_sizes)
        if n == 0:
            return 612.0, 792.0  # fallback: US Letter

        if slot <= 0:
            return self.pdf_page_sizes[0]
        if slot >= n:
            return self.pdf_page_sizes[-1]

        # An image inserted between pages follows the previous page's physical size.
        return self.pdf_page_sizes[slot - 1]

    @staticmethod
    def _image_to_png_bytes(path: str) -> tuple[bytes, int, int]:
        with Image.open(path) as im:
            im = ImageOps.exif_transpose(im)

            # Preserve alpha if present; otherwise use RGB.
            if "A" in im.getbands():
                im = im.convert("RGBA")
            else:
                im = im.convert("RGB")

            width, height = im.size
            if width <= 0 or height <= 0:
                raise ValueError(f"Invalid image dimensions: {path}")

            buf = io.BytesIO()
            im.save(buf, format="PNG", optimize=True)
            return buf.getvalue(), width, height

    @staticmethod
    def _fit_rect(page_w: float, page_h: float, img_w: int, img_h: int, margin: float = 18.0):
        avail_w = max(1.0, page_w - 2 * margin)
        avail_h = max(1.0, page_h - 2 * margin)

        scale = min(avail_w / img_w, avail_h / img_h)
        draw_w = img_w * scale
        draw_h = img_h * scale

        x0 = (page_w - draw_w) / 2.0
        y0 = (page_h - draw_h) / 2.0
        return fitz.Rect(x0, y0, x0 + draw_w, y0 + draw_h)

    # ----------------------------- Drag-and-drop -----------------------------

    def _parse_drop_paths(self, raw: str) -> List[str]:
        try:
            return list(self.root.tk.splitlist(raw))
        except Exception:
            return [raw]

    def _on_drop(self, event):
        paths = self._parse_drop_paths(event.data)
        pdfs = [p for p in paths if Path(p).suffix.lower() == ".pdf" and os.path.isfile(p)]
        images = [p for p in paths if Path(p).suffix.lower() in IMAGE_SUFFIXES and os.path.isfile(p)]

        if pdfs:
            if self.pdf_path and os.path.abspath(pdfs[0]) != os.path.abspath(self.pdf_path):
                replace = messagebox.askyesno(
                    "Replace current PDF?",
                    "A PDF is already loaded. Replace it with the dropped PDF?",
                )
                if not replace:
                    pdfs = []
            if pdfs:
                self.load_pdf(pdfs[0])

        if images:
            if not self.pdf_path:
                messagebox.showinfo(
                    "Choose a PDF first",
                    "Drop or choose a PDF first, then drop image files.",
                )
            else:
                self.insert_image_paths(images, self.selected_gap)

    def _on_gap_drop(self, event, gap_index: int):
        paths = self._parse_drop_paths(event.data)
        images = [p for p in paths if Path(p).suffix.lower() in IMAGE_SUFFIXES and os.path.isfile(p)]
        if images:
            self.insert_image_paths(images, gap_index)
        return "break"

    # ----------------------------- Scrolling / helpers -----------------------------

    def _on_strip_configure(self, _event=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_configure(self, event):
        # Keep strip at least as tall as the canvas while retaining horizontal scroll.
        self.canvas.itemconfigure(self.strip_window, height=max(event.height, CARD_H + 45))

    def _on_shift_wheel(self, event):
        delta = -1 if event.delta > 0 else 1
        self.canvas.xview_scroll(delta * 3, "units")
        return "break"

    def _on_mousewheel(self, event):
        # For this horizontal page strip, ordinary wheel scrolling moves horizontally.
        delta = -1 if event.delta > 0 else 1
        self.canvas.xview_scroll(delta * 3, "units")
        return "break"

    def _scroll_selected_gap_into_view(self):
        self.root.after(30, self._scroll_selected_gap_into_view_now)

    def _scroll_selected_gap_into_view_now(self):
        self.strip.update_idletasks()
        total_w = max(1, self.strip.winfo_reqwidth())
        viewport = max(1, self.canvas.winfo_width())

        # Approximate selected gap center from the visual sequence.
        x = self.selected_gap * (CARD_W + PLUS_W + 18) + PLUS_W / 2
        left_target = max(0, x - viewport / 2)
        max_left = max(1, total_w - viewport)
        self.canvas.xview_moveto(min(1.0, left_target / max_left))

    @staticmethod
    def _attach_tooltip(widget, text: str):
        tip = {"window": None}

        def show(_event=None):
            if tip["window"] is not None:
                return
            x = widget.winfo_rootx() + widget.winfo_width() // 2
            y = widget.winfo_rooty() + widget.winfo_height() + 6
            win = tk.Toplevel(widget)
            win.wm_overrideredirect(True)
            win.wm_geometry(f"+{x}+{y}")
            label = tk.Label(
                win,
                text=text,
                bg="#222222",
                fg="white",
                padx=7,
                pady=4,
                font=("Segoe UI", 8),
            )
            label.pack()
            tip["window"] = win

        def hide(_event=None):
            win = tip.get("window")
            if win is not None:
                try:
                    win.destroy()
                except Exception:
                    pass
            tip["window"] = None

        widget.bind("<Enter>", show, add="+")
        widget.bind("<Leave>", hide, add="+")


def create_root():
    if DND_AVAILABLE:
        return TkinterDnD.Tk()
    return tk.Tk()


def main():
    root = create_root()
    app = PDFImageInserterApp(root)

    # Optional convenience: open a PDF passed on the command line.
    if len(sys.argv) > 1 and Path(sys.argv[1]).suffix.lower() == ".pdf":
        root.after(150, lambda: app.load_pdf(sys.argv[1]))

    root.mainloop()


if __name__ == "__main__":
    main()
