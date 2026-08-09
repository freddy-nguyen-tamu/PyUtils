import argparse
import shutil
import subprocess
import sys
from pathlib import Path

from pydub import AudioSegment


def choose_mp3():
    """
    Open Windows file explorer to select an MP3.
    """

    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()

    file_path = filedialog.askopenfilename(
        title="Choose MP3 to edit",
        filetypes=[
            ("MP3 files", "*.mp3"),
            ("All files", "*.*")
        ]
    )

    root.destroy()

    if not file_path:
        print("No file selected.")
        exit(0)

    return Path(file_path)



def run_demucs(input_file, output_dir):
    """
    Separate vocals and instrumental using Demucs.
    Uses the exact Python interpreter running this script.
    """

    cmd = [
        sys.executable,
        "-m",
        "demucs.separate",
        "--two-stems=vocals",
        "-o",
        str(output_dir),
        str(input_file)
    ]

    print("Running:")
    print(" ".join(cmd))

    subprocess.run(
        cmd,
        check=True
    )



def find_stems(output_dir, input_file):

    song_name = input_file.stem

    folder = (
        output_dir /
        "htdemucs" /
        song_name
    )

    vocals = folder / "vocals.wav"
    beat = folder / "no_vocals.wav"

    if not vocals.exists():
        raise FileNotFoundError(
            f"Cannot find vocals:\n{vocals}"
        )

    if not beat.exists():
        raise FileNotFoundError(
            f"Cannot find instrumental:\n{beat}"
        )

    return vocals, beat



def change_volume(audio, multiplier):

    if multiplier is None:
        return audio

    if multiplier <= 0:
        raise ValueError(
            "Multiplier must be greater than zero"
        )

    gain_db = 20 * (multiplier - 1)

    return audio.apply_gain(
        gain_db
    )



def main():

    parser = argparse.ArgumentParser(
        description="Adjust MP3 vocals and beat"
    )

    parser.add_argument(
        "--beat",
        type=float,
        default=None,
        help="Beat volume multiplier"
    )

    parser.add_argument(
        "--lyric",
        type=float,
        default=None,
        help="Vocal volume multiplier"
    )

    args = parser.parse_args()


    input_file = choose_mp3()


    output_file = (
        input_file.parent /
        f"{input_file.stem}_edited.mp3"
    )


    temp_dir = Path(
        "_demucs_temp"
    )


    if temp_dir.exists():
        shutil.rmtree(temp_dir)

    temp_dir.mkdir()


    try:

        print("\nSeparating vocals and beat...")

        run_demucs(
            input_file,
            temp_dir
        )


        print("Loading stems...")


        vocals_file, beat_file = find_stems(
            temp_dir,
            input_file
        )


        vocals = AudioSegment.from_file(
            vocals_file
        )

        beat = AudioSegment.from_file(
            beat_file
        )


        if args.lyric is not None:

            print(
                f"Vocal volume: {args.lyric}x"
            )

            vocals = change_volume(
                vocals,
                args.lyric
            )


        if args.beat is not None:

            print(
                f"Beat volume: {args.beat}x"
            )

            beat = change_volume(
                beat,
                args.beat
            )


        print("Mixing...")

        result = beat.overlay(
            vocals
        )


        print("Exporting...")

        result.export(
            output_file,
            format="mp3",
            bitrate="320k"
        )


        print("\nDone!")
        print(output_file)


    finally:

        if temp_dir.exists():
            shutil.rmtree(temp_dir)



if __name__ == "__main__":
    main()