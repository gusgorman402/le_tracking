import argparse
import math
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from pydub import AudioSegment
from pydub.silence import detect_nonsilent


FILENAME_RE = re.compile(r"^(\d{8})(\d{4})-([^-]+)-([^-]+)\.wav$", re.IGNORECASE)


def parse_source_filename(path):
    match = FILENAME_RE.match(path.name)
    if not match:
        raise ValueError(
            "Input filename must match YYYYMMDDTTTT-randomID-feedID.wav, "
            "for example 202606111722-681200-34786.wav"
        )

    date_part, time_part, random_id, feed_id = match.groups()

    try:
        delayed_time = datetime.strptime(date_part + time_part, "%Y%m%d%H%M")
    except ValueError as exc:
        raise ValueError("Filename contains an invalid date or time") from exc

    recording_start = delayed_time - timedelta(minutes=30)
    return recording_start, random_id, feed_id


def rounded_start_seconds(start_ms, mode):
    seconds = start_ms / 1000

    if mode == "floor":
        return math.floor(seconds)
    if mode == "ceil":
        return math.ceil(seconds)

    return int(round(seconds))


def make_output_name(recording_start, audio_start_ms, audio_end_ms, exported_length_ms, random_id, feed_id, start_rounding, filename_duration):
    start_seconds = rounded_start_seconds(audio_start_ms, start_rounding)
    segment_start_time = recording_start + timedelta(seconds=start_seconds)

    if filename_duration == "exported":
        length_ms = exported_length_ms
    else:
        length_ms = audio_end_ms - audio_start_ms

    segment_length_seconds = max(1, math.ceil(length_ms / 1000))
    timestamp = segment_start_time.strftime("%Y%m%d%H%M%S")
    return f"{timestamp}-{segment_length_seconds:04d}-{random_id}-{feed_id}.wav"


def unique_path(output_dir, filename, seen_names):
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    seen_names[filename] += 1

    if seen_names[filename] == 1:
        return output_dir / filename

    return output_dir / f"{stem}-{seen_names[filename]:02d}{suffix}"


def matching_silence(duration_ms, source_audio):
    return (
        AudioSegment.silent(duration=duration_ms, frame_rate=source_audio.frame_rate)
        .set_channels(source_audio.channels)
        .set_sample_width(source_audio.sample_width)
    )


def build_segments(audio, ranges, pre_roll_ms, post_roll_ms):
    segments = []

    for index, (audio_start_ms, audio_end_ms) in enumerate(ranges):
        previous_audio_end_ms = ranges[index - 1][1] if index > 0 else 0
        next_audio_start_ms = ranges[index + 1][0] if index < len(ranges) - 1 else len(audio)

        real_silence_before_ms = max(0, audio_start_ms - previous_audio_end_ms)
        real_pre_roll_ms = min(pre_roll_ms, real_silence_before_ms)
        missing_pre_roll_ms = pre_roll_ms - real_pre_roll_ms
        export_start_ms = audio_start_ms - real_pre_roll_ms

        real_silence_after_ms = max(0, next_audio_start_ms - audio_end_ms)
        real_post_roll_ms = min(post_roll_ms, real_silence_after_ms)
        missing_post_roll_ms = post_roll_ms - real_post_roll_ms
        export_end_ms = audio_end_ms + real_post_roll_ms

        exported_length_ms = missing_pre_roll_ms + (export_end_ms - export_start_ms) + missing_post_roll_ms

        segments.append(
            {
                "audio_start_ms": audio_start_ms,
                "audio_end_ms": audio_end_ms,
                "export_start_ms": export_start_ms,
                "export_end_ms": export_end_ms,
                "missing_pre_roll_ms": missing_pre_roll_ms,
                "missing_post_roll_ms": missing_post_roll_ms,
                "exported_length_ms": exported_length_ms,
            }
        )

    return segments


