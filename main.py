from __future__ import annotations

from shazam_project.config import load_config, missing_configuration
from shazam_project.display import show_result
from shazam_project.fft_analyze import analyze_audio
from shazam_project.matcher import match_audio
from shazam_project.recorder import AudioInputError, load_audio_file, record_microphone


def main() -> int:
    print("DIY Shazam - startup check")

    config = load_config()
    missing = missing_configuration(config)

    if missing:
        print("Missing required configuration:")
        for name in missing:
            print(f"- {name}")
        print()
        print("The app can still validate, analyze, and attempt local matching.")
        print("Provider matching will continue to the next configured backend.")

    print("Configuration loaded successfully.")

    mode = input("Listen via microphone or load a file? (mic/file): ").strip().lower()

    try:
        if mode == "mic":
            duration = input("Recording length in seconds [8]: ").strip()
            duration_seconds = int(duration) if duration else config.audio_seconds
            clip = record_microphone(
                duration_seconds=duration_seconds,
                sample_rate=config.internal_sample_rate,
                config=config,
            )
            print(f"Captured {len(clip.samples)} samples at {clip.sample_rate} Hz from microphone.")
        elif mode == "file":
            file_path = input("Enter a WAV file path: ").strip().strip('"')
            clip = load_audio_file(file_path, config=config)
            print(f"Loaded {clip.path} with {len(clip.samples)} samples at {clip.sample_rate} Hz.")
        else:
            print("Invalid choice. Use 'mic' or 'file'.")
            return 1
    except AudioInputError as exc:
        print(f"Audio input error [{exc.code}]: {exc.message}")
        return 1
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"Audio input error: {exc}")
        return 1

    try:
        fft_output = analyze_audio(clip.samples, clip.sample_rate, config.fft_output_path)
    except ValueError as exc:
        print(f"FFT analysis error: {exc}")
        return 1

    print(f"FFT diagnostic visualization saved to {fft_output}")
    print("FFT is diagnostic only; song identification uses provider matching or spectrogram peak hash pairs.")

    try:
        result = match_audio(clip, config)
    except Exception:
        print("Matching error: recognition failed.")
        return 1

    show_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
