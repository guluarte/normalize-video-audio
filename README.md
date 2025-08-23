# Python Audio Normalizer

This script normalizes the audio volume of video files in a directory.

## Description

This script recursively scans a directory for video files (MP4, MKV, MOV, AVI, WEBM) and uses the `ffmpeg` loudnorm filter to normalize the audio volume to the EBU R 128 standard. It performs a two-pass normalization to ensure the best quality.

A marker file (`.normalized`) is created for each processed file to avoid re-processing on subsequent runs.

## Requirements

- Python 3.12 or later
- `ffmpeg` installed and available in your system's PATH.

## Usage

Run the script from your terminal, providing the path to the directory containing your video files:

```bash
uv run normalize_video_audio.py /path/to/your/video/directory
```

The script will scan the directory and its subdirectories for video files and normalize their audio.

## How it Works

The script uses the `ffmpeg` loudnorm filter, which is a two-pass process:

1.  **First Pass:** The script analyzes the audio of each video file to determine its loudness characteristics (Integrated Loudness, Loudness Range, True Peak).
2.  **Second Pass:** Based on the analysis, the script applies the necessary gain adjustments to normalize the audio to the target levels (I: -16 LUFS, LRA: 11, TP: -1.5 dBFS).

The video stream is copied without re-encoding to preserve video quality and speed up the process. The audio is encoded using the lossless FLAC codec.

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.
