from pydub import AudioSegment
from pydub.effects import invert_phase
import os
import sys


def get_import_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower().lstrip(".")

    format_map = {
        "mp3": "mp3",
        "wav": "wav",
        "flac": "flac",
        "m4a": "mp4",
        "mp4": "mp4",
        "ogg": "ogg",
        "aac": "aac",
        "wma": "asf",
    }

    return format_map.get(ext, ext)


def get_export_settings(path: str) -> dict:
    ext = os.path.splitext(path)[1].lower().lstrip(".")

    if ext == "aac":
        # Raw AAC uses ADTS container in FFmpeg.
        return {
            "format": "adts",
            "codec": "aac",
        }

    if ext == "m4a":
        # AAC inside MP4/M4A container.
        return {
            "format": "mp4",
            "codec": "aac",
        }

    if ext == "mp4":
        return {
            "format": "mp4",
            "codec": "aac",
        }

    if ext == "wma":
        return {
            "format": "asf",
        }

    return {
        "format": ext,
    }


def make_negative_audio(input_path: str, output_path: str | None = None):
    if not os.path.isfile(input_path):
        raise FileNotFoundError(f"Input file not found: {input_path}")

    name, input_ext = os.path.splitext(input_path)
    input_ext = input_ext.lower().lstrip(".")

    if output_path is None:
        output_path = f"{name}_negative.{input_ext}"

    output_ext = os.path.splitext(output_path)[1].lower().lstrip(".")
    if not output_ext:
        raise ValueError("Output path must include an audio extension, like .wav, .mp3, .m4a, or .aac")

    input_format = get_import_format(input_path)
    export_settings = get_export_settings(output_path)

    print(f"Loading: {input_path}")
    audio = AudioSegment.from_file(input_path, format=input_format)

    print("Inverting waveform...")
    negative = invert_phase(audio)

    print(f"Saving: {output_path}")
    negative.export(output_path, **export_settings)

    print("Done.")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage:")
        print("  python anc.py input_audio.aac")
        print("  python anc.py input_audio.aac output_negative.wav")
        print("  python anc.py input_audio.aac output_negative.m4a")
        print("  python anc.py input_audio.mp3 output_negative.aac")
        sys.exit(1)

    input_file = sys.argv[1]
    output_file = sys.argv[2] if len(sys.argv) >= 3 else None

    make_negative_audio(input_file, output_file)