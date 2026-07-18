import argparse
from pydub import AudioSegment
import math

def merge_with_gain(music_path, vocals_path, beat_gain_factor, voice_gain_factor, output_path):
    try:
        # Load audio files
        music = AudioSegment.from_file(music_path)
        vocals = AudioSegment.from_file(vocals_path)
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}")
        return

    # Convert volume factor to decibels and apply it (correct way in pydub 0.25.1)
    # Formula: dB = 20 * log10(factor)
    if beat_gain_factor != 1.0:
        beat_db = 20 * math.log10(beat_gain_factor)
        music = music.apply_gain(beat_db)
        print(f"Applied beat multiplier: {beat_gain_factor}x ({beat_db:.2f} dB)")
        
    if voice_gain_factor != 1.0:
        voice_db = 20 * math.log10(voice_gain_factor)
        vocals = vocals.apply_gain(voice_db)
        print(f"Applied voice multiplier: {voice_gain_factor}x ({voice_db:.2f} dB)")

    # Pad the shorter track with silence to match lengths
    max_len = max(len(music), len(vocals))
    if len(music) < max_len:
        music = music + AudioSegment.silent(duration=(max_len - len(music)))
    elif len(vocals) < max_len:
        vocals = vocals + AudioSegment.silent(duration=(max_len - len(vocals)))

    # Mix the tracks together
    combined = music.overlay(vocals)

    # --- SAFETY CHECK: Prevent digital clipping (distortion) ---
    peak_dBFS = combined.max_dBFS
    
    if peak_dBFS > 0:
        reduction = peak_dBFS + 0.1
        combined = combined.apply_gain(-reduction)
        print(f"⚠️ Clipping detected! Master volume automatically lowered by {reduction:.1f} dB to keep the audio crystal clear.")

    # Export clean result
    combined.export(output_path, format="mp3")
    print(f"✅ Success! Distortion-free merged audio saved to: {output_path}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Merge instrumental and vocal tracks with volume adjustments.")
    parser.add_argument("--music", default="audio [music].mp3", help="Path to the music/beat file.")
    parser.add_argument("--vocals", default="audio [vocals].mp3", help="Path to the vocals file.")
    parser.add_argument("--beat", type=float, default=1.0, help="Amplify the beat by x factor. E.g., 1.5 = +50%, 0.5 = -50%.")
    parser.add_argument("--voice", type=float, default=1.0, help="Amplify the voice by x factor. E.g., 1.25 = +25%, 0.5 = -50%.")
    parser.add_argument("--output", default="final_merged.mp3", help="Output MP3 filename.")

    args = parser.parse_args()
    merge_with_gain(args.music, args.vocals, args.beat, args.voice, args.output)