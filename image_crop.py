from __future__ import annotations

import math
from pathlib import Path
import tkinter as tk
from tkinter import messagebox

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

# Optional HEIC/HEIF support.
# Install with: pip install pillow-heif
try:
    from pillow_heif import register_heif_opener

    register_heif_opener()
except ImportError:
    pass


# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------

SOURCE_FOLDER = Path(r"C:\Users\qacer\Downloads\new\new")
OUTPUT_FOLDER_NAME = "cropped"

# Set this to True to also search folders inside SOURCE_FOLDER.
SEARCH_SUBFOLDERS = False

# Very small accidental selections will be ignored.
MINIMUM_SELECTION_SIZE = 3


class ImageCropQueueApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Original-Resolution Image Crop Queue")
        self.root.configure(background="black")

        # Open the application using the entire screen.
        self.root.attributes("-fullscreen", True)

        self.canvas = tk.Canvas(
            root,
            background="black",
            highlightthickness=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.output_folder = SOURCE_FOLDER / OUTPUT_FOLDER_NAME
        self.output_folder.mkdir(parents=True, exist_ok=True)

        self.image_paths = self.find_images()
        self.current_index = 0

        # Original image after applying EXIF orientation.
        self.original_image: Image.Image | None = None

        # Tkinter must retain a reference to the displayed image.
        self.tk_display_image: ImageTk.PhotoImage | None = None

        # Display transformation values.
        #
        # original_x = (screen_x - offset_x) / display_scale
        # original_y = (screen_y - offset_y) / display_scale
        self.display_scale = 1.0
        self.offset_x = 0.0
        self.offset_y = 0.0
        self.display_width = 0
        self.display_height = 0

        # Selection state.
        self.selection_start_x = 0.0
        self.selection_start_y = 0.0
        self.selection_rectangle_id: int | None = None
        self.selection_active = False

        # Number of crops created during this application session.
        self.total_crops_saved = 0
        self.current_image_crops_saved = 0

        # Used to debounce window resize events.
        self.resize_job: str | None = None

        self.canvas.bind("<ButtonPress-1>", self.start_selection)
        self.canvas.bind("<B1-Motion>", self.update_selection)
        self.canvas.bind("<ButtonRelease-1>", self.finish_selection)
        self.canvas.bind("<Configure>", self.handle_resize)

        self.root.bind_all("<space>", self.next_image)
        self.root.bind_all("<Escape>", self.exit_application)
        self.root.bind_all("<F11>", self.toggle_fullscreen)

        if not self.image_paths:
            messagebox.showerror(
                "No images found",
                f"No readable images were found in:\n{SOURCE_FOLDER}",
            )
            self.root.destroy()
            return

        # Wait until the full-screen canvas has received its real dimensions.
        self.root.after(100, self.load_current_image)

    # ------------------------------------------------------------------
    # Image queue
    # ------------------------------------------------------------------

    def find_images(self) -> list[Path]:
        """Find every file that Pillow can actually open as an image."""
        if not SOURCE_FOLDER.exists():
            messagebox.showerror(
                "Folder not found",
                f"The source folder does not exist:\n{SOURCE_FOLDER}",
            )
            return []

        if SEARCH_SUBFOLDERS:
            candidates = SOURCE_FOLDER.rglob("*")
        else:
            candidates = SOURCE_FOLDER.iterdir()

        image_paths: list[Path] = []

        for path in candidates:
            if not path.is_file():
                continue

            # Do not reload images produced by this application.
            if self.output_folder in path.parents:
                continue

            try:
                with Image.open(path) as test_image:
                    test_image.verify()

                image_paths.append(path)

            except (
                UnidentifiedImageError,
                OSError,
                ValueError,
                SyntaxError,
            ):
                # The file is not a supported image or is damaged.
                continue

        return sorted(
            image_paths,
            key=lambda item: str(item).lower(),
        )

    def load_current_image(self) -> None:
        """Load the current queue item at its original resolution."""
        self.clear_selection()

        if self.current_index >= len(self.image_paths):
            self.show_queue_complete_screen()
            return

        image_path = self.image_paths[self.current_index]

        try:
            with Image.open(image_path) as opened_image:
                # Correct images taken with cameras or phones whose orientation
                # is stored in EXIF metadata instead of in the pixel layout.
                oriented_image = ImageOps.exif_transpose(opened_image)

                # Copy it so the source file can be closed safely.
                self.original_image = oriented_image.copy()

        except Exception as error:
            print(f"Could not open {image_path}: {error}")
            self.current_index += 1
            self.load_current_image()
            return

        self.current_image_crops_saved = 0
        self.render_current_image()

    def render_current_image(self) -> None:
        """Scale the image for viewing while retaining the exact mapping."""
        if self.original_image is None:
            return

        canvas_width = max(self.canvas.winfo_width(), 1)
        canvas_height = max(self.canvas.winfo_height(), 1)

        original_width, original_height = self.original_image.size

        # "Contain" scaling:
        # one dimension touches the viewport boundary and the whole image
        # remains visible without changing its aspect ratio.
        width_scale = canvas_width / original_width
        height_scale = canvas_height / original_height

        self.display_scale = min(width_scale, height_scale)

        self.display_width = max(
            1,
            round(original_width * self.display_scale),
        )
        self.display_height = max(
            1,
            round(original_height * self.display_scale),
        )

        # Center the scaled image inside the viewport.
        self.offset_x = (canvas_width - self.display_width) / 2
        self.offset_y = (canvas_height - self.display_height) / 2

        display_image = self.original_image.resize(
            (self.display_width, self.display_height),
            Image.Resampling.LANCZOS,
        )

        # ImageTk works reliably with RGB and RGBA images.
        if display_image.mode not in ("RGB", "RGBA"):
            display_image = display_image.convert("RGBA")

        self.tk_display_image = ImageTk.PhotoImage(display_image)

        self.canvas.delete("all")

        self.canvas.create_image(
            self.offset_x,
            self.offset_y,
            anchor=tk.NW,
            image=self.tk_display_image,
            tags=("displayed_image",),
        )

        self.draw_status_text()

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def start_selection(self, event: tk.Event) -> None:
        """Begin a rectangular selection."""
        if self.original_image is None:
            return

        if not self.point_is_inside_displayed_image(event.x, event.y):
            return

        x, y = self.clamp_to_displayed_image(event.x, event.y)

        self.selection_start_x = x
        self.selection_start_y = y
        self.selection_active = True

        self.clear_selection()

        self.selection_rectangle_id = self.canvas.create_rectangle(
            x,
            y,
            x,
            y,
            outline="white",
            width=2,
            dash=(6, 4),
            tags=("selection",),
        )

    def update_selection(self, event: tk.Event) -> None:
        """Resize the temporary rectangle while the mouse is dragged."""
        if not self.selection_active:
            return

        if self.selection_rectangle_id is None:
            return

        x, y = self.clamp_to_displayed_image(event.x, event.y)

        self.canvas.coords(
            self.selection_rectangle_id,
            self.selection_start_x,
            self.selection_start_y,
            x,
            y,
        )

    def finish_selection(self, event: tk.Event) -> None:
        """
        Convert the displayed selection into original-image coordinates,
        save the original-resolution crop, and remove the rectangle.
        """
        if not self.selection_active or self.original_image is None:
            self.clear_selection()
            return

        end_x, end_y = self.clamp_to_displayed_image(event.x, event.y)

        screen_left = min(self.selection_start_x, end_x)
        screen_top = min(self.selection_start_y, end_y)
        screen_right = max(self.selection_start_x, end_x)
        screen_bottom = max(self.selection_start_y, end_y)

        selected_width = screen_right - screen_left
        selected_height = screen_bottom - screen_top

        # The rectangle disappears immediately after release.
        self.clear_selection()

        if (
            selected_width < MINIMUM_SELECTION_SIZE
            or selected_height < MINIMUM_SELECTION_SIZE
        ):
            self.show_temporary_message("Selection was too small.")
            return

        # Convert the screen coordinates back to original pixel coordinates.
        #
        # floor is used for the upper-left corner and ceil for the lower-right
        # corner so the selected original pixels are not accidentally omitted.
        original_left = math.floor(
            (screen_left - self.offset_x) / self.display_scale
        )
        original_top = math.floor(
            (screen_top - self.offset_y) / self.display_scale
        )
        original_right = math.ceil(
            (screen_right - self.offset_x) / self.display_scale
        )
        original_bottom = math.ceil(
            (screen_bottom - self.offset_y) / self.display_scale
        )

        original_width, original_height = self.original_image.size

        original_left = max(0, min(original_left, original_width))
        original_top = max(0, min(original_top, original_height))
        original_right = max(0, min(original_right, original_width))
        original_bottom = max(0, min(original_bottom, original_height))

        if (
            original_right <= original_left
            or original_bottom <= original_top
        ):
            self.show_temporary_message("The selection contained no pixels.")
            return

        crop_box = (
            original_left,
            original_top,
            original_right,
            original_bottom,
        )

        cropped_image = self.original_image.crop(crop_box)
        saved_path = self.save_crop(cropped_image)

        self.current_image_crops_saved += 1
        self.total_crops_saved += 1

        print(
            f"Saved: {saved_path} | "
            f"Original crop coordinates: {crop_box}"
        )

        self.draw_status_text(
            extra_message=(
                f"Saved {saved_path.name} — "
                f"{cropped_image.width} × {cropped_image.height} original pixels"
            )
        )

    # ------------------------------------------------------------------
    # Crop saving
    # ------------------------------------------------------------------

    def save_crop(self, cropped_image: Image.Image) -> Path:
        """Save a crop losslessly as PNG without overwriting old crops."""
        source_path = self.image_paths[self.current_index]
        source_stem = self.safe_filename_part(source_path.stem)

        crop_number = 1

        while True:
            output_path = self.output_folder / (
                f"{source_stem}_crop_{crop_number:03d}.png"
            )

            if not output_path.exists():
                break

            crop_number += 1

        # PNG supports RGB, RGBA, grayscale and several other modes, but
        # converting uncommon source modes prevents format-related failures.
        if cropped_image.mode == "P":
            if "transparency" in cropped_image.info:
                cropped_image = cropped_image.convert("RGBA")
            else:
                cropped_image = cropped_image.convert("RGB")

        elif cropped_image.mode not in (
            "1",
            "L",
            "LA",
            "I",
            "I;16",
            "RGB",
            "RGBA",
        ):
            cropped_image = cropped_image.convert("RGBA")

        cropped_image.save(output_path, format="PNG")
        return output_path

    @staticmethod
    def safe_filename_part(value: str) -> str:
        """Remove characters that cannot be used in Windows filenames."""
        forbidden_characters = '<>:"/\\|?*'

        cleaned_value = "".join(
            "_" if character in forbidden_characters else character
            for character in value
        )

        cleaned_value = cleaned_value.strip().rstrip(".")
        return cleaned_value or "image"

    # ------------------------------------------------------------------
    # Queue navigation
    # ------------------------------------------------------------------

    def next_image(self, event: tk.Event | None = None) -> str:
        """Move to the next image when Spacebar is pressed."""
        if self.current_index >= len(self.image_paths):
            return "break"

        self.clear_selection()
        self.original_image = None
        self.tk_display_image = None

        self.current_index += 1
        self.load_current_image()

        return "break"

    def show_queue_complete_screen(self) -> None:
        self.original_image = None
        self.tk_display_image = None
        self.clear_selection()
        self.canvas.delete("all")

        canvas_width = self.canvas.winfo_width()
        canvas_height = self.canvas.winfo_height()

        self.canvas.create_text(
            canvas_width / 2,
            canvas_height / 2,
            text=(
                "IMAGE QUEUE COMPLETE\n\n"
                f"Images processed: {len(self.image_paths)}\n"
                f"Crops saved this session: {self.total_crops_saved}\n\n"
                f"Output folder:\n{self.output_folder}\n\n"
                "Press Escape to close."
            ),
            fill="white",
            justify=tk.CENTER,
            font=("Segoe UI", 20),
        )

    # ------------------------------------------------------------------
    # Coordinates and visual helpers
    # ------------------------------------------------------------------

    def point_is_inside_displayed_image(self, x: float, y: float) -> bool:
        return (
            self.offset_x <= x <= self.offset_x + self.display_width
            and self.offset_y <= y <= self.offset_y + self.display_height
        )

    def clamp_to_displayed_image(
        self,
        x: float,
        y: float,
    ) -> tuple[float, float]:
        clamped_x = max(
            self.offset_x,
            min(x, self.offset_x + self.display_width),
        )
        clamped_y = max(
            self.offset_y,
            min(y, self.offset_y + self.display_height),
        )

        return clamped_x, clamped_y

    def clear_selection(self) -> None:
        if self.selection_rectangle_id is not None:
            self.canvas.delete(self.selection_rectangle_id)

        self.selection_rectangle_id = None
        self.selection_active = False

    def draw_status_text(self, extra_message: str = "") -> None:
        self.canvas.delete("status")

        if self.current_index >= len(self.image_paths):
            return

        source_path = self.image_paths[self.current_index]

        image_dimensions = ""
        scale_information = ""

        if self.original_image is not None:
            original_width, original_height = self.original_image.size

            image_dimensions = (
                f"Original: {original_width} × {original_height} px"
            )

            scale_information = (
                f"View scale: {self.display_scale:.6f} "
                f"({self.display_scale * 100:.2f}%)"
            )

        status_lines = [
            f"{self.current_index + 1} / {len(self.image_paths)}",
            source_path.name,
            image_dimensions,
            scale_information,
            (
                f"Crops from this image: "
                f"{self.current_image_crops_saved}"
            ),
            "Drag to crop  •  Space: next image  •  Escape: exit",
        ]

        if extra_message:
            status_lines.append(extra_message)

        self.canvas.create_text(
            15,
            15,
            anchor=tk.NW,
            text="\n".join(status_lines),
            fill="white",
            font=("Segoe UI", 12),
            tags=("status",),
        )

    def show_temporary_message(self, message: str) -> None:
        self.draw_status_text(extra_message=message)

        self.root.after(
            1500,
            lambda: self.draw_status_text()
            if self.original_image is not None
            else None,
        )

    # ------------------------------------------------------------------
    # Window controls
    # ------------------------------------------------------------------

    def handle_resize(self, event: tk.Event) -> None:
        """Redraw the current image if the viewport dimensions change."""
        if self.original_image is None:
            return

        if self.resize_job is not None:
            self.root.after_cancel(self.resize_job)

        self.resize_job = self.root.after(
            100,
            self.render_current_image,
        )

    def toggle_fullscreen(self, event: tk.Event | None = None) -> str:
        current_state = bool(self.root.attributes("-fullscreen"))
        self.root.attributes("-fullscreen", not current_state)
        return "break"

    def exit_application(self, event: tk.Event | None = None) -> str:
        self.root.destroy()
        return "break"


def main() -> None:
    root = tk.Tk()
    ImageCropQueueApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()