#!/usr/bin/env python3
"""Associate timestamped CSV FROM values with timestamped audio filenames.

Accepted command-line forms:

    python3 associate_audio_timestamps.py AUDIO_LIST CSV_LOG

or:

    python3 associate_audio_timestamps.py AUDIO_LIST MIN_SECONDS CSV_LOG

or:

    python3 associate_audio_timestamps.py AUDIO_LIST MIN_SECONDS START_SUBTRACT CSV_LOG

or the full five-argument form:

    python3 associate_audio_timestamps.py AUDIO_LIST MIN_SECONDS START_SUBTRACT END_ADD CSV_LOG

Defaults:
    MIN_SECONDS   = 3
    START_SUBTRACT = 30
    END_ADD        = 30

CSV output is written to standard output. Redirect it to a file with > output.csv.
"""

from __future__ import annotations

import bisect
import csv
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import TextIO


DEFAULT_MIN_SECONDS = 3
DEFAULT_START_SUBTRACT = 30
DEFAULT_END_ADD = 30

# Example:
# 20260707140311-0025-167460-34786.wav
#  timestamp      duration random  feed
FILENAME_RE = re.compile(
    r"^(?P<timestamp>\d{14})-(?P<duration>\d+)-[^-]+-[^-]+\.[^.]+$"
)

AUDIO_TIMESTAMP_FORMAT = "%Y%m%d%H%M%S"
CSV_TIMESTAMP_FORMAT = "%Y:%m:%d:%H:%M:%S"


@dataclass(frozen=True)
class AudioPeriod:
    filename: str
    start: datetime
    end: datetime


def usage(program: str) -> str:
    return f"""Usage:
  {program} AUDIO_LIST CSV_LOG
  {program} AUDIO_LIST MIN_SECONDS CSV_LOG
  {program} AUDIO_LIST MIN_SECONDS START_SUBTRACT CSV_LOG
  {program} AUDIO_LIST MIN_SECONDS START_SUBTRACT END_ADD CSV_LOG

Arguments:
  AUDIO_LIST       Text file containing one audio path or filename per line
  MIN_SECONDS      Minimum audio duration to analyze (default: 3)
  START_SUBTRACT   Seconds to subtract from each audio start (default: 30)
  END_ADD          Seconds to add after each audio end (default: 30)
  CSV_LOG          CSV file containing TIMESTAMP and FROM columns

Example:
  {program} mp3_file_list.txt 3 30 30 sarpy_log.csv > matches.csv
"""


def parse_nonnegative_number(value: str, name: str) -> float:
    try:
        number = float(value)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number: {value!r}") from exc

    if number < 0:
        raise ValueError(f"{name} cannot be negative: {value!r}")
    return number


def parse_arguments(argv: list[str]) -> tuple[Path, float, float, float, Path]:
    """Parse 2-5 user arguments while keeping the CSV file as the final one."""
    if any(arg in {"-h", "--help"} for arg in argv[1:]):
        print(usage(argv[0]))
        raise SystemExit(0)

    args = argv[1:]
    if not 2 <= len(args) <= 5:
        print(usage(argv[0]), file=sys.stderr)
        raise SystemExit(2)

    audio_list = Path(args[0])
    csv_log = Path(args[-1])

    numeric_args = args[1:-1]
    defaults = [
        float(DEFAULT_MIN_SECONDS),
        float(DEFAULT_START_SUBTRACT),
        float(DEFAULT_END_ADD),
    ]
    names = ["MIN_SECONDS", "START_SUBTRACT", "END_ADD"]

    for index, value in enumerate(numeric_args):
        defaults[index] = parse_nonnegative_number(value, names[index])

    min_seconds, start_subtract, end_add = defaults
    return audio_list, min_seconds, start_subtract, end_add, csv_log


def read_audio_periods(
    audio_list_path: Path,
    min_seconds: float,
    start_subtract: float,
    end_add: float,
) -> list[AudioPeriod]:
    periods: list[AudioPeriod] = []

    with audio_list_path.open("r", encoding="utf-8-sig", errors="replace") as infile:
        for line_number, line in enumerate(infile, start=1):
            path_text = line.strip()
            if not path_text:
                continue

            # Handles both Unix and Windows path separators, regardless of the
            # operating system on which this script is run.
            filename = re.split(r"[/\\]", path_text)[-1]
            match = FILENAME_RE.fullmatch(filename)
            if match is None:
                print(
                    f"Warning: ignoring unrecognized filename on line "
                    f"{line_number}: {path_text}",
                    file=sys.stderr,
                )
                continue

            duration_seconds = int(match.group("duration"))
            if duration_seconds < min_seconds:
                continue

            try:
                initial_start = datetime.strptime(
                    match.group("timestamp"), AUDIO_TIMESTAMP_FORMAT
                )
            except ValueError as exc:
                print(
                    f"Warning: ignoring invalid timestamp on line "
                    f"{line_number}: {filename} ({exc})",
                    file=sys.stderr,
                )
                continue

            adjusted_start = initial_start - timedelta(seconds=start_subtract)
            adjusted_end = (
                initial_start
                + timedelta(seconds=duration_seconds)
                + timedelta(seconds=end_add)
            )

            periods.append(
                AudioPeriod(
                    filename=filename,
                    start=adjusted_start,
                    end=adjusted_end,
                )
            )

    return periods


