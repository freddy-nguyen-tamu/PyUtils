import base64
import io
import os
from typing import Tuple

from openai import OpenAI
from PIL import Image, ImageOps
import gradio as gr


client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))


ALLOWED_SIZES = {"auto", "1024x1024", "1536x1024", "1024x1536"}
ALLOWED_QUALITY = {"auto", "low", "medium", "high"}
ALLOWED_BACKGROUND = {"auto", "opaque", "transparent"}
ALLOWED_FIDELITY = {"high", "low"}
ALLOWED_FORMATS = {"png", "jpeg", "webp"}


def _normalize_image(img: Image.Image) -> Image.Image:
    """Convert to RGBA and auto-rotate using EXIF."""
    img = ImageOps.exif_transpose(img)
    return img.convert("RGBA")


def build_outpaint_canvas(
    image: Image.Image,
    pad_left: int,
    pad_right: int,
    pad_top: int,
    pad_bottom: int,
) -> Tuple[Image.Image, Image.Image]:
    """
    Returns:
      base_image: original image centered on a larger transparent canvas
      mask_image: transparent where the model may generate, opaque where it should preserve

    For the OpenAI image edit API, fully transparent mask areas are editable.
    """
    image = _normalize_image(image)
    w, h = image.size
    new_w = w + pad_left + pad_right
    new_h = h + pad_top + pad_bottom

    if new_w <= 0 or new_h <= 0:
        raise ValueError("Expanded canvas must have positive width and height.")

    # Larger transparent canvas with the input image pasted in the middle.
    base = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))
    base.paste(image, (pad_left, pad_top), image)

    # Mask rules:
    # - transparent (alpha=0): region can be edited/generated
    # - opaque (alpha=255): region should be preserved
    mask = Image.new("RGBA", (new_w, new_h), (0, 0, 0, 0))

    preserve = Image.new("RGBA", (w, h), (255, 255, 255, 255))
    mask.paste(preserve, (pad_left, pad_top))

    return base, mask


def pil_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def decode_b64_to_pil(b64_json: str) -> Image.Image:
    raw = base64.b64decode(b64_json)
    return Image.open(io.BytesIO(raw)).convert("RGBA")


def outpaint(
    image: Image.Image,
    prompt: str,
    negative_hint: str,
    pad_left: int,
    pad_right: int,
    pad_top: int,
    pad_bottom: int,
    size: str,
    quality: str,
    background: str,
    input_fidelity: str,
    output_format: str,
    n: int,
):
    if image is None:
        raise gr.Error("Upload an image first.")
    if not prompt.strip():
        raise gr.Error("Enter a prompt describing the new context to invent around the image.")
    if size not in ALLOWED_SIZES:
        raise gr.Error(f"Invalid size: {size}")
    if quality not in ALLOWED_QUALITY:
        raise gr.Error(f"Invalid quality: {quality}")
    if background not in ALLOWED_BACKGROUND:
        raise gr.Error(f"Invalid background: {background}")
    if input_fidelity not in ALLOWED_FIDELITY:
        raise gr.Error(f"Invalid input_fidelity: {input_fidelity}")
    if output_format not in ALLOWED_FORMATS:
        raise gr.Error(f"Invalid output format: {output_format}")

    base_img, mask_img = build_outpaint_canvas(
        image,
        pad_left=pad_left,
        pad_right=pad_right,
        pad_top=pad_top,
        pad_bottom=pad_bottom,
    )

    base_bytes = pil_to_png_bytes(base_img)
    mask_bytes = pil_to_png_bytes(mask_img)

    full_prompt = prompt.strip()
    if negative_hint.strip():
        full_prompt += (
            "\n\nAvoid these issues if possible: " + negative_hint.strip()
        )

    result = client.images.edit(
        model="gpt-image-1.5",
        image=("base.png", base_bytes, "image/png"),
        mask=("mask.png", mask_bytes, "image/png"),
        prompt=full_prompt,
        size=size,
        quality=quality,
        background=background,
        input_fidelity=input_fidelity,
        output_format=output_format,
        n=n,
    )

    outputs = []
    for item in result.data:
        outputs.append(decode_b64_to_pil(item.b64_json))

    debug_preview = mask_img
    return base_img, debug_preview, outputs


with gr.Blocks(title="Image Outpainting Studio") as demo:
    gr.Markdown(
        "# Image Outpainting Studio\n"
        "Upload an image, expand the canvas, and describe the new context you want generated."
    )

    with gr.Row():
        with gr.Column(scale=1):
            input_image = gr.Image(type="pil", label="Input image")
            prompt = gr.Textbox(
                label="Prompt",
                lines=4,
                placeholder=(
                    "Example: Expand this portrait into a cinematic rooftop scene at sunset, "
                    "adding skyline reflections, subtle wind in the clothing, and realistic lighting."
                ),
            )
            negative_hint = gr.Textbox(
                label="Optional guardrails",
                lines=2,
                placeholder="Example: extra fingers, warped face, duplicate objects, blurry background",
            )

            with gr.Accordion("Canvas expansion", open=True):
                pad_left = gr.Slider(0, 1024, value=256, step=16, label="Pad left")
                pad_right = gr.Slider(0, 1024, value=256, step=16, label="Pad right")
                pad_top = gr.Slider(0, 1024, value=128, step=16, label="Pad top")
                pad_bottom = gr.Slider(0, 1024, value=128, step=16, label="Pad bottom")

            with gr.Accordion("Generation settings", open=True):
                size = gr.Dropdown(
                    choices=["auto", "1024x1024", "1536x1024", "1024x1536"],
                    value="auto",
                    label="Output size",
                )
                quality = gr.Dropdown(
                    choices=["auto", "low", "medium", "high"],
                    value="high",
                    label="Quality",
                )
                background = gr.Dropdown(
                    choices=["auto", "opaque", "transparent"],
                    value="auto",
                    label="Background",
                )
                input_fidelity = gr.Dropdown(
                    choices=["high", "low"],
                    value="high",
                    label="Input fidelity",
                )
                output_format = gr.Dropdown(
                    choices=["png", "jpeg", "webp"],
                    value="png",
                    label="Output format",
                )
                n = gr.Slider(1, 4, value=1, step=1, label="Number of results")

            run_btn = gr.Button("Outpaint")

        with gr.Column(scale=1):
            expanded_preview = gr.Image(type="pil", label="Expanded input canvas")
            mask_preview = gr.Image(type="pil", label="Mask preview (transparent area is editable)")
            output_gallery = gr.Gallery(label="Generated outputs", columns=2, rows=2, height=700)

    run_btn.click(
        fn=outpaint,
        inputs=[
            input_image,
            prompt,
            negative_hint,
            pad_left,
            pad_right,
            pad_top,
            pad_bottom,
            size,
            quality,
            background,
            input_fidelity,
            output_format,
            n,
        ],
        outputs=[expanded_preview, mask_preview, output_gallery],
    )


if __name__ == "__main__":
    demo.launch()
