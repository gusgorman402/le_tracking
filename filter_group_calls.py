#!/usr/bin/env python3

import argparse
import csv
import sys
from pathlib import Path


ALLOWED_EVENTS = {
    "Group Call",
    "Encrypted Group Call",
}

REQUIRED_COLUMNS = {
    "DURATION_MS",
    "EVENT",
    "FROM",
    "EVENT_ID",
}


def parse_duration(value, record_number):
    """
    Convert DURATION_MS to an integer.

    A blank duration is ranked below every numeric duration. If all
    matching records have blank durations, the latest record wins.
    """
    value = value.strip()

    if value == "":
        return -1

    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"record {record_number}: "
            f"invalid DURATION_MS value {value!r}"
        )


def filter_csv(filename):
    # Dictionary:
    #   key   = (FROM, EVENT_ID)
    #   value = (duration, input_position, complete_row)
    best_rows = {}

    # Opening with "r" makes the input file read-only.
    with filename.open(
        "r",
        encoding="utf-8-sig",
        newline="",
    ) as input_file:

        reader = csv.DictReader(input_file)

        if reader.fieldnames is None:
            raise ValueError("input file does not have a CSV header")

        missing_columns = REQUIRED_COLUMNS.difference(
            reader.fieldnames
        )

        if missing_columns:
            missing = ", ".join(sorted(missing_columns))
            raise ValueError(
                f"missing required CSV column(s): {missing}"
            )

        for input_position, row in enumerate(reader, start=1):
            event = row["EVENT"].strip()

            # Ignore all other event types.
            if event not in ALLOWED_EVENTS:
                continue

            duration = parse_duration(
                row["DURATION_MS"],
                input_position,
            )

            key = (
                row["FROM"],
                row["EVENT_ID"],
            )

            previous = best_rows.get(key)

            # Use >= so the latest record replaces the earlier record
            # when both have the same maximum DURATION_MS.
            if previous is None or duration >= previous[0]:
                best_rows[key] = (
                    duration,
                    input_position,
                    row,
                )

        fieldnames = reader.fieldnames

    writer = csv.DictWriter(
        sys.stdout,
        fieldnames=fieldnames,
        lineterminator="\n",
    )

    writer.writeheader()

    # Print selected records in the order their winning versions
    # appeared in the input file.
    selected_rows = sorted(
        best_rows.values(),
        key=lambda item: item[1],
    )

    for duration, input_position, row in selected_rows:
        writer.writerow(row)


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Keep Group Call and Encrypted Group Call records, "
            "retaining the latest record with the highest "
            "DURATION_MS for each FROM and EVENT_ID combination."
        )
    )

    parser.add_argument(
        "csv_file",
        type=Path,
        help="input CSV filename",
    )

    args = parser.parse_args()

    try:
        filter_csv(args.csv_file)
    except (OSError, csv.Error, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