def split_wav(args):
    input_path = Path(args.input_wav).expanduser().resolve()

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    recording_start, random_id, feed_id = parse_source_filename(input_path)
    output_dir = Path(args.output_dir).expanduser().resolve() if args.output_dir else input_path.with_name(input_path.stem + "_segments")
    output_dir.mkdir(parents=True, exist_ok=True)

    audio = AudioSegment.from_wav(input_path)
    ranges = detect_nonsilent(
        audio,
        min_silence_len=args.min_silence_ms,
        silence_thresh=args.silence_thresh_db,
        seek_step=args.seek_step_ms,
    )

    ranges = [
        [start, end]
        for start, end in ranges
        if end - start >= args.min_segment_ms
    ]

    if not ranges:
        print("No non-silent audio segments found. Try lowering --silence-thresh-db, for example -55 or -60.")
        return 0

    segments = build_segments(audio, ranges, args.pre_roll_ms, args.post_roll_ms)
    seen_names = defaultdict(int)

    print(f"Input file: {input_path.name}")
    print(f"Recording start after 30-minute correction: {recording_start:%Y-%m-%d %H:%M:%S}")
    print(f"Detected segments: {len(segments)}")
    print(f"Pre-roll before each audible segment: {args.pre_roll_ms} ms")
    print(f"Post-roll after each audible segment: {args.post_roll_ms} ms")
    print(f"Output directory: {output_dir}")

    for index, segment_info in enumerate(segments, start=1):
        filename = make_output_name(
            recording_start,
            segment_info["audio_start_ms"],
            segment_info["audio_end_ms"],
            segment_info["exported_length_ms"],
            random_id,
            feed_id,
            args.start_rounding,
            args.filename_duration,
        )
        output_path = unique_path(output_dir, filename, seen_names)
        audio_start_seconds = segment_info["audio_start_ms"] / 1000
        audio_end_seconds = segment_info["audio_end_ms"] / 1000
        export_start_seconds = segment_info["export_start_ms"] / 1000
        export_end_seconds = segment_info["export_end_ms"] / 1000
        audible_length_seconds = math.ceil((segment_info["audio_end_ms"] - segment_info["audio_start_ms"]) / 1000)
        exported_length_seconds = math.ceil(segment_info["exported_length_ms"] / 1000)

        if args.dry_run:
            print(
                f"{index:04d}: audio {audio_start_seconds:.3f}s to {audio_end_seconds:.3f}s, "
                f"export {export_start_seconds:.3f}s to {export_end_seconds:.3f}s, "
                f"prepend {segment_info['missing_pre_roll_ms']} ms silence, "
                f"append {segment_info['missing_post_roll_ms']} ms silence, "
                f"audible {audible_length_seconds}s, exported {exported_length_seconds}s -> {output_path.name}"
            )
            continue

        if output_path.exists() and not args.overwrite:
            raise FileExistsError(f"Output file already exists: {output_path}. Use --overwrite to replace files.")

        output_audio = audio[segment_info["export_start_ms"]:segment_info["export_end_ms"]]

        if segment_info["missing_pre_roll_ms"] > 0:
            output_audio = matching_silence(segment_info["missing_pre_roll_ms"], audio) + output_audio

        if segment_info["missing_post_roll_ms"] > 0:
            output_audio = output_audio + matching_silence(segment_info["missing_post_roll_ms"], audio)

        output_audio.export(output_path, format="wav")
        print(f"{index:04d}: saved {output_path.name}")

    return len(segments)


def build_parser():
    parser = argparse.ArgumentParser(
        description="Split a Broadcastify-style WAV archive into separate non-silent WAV segments with pre-roll and post-roll silence."
    )
    parser.add_argument("input_wav", help="Input WAV named like YYYYMMDDTTTT-randomID-feedID.wav")
    parser.add_argument("--output-dir", help="Directory where extracted WAV files will be saved")
    parser.add_argument("--silence-thresh-db", type=float, default=-50.0, help="Audio below this dBFS value is treated as silence. Default: -50")
    parser.add_argument("--min-silence-ms", type=int, default=700, help="Minimum silence length that separates segments. Default: 700")
    parser.add_argument("--min-segment-ms", type=int, default=250, help="Ignore detected audio shorter than this. Default: 250")
    parser.add_argument("--seek-step-ms", type=int, default=10, help="Silence scan step in milliseconds. Lower is more precise but slower. Default: 10")
    parser.add_argument("--pre-roll-ms", type=int, default=1000, help="Milliseconds of silence to include before each audible segment. Default: 1000")
    parser.add_argument("--post-roll-ms", type=int, default=500, help="Milliseconds of silence to include after each audible segment. Default: 500")
    parser.add_argument("--filename-duration", choices=["audible", "exported"], default="audible", help="Use audible duration or total exported file duration in output filenames. Default: audible")
    parser.add_argument("--start-rounding", choices=["round", "floor", "ceil"], default="round", help="How to round segment start time for filenames. Default: round")
    parser.add_argument("--dry-run", action="store_true", help="Print planned output filenames without writing files")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing files")
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        split_wav(args)
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
