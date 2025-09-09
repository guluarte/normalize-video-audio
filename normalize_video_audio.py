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
        # Find the JSON output in stderr - it should be the last JSON object
        lines = result.stderr.strip().split("\n")
        json_lines = []
        in_json = False
        
        for line in lines:
            line = line.strip()
            if line.startswith('{'):
                in_json = True
                json_lines = [line]
            elif in_json:
                json_lines.append(line)
                if line.endswith('}'):
                    break
        
        if not json_lines:
            click.secho(
                f"    Could not extract loudness stats for {file_path.name}", fg="red"
            )
            return None
            
        stats_str = '\n'.join(json_lines)
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
    input_path: pathlib.Path, output_path: pathlib.Path, stats: dict, source_stats: dict | None = None
) -> bool:
    """
    Second pass of normalization: Apply the calculated stats to normalize.
    """
    click.echo(f"  Applying normalization to: {input_path.name}")

    # Use source video targets if available, otherwise use standard EBU R128 targets
    target_I = float(source_stats["measured_I"]) if source_stats else LOUDNESS_TARGETS["I"]
    target_LRA = float(source_stats["measured_LRA"]) if source_stats else LOUDNESS_TARGETS["LRA"]
    target_TP = float(source_stats["measured_TP"]) if source_stats else LOUDNESS_TARGETS["TP"]
    
    loudnorm_filter = (
        f"loudnorm=I={target_I}:"
        f"LRA={target_LRA}:"
        f"tp={target_TP}:"
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

    # Display the ffmpeg command
    click.echo(f"    Running command: {' '.join(cmd)}")

    # Run the command and capture output
    result = run_command(cmd)

    # Display ffmpeg output
    if result.stdout:
        click.echo(f"    ffmpeg stdout:\n{result.stdout}")
    if result.stderr:
        click.echo(f"    ffmpeg stderr:\n{result.stderr}")

    if result.returncode != 0:
        click.secho(f"    Error normalizing file: {input_path.name}", fg="red")
        return False

    return True


@click.command()
@click.argument(
    "directory",
    type=click.Path(
        exists=True, file_okay=False, dir_okay=True, path_type=pathlib.Path
    ),
)
@click.option(
    "--dry-run",
    is_flag=True,
    help="Show what would be done without actually modifying files"
)
@click.option(
    "--source-video",
    "-s",
    type=click.Path(exists=True, file_okay=True, dir_okay=False, path_type=pathlib.Path),
    help="Use a source video as the base for normalization (uses its audio characteristics)"
)
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    help="Auto-confirm normalization for all files without prompting"
)
@click.option(
    "--threshold",
    "-t",
    type=float,
    default=2.0,
    help="Loudness threshold in LUFS - only ask to normalize if difference exceeds this value"
)
def main(directory: pathlib.Path, dry_run: bool, source_video: pathlib.Path | None, yes: bool, threshold: float):
    """
    Normalizes audio volume for all video files in a directory and its subdirectories.
    
    If --source-video is provided, uses that video's audio characteristics as the 
    normalization base instead of standard EBU R128 targets.
    
    By default, asks for confirmation (y/n) before normalizing each file only if
    the loudness difference exceeds the threshold. Use --yes to auto-confirm all files.

    This tool requires 'ffmpeg' to be installed and available in your system's PATH.
    """
    if dry_run:
        click.secho("DRY RUN MODE: No files will be modified", fg="yellow", bold=True)
    if not shutil.which("ffmpeg"):
        click.secho("Error: ffmpeg is not installed or not in your PATH.", fg="red")
        click.secho("Please install ffmpeg to use this script.", fg="red")
        sys.exit(1)

    click.echo(f"Scanning for video files in: {directory.resolve()}")

    video_files = [
        f for f in directory.rglob("*") if f.suffix.lower() in VIDEO_EXTENSIONS
    ]
    
    # If source video is provided, get its loudness stats first
    source_stats = None
    if source_video:
        click.echo(f"Using source video as base: {source_video.name}")
        source_stats = get_loudness_stats(source_video)
        if not source_stats:
            click.secho("Error: Could not analyze source video audio", fg="red")
            sys.exit(1)

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

            # 1. Analyze the file (or use source stats if provided)
            if source_stats:
                stats = source_stats
                click.echo(f"  Using source video stats for: {file_path.name}")
            else:
                stats = get_loudness_stats(file_path)
                if not stats:
                    error_count += 1
                    continue
            
            # Calculate loudness difference from target
            target_loudness = float(source_stats["measured_I"]) if source_stats else LOUDNESS_TARGETS["I"]
            loudness_diff = abs(float(stats["measured_I"]) - target_loudness)
            
            # Print stats for every file
            click.echo(f"  Measured loudness: {stats['measured_I']} LUFS")
            click.echo(f"  Target loudness: {target_loudness} LUFS")
            click.echo(f"  Difference: {loudness_diff:.2f} LUFS (threshold: {threshold} LUFS)")
            
            # Skip confirmation if difference is below threshold
            if loudness_diff <= threshold:
                click.echo(f"  Loudness difference within threshold, skipping: {file_path.name}")
                skipped_count += 1
                continue
            
            # Ask for confirmation unless --yes flag is provided
            if not yes and not dry_run:
                response = click.prompt(f"Normalize {file_path.name}? [y/N]", default="n")
                if response.lower() not in ('y', 'yes'):
                    click.echo(f"  Skipping: {file_path.name}")
                    skipped_count += 1
                    continue

            # 2. Apply normalization to a temporary file (skip in dry-run)
            temp_output_path = None
            if dry_run:
                click.secho(f"  [DRY RUN] Would normalize: {file_path.name}", fg="blue")
                success = True
            else:
                temp_output_path = file_path.with_name(
                    file_path.stem + ".temp_normalized" + file_path.suffix
                )
                success = apply_normalization(file_path, temp_output_path, stats, source_stats)

            if not success:
                error_count += 1
                # Clean up temp file on failure
                if not dry_run and temp_output_path and temp_output_path.exists():
                    temp_output_path.unlink()
                continue

            # 3. Replace original with normalized file and create marker (skip in dry-run)
            if dry_run:
                click.secho(f"  [DRY RUN] Would replace original and create marker for: {file_path.name}", fg="blue")
                success_count += 1
            else:
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

