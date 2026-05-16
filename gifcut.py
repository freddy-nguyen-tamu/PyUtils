from PIL import Image


def cut_gif_by_frame(input_path: str, output_path: str, start_frame: int, end_frame: int) -> None:
    """
    Cut a GIF by frame number and save the selected frames as a new GIF.

    Args:
        input_path: Path to the input GIF.
        output_path: Path to save the output GIF.
        start_frame: First frame to keep (inclusive, 0-based).
        end_frame: Last frame to keep (inclusive, 0-based).
    """
    if start_frame < 0 or end_frame < start_frame:
        raise ValueError("Invalid frame range.")

    with Image.open(input_path) as gif:
        frames = []
        durations = []
        disposals = []

        total_frames = getattr(gif, "n_frames", 1)

        if end_frame >= total_frames:
            raise ValueError(
                f"end_frame ({end_frame}) is out of range. GIF has {total_frames} frames."
            )

        for i in range(total_frames):
            gif.seek(i)
            if start_frame <= i <= end_frame:
                frames.append(gif.copy())
                durations.append(gif.info.get("duration", 100))
                disposals.append(gif.disposal_method if hasattr(gif, "disposal_method") else 2)

        if not frames:
            raise ValueError("No frames selected.")

        first, rest = frames[0], frames[1:]

        first.save(
            output_path,
            save_all=True,
            append_images=rest,
            duration=durations,
            loop=gif.info.get("loop", 0),
            disposal=2,
        )


if __name__ == "__main__":
    input_gif = "input.gif"
    output_gif = "output.gif"
    start_frame = 10
    end_frame = 30

    cut_gif_by_frame(input_gif, output_gif, start_frame, end_frame)
    print(f"Saved cut GIF to {output_gif}")