def normalized_headers(fieldnames: list[str] | None) -> dict[str, str]:
    """Map normalized uppercase header names to their original spelling."""
    if not fieldnames:
        return {}
    return {name.strip().upper(): name for name in fieldnames if name is not None}


def read_csv_timestamp_rows(csv_path: Path) -> tuple[list[datetime], list[str]]:
    timestamp_and_from: list[tuple[datetime, str]] = []

    with csv_path.open("r", encoding="utf-8-sig", errors="replace", newline="") as infile:
        reader = csv.DictReader(infile)
        headers = normalized_headers(reader.fieldnames)

        if "TIMESTAMP" not in headers or "FROM" not in headers:
            actual = ", ".join(reader.fieldnames or []) or "(no header found)"
            raise ValueError(
                "CSV must contain TIMESTAMP and FROM columns. "
                f"Found: {actual}"
            )

        timestamp_column = headers["TIMESTAMP"]
        from_column = headers["FROM"]

        for row_number, row in enumerate(reader, start=2):
            timestamp_text = (row.get(timestamp_column) or "").strip()
            from_value = (row.get(from_column) or "").strip()

            if not timestamp_text:
                print(
                    f"Warning: CSV row {row_number} has no timestamp; ignoring it",
                    file=sys.stderr,
                )
                continue

            try:
                timestamp = datetime.strptime(timestamp_text, CSV_TIMESTAMP_FORMAT)
            except ValueError as exc:
                print(
                    f"Warning: CSV row {row_number} has an invalid timestamp "
                    f"{timestamp_text!r}; ignoring it ({exc})",
                    file=sys.stderr,
                )
                continue

            # Blank FROM fields are not numbers, so they are omitted.
            if from_value:
                timestamp_and_from.append((timestamp, from_value))

    # Sorting makes the lookup correct even if the input CSV is not ordered.
    # Python's sort is stable, so equal timestamps retain their original order.
    timestamp_and_from.sort(key=lambda item: item[0])

    timestamps = [item[0] for item in timestamp_and_from]
    from_values = [item[1] for item in timestamp_and_from]
    return timestamps, from_values


def find_matches(
    periods: list[AudioPeriod],
    csv_timestamps: list[datetime],
    csv_from_values: list[str],
) -> list[list[str]]:
    output_rows: list[list[str]] = []

    for period in periods:
        # Inclusive range: period.start <= timestamp <= period.end
        first = bisect.bisect_left(csv_timestamps, period.start)
        last = bisect.bisect_right(csv_timestamps, period.end)
        output_rows.append([period.filename, *csv_from_values[first:last]])

    return output_rows


def write_output(rows: list[list[str]], outfile: TextIO) -> None:
    writer = csv.writer(outfile, lineterminator="\n")
    max_from_count = max((len(row) - 1 for row in rows), default=0)
    header = ["AUDIO_FILENAME"] + [
        f"FROM_{number}" for number in range(1, max_from_count + 1)
    ]
    writer.writerow(header)
    writer.writerows(rows)


def main(argv: list[str]) -> int:
    try:
        audio_list, min_seconds, start_subtract, end_add, csv_log = (
            parse_arguments(argv)
        )

        if not audio_list.is_file():
            raise FileNotFoundError(f"Audio-list file not found: {audio_list}")
        if not csv_log.is_file():
            raise FileNotFoundError(f"CSV log file not found: {csv_log}")

        periods = read_audio_periods(
            audio_list,
            min_seconds=min_seconds,
            start_subtract=start_subtract,
            end_add=end_add,
        )
        timestamps, from_values = read_csv_timestamp_rows(csv_log)
        rows = find_matches(periods, timestamps, from_values)
        write_output(rows, sys.stdout)

        print(
            f"Processed {len(periods)} qualifying audio filename(s) and "
            f"{len(timestamps)} timestamped CSV row(s).",
            file=sys.stderr,
        )
        return 0

    except (OSError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
