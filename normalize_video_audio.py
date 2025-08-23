# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "click",
# ]
# ///

"""
A tool to normalize the audio volume of video files in a directory.

This script scans a directory recursively for video files and uses the ffmpeg
loudnorm filter to normalize the audio volume to a standard level. It performs
a two-pass normalization to maintain audio quality and avoids re-encoding the
video stream.

A marker file (.normalized) is created for each processed file to avoid
re-processing on subsequent runs.

Requires the ffmpeg executable to be in the system's PATH.
"""

import json
import pathlib
import shutil
import subprocess
import sys

import click

# Common video file extensions
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".avi", ".webm"}

# EBU R 128 standard parameters for loudness normalization
LOUDNESS_TARGETS = {
    "I": -16,  # Integrated Loudness Target in LUFS
    "LRA": 11,  # Loudness Range Target
    "TP": -1.5,  # True Peak Target in dBFS
}


def run_command(command: list[str]) -> subprocess.CompletedProcess:
    """Runs a command and returns the completed process."""
    return subprocess.run(command, capture_output=True, text=True, encoding="utf-8")


def get_loudness_stats(file_path: pathlib.Path) -> dict | None:
    """
    First pass of normalization: Analyze the audio to get loudness stats.
    """
    click.echo(f"  Analyzing audio for: {file_path.name}")
    cmd = [
        "ffmpeg",
        "-i",
        str(file_path),
        "-hide_banner",
        "-vn",  # No video output
        "-af",
        (
            f"loudnorm=I={LOUDNESS_TARGETS['I']}:"
            f"LRA={LOUDNESS_TARGETS['LRA']}:"
            f"tp={LOUDNESS_TARGETS['TP']}:"
            "print_format=json"
        ),
        "-f",
        "null",  # Don't write an output file for this pass
        "-",
    ]

    result = run_command(cmd)

    if result.returncode != 0:
        click.secho(f"    Error analyzing file: {file_path.name}", fg="red")
        click.secho(result.stderr, fg="red")
        return None

    # ffmpeg prints the JSON summary to stderr
    lines = result.stderr.strip().split("\n")
    json_output = [line for line in lines if line.startswith("{")]

    if not json_output:
        click.secho(
            f"    Could not extract loudness stats for {file_path.name}", fg="red"
        )
        return None

    try:
        # The JSON output is the last part of the stderr
        stats_str = result.stderr.strip().split("\n")[-1]
        stats = json.loads(stats_str)
        return {
            "measured_I": stats["input_i"],
            "measured_LRA": stats["input_lra"],
            "measured_TP": stats["input_tp"],
            "measured_thresh": stats["input_thresh"],
            "offset": stats["target_offset"],
        }
    except (json.JSONDecodeError, KeyError) as e:
        click.secho(
            f"    Error parsing loudness stats for {file_path.name}: {e}", fg="red"
        )
        click.secho(f"    Full ffmpeg output:\n{result.stderr}", fg="red")
        return None


def apply_normalization(
    input_path: pathlib.Path, output_path: pathlib.Path, stats: dict
) -> bool:
    """
    Second pass of normalization: Apply the calculated stats to normalize.
    """
    click.echo(f"  Applying normalization to: {input_path.name}")

    loudnorm_filter = (
        f"loudnorm=I={LOUDNESS_TARGETS['I']}:"
        f"LRA={LOUDNESS_TARGETS['LRA']}:"
        f"tp={LOUDNESS_TARGETS['TP']}:"
        f"measured_I={stats['measured_I']}:"
        f"measured_LRA={stats['measured_LRA']}:"
        f"measured_tp={stats['measured_TP']}:"
        f"measured_thresh={stats['measured_thresh']}:"
        f"offset={stats['offset']}"
    )

    cmd = [
        "ffmpeg",
        "-i",
        str(input_path),
        "-hide_banner",
        "-y",  # Overwrite output file if it exists
        "-c:v",
        "copy",  # Copy video stream without re-encoding
        "-c:a",
        "flac",  # Use FLAC for lossless audio codec
        "-ar",
        "48k",  # Standard audio sample rate
        "-af",
        loudnorm_filter,
        str(output_path),
    ]

    result = run_command(cmd)

    if result.returncode != 0:
        click.secho(f"    Error normalizing file: {input_path.name}", fg="red")
        click.secho(result.stderr, fg="red")
        return False

    return True


@click.command()
@click.argument(
    "directory",
    type=click.Path(
        exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path
    ),
)
def main(directory: pathlib.Path):
    """
    Normalizes audio volume for all video files in a directory and its subdirectories.

    This tool requires 'ffmpeg' to be installed and available in your system's PATH.
    """
    if not shutil.which("ffmpeg"):
        click.secho("Error: ffmpeg is not installed or not in your PATH.", fg="red")
        click.secho("Please install ffmpeg to use this script.", fg="red")
        sys.exit(1)

    click.echo(f"Scanning for video files in: {directory.resolve()}")

    video_files = [
        f for f in directory.rglob("*") if f.suffix.lower() in VIDEO_EXTENSIONS
    ]

    if not video_files:
        click.secho("No video files found.", fg="yellow")
        return

    click.echo(f"Found {len(video_files)} video file(s). Starting normalization...")

    success_count = 0
    skipped_count = 0
    error_count = 0

    with click.progressbar(video_files, label="Processing files") as bar:
        for file_path in bar:
            marker_file = file_path.with_suffix(file_path.suffix + ".normalized")

            if marker_file.exists():
                click.echo(f"\nSkipping (already normalized): {file_path.name}")
                skipped_count += 1
                continue

            click.echo(f"\nProcessing: {file_path.name}")

            # 1. Analyze the file
            stats = get_loudness_stats(file_path)
            if not stats:
                error_count += 1
                continue

            # 2. Apply normalization to a temporary file
            temp_output_path = file_path.with_suffix(
                file_path.suffix + ".temp_normalized"
            )
            success = apply_normalization(file_path, temp_output_path, stats)

            if not success:
                error_count += 1
                # Clean up temp file on failure
                if temp_output_path.exists():
                    temp_output_path.unlink()
                continue

            # 3. Replace original with normalized file and create marker
            try:
                shutil.move(str(temp_output_path), str(file_path))
                marker_file.touch()
                click.secho(f"  Successfully normalized: {file_path.name}", fg="green")
                success_count += 1
            except Exception as e:
                click.secho(f"  Error replacing file {file_path.name}: {e}", fg="red")
                error_count += 1

    click.echo("\n" + "=" * 20)
    click.echo("Normalization Complete")
    click.echo("=" * 20)
    click.secho(f"Successfully normalized: {success_count}", fg="green")
    click.secho(f"Skipped (already done): {skipped_count}", fg="yellow")
    click.secho(f"Errors: {error_count}", fg="red")


if __name__ == "__main__":
    main()

