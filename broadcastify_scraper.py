import argparse
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlparse

import requests

#create .env file with the following lines:
#BROADCASTIFY_USERNAME=yourusername
#BROADCASTIFY_PASSWORD=yourpassword

#usage: python3 test_updated.py feedID archiveDate --env-file /path/to/.env
#usage_example: python3 test_updated.py 34786 06/11/2026 --env-file /path/to/.env


ARCHIVE_DOWNLOAD_URL = "https://www.broadcastify.com/archives/download/"
ARCHIVE_API_URL = "https://www.broadcastify.com/archives/api/archives.php"
LOGIN_URL = "https://www.broadcastify.com/login/"
DATE_PATTERN = re.compile(r"^\d{2}/\d{2}/\d{4}$")


def load_env_file(env_file):
    env_path = Path(env_file)
    if not env_path.exists():
        raise FileNotFoundError(f"Missing env file: {env_path}")

    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def get_credentials():
    username = os.getenv("BROADCASTIFY_USERNAME")
    password = os.getenv("BROADCASTIFY_PASSWORD")

    if not username or not password:
        raise RuntimeError(
            "BROADCASTIFY_USERNAME and BROADCASTIFY_PASSWORD must be set in your .env file"
        )

    return username, password


def parse_archive_date(value):
    if "-" in value:
        raise argparse.ArgumentTypeError(
            "archiveDate must use slashes, not hyphens. Example: 06/11/2026"
        )

    if not DATE_PATTERN.match(value):
        raise argparse.ArgumentTypeError(
            "archiveDate must be in MM/DD/YYYY format. Example: 06/11/2026"
        )

    try:
        parsed_date = datetime.strptime(value, "%m/%d/%Y")
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "archiveDate is not a valid calendar date. Example: 06/11/2026"
        ) from exc

    return parsed_date


def get_args():
    parser = argparse.ArgumentParser(
        description="Download all Broadcastify MP3 archives for one feed and one day."
    )
    parser.add_argument("feedID", help="Broadcastify feed ID. Example: 34786")
    parser.add_argument(
        "archiveDate",
        type=parse_archive_date,
        help="Archive date in MM/DD/YYYY format. Example: 06/11/2026",
    )
    parser.add_argument("--env-file", default=".env", help="Path to .env file")
    parser.add_argument("--output-dir", default=".", help="Directory where MP3 files are saved")
    return parser.parse_args()


def login(session, username, password):
    response = session.get(LOGIN_URL, timeout=30)
    response.raise_for_status()

    if username in response.text:
        print("Already logged in")
        return

    print("Not logged in. Attempting new login")
    response = session.post(
        LOGIN_URL,
        data={
            "username": username,
            "password": password,
            "action": "auth",
            "redirect": "/",
        },
        timeout=30,
    )
    response.raise_for_status()

    if username not in response.text:
        raise RuntimeError("Login failed")

    print("Login successful")


def get_archive_list(session, feed_id, archive_date):
    response = session.get(
        ARCHIVE_API_URL,
        params={
            "feedId": feed_id,
            "date": archive_date.strftime("%m/%d/%Y"),
        },
        timeout=60,
    )
    response.raise_for_status()

    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Archive API did not return JSON: {response.text[:500]}") from exc

    if "archives" not in data:
        raise RuntimeError(f"Archive API response did not contain archives: {data}")

    print("Recordings JSON list downloaded")
    return data["archives"]


def filename_from_url(url):
    name = Path(urlparse(url).path).name
    return unquote(name) if name else "archive.mp3"


def download_mp3(session, archive_id, output_dir):
    print(f"Downloading mp3 for {archive_id}")
    response = session.get(
        f"{ARCHIVE_DOWNLOAD_URL}{archive_id}",
        allow_redirects=True,
        stream=True,
        timeout=120,
    )
    response.raise_for_status()

    content_type = response.headers.get("content-type", "").lower()
    if "text/html" in content_type:
        raise RuntimeError(f"Expected MP3 but received HTML for archive ID {archive_id}")

    mp3_file = filename_from_url(response.url)
    if not mp3_file.lower().endswith(".mp3"):
        mp3_file = f"{archive_id}.mp3"

    output_path = output_dir / mp3_file
    temp_path = output_path.with_suffix(output_path.suffix + ".part")

    with open(temp_path, "wb") as file_handle:
        for chunk in response.iter_content(chunk_size=1024 * 256):
            if chunk:
                file_handle.write(chunk)

    temp_path.rename(output_path)
    print(f"File saved to: {output_path}")


def main():
    args = get_args()
    load_env_file(args.env_file)
    username, password = get_credentials()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    session = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0"})

    login(session, username, password)
    archives = get_archive_list(session, args.feedID, args.archiveDate)
    downloaded_ids = set()

    print("***********************************************")

    for archive in archives:
        archive_id = archive["id"]
        if archive_id in downloaded_ids:
            continue
        downloaded_ids.add(archive_id)
        download_mp3(session, archive_id, output_dir)


if __name__ == "__main__":
    main()
