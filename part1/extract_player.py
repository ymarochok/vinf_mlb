#!/usr/bin/env python3
"""Extract MLB player biography/characteristics from local saved HTML files.

Default (process every stored_players/**/page.html):
    python3 extract_player.py

Choose input directory and output CSV:
    python3 extract_player.py stored_players players.csv

Still supports a single file:
    python3 extract_player.py stored_players/819265/page.html player.csv

Uses regex and Python standard library only. No browsing, downloads or APIs.
"""

import argparse
import csv
import html
import re
import sys
from datetime import datetime
from pathlib import Path


COLUMNS = [
    "player_id", "name", "full_name", "nickname", "birth_date", "birthplace",
    "death_date", "debut_date", "position_code", "bats", "throws",
    "height", "weight_lb", "last_played_year", "last_associated_team",
    "is_active", "teams_from_transactions",
]


def text_only(fragment):
    """Strip tags and unescape entities; normalize whitespace."""
    return " ".join(html.unescape(re.sub(r"<[^>]*>", " ", fragment, flags=re.S)).split())


def capture(pattern, source, flags=re.S):
    match = re.search(pattern, source, flags)
    return text_only(match.group(1)) if match else ""


def iso_date(value):
    """Convert a date like 10/01/1912 to 1912-10-01; leave others as-is."""
    try:
        return datetime.strptime(value, "%m/%d/%Y").date().isoformat()
    except ValueError:
        return value


def extract_player(page):
    """Return one player's 17-column record. Empty strings represent absent data."""
    # Restrict name extraction to the player header, not site-wide JS dictionaries.
    header_match = re.search(
        r'<div class="player-header--vitals[^\"]*"[^>]*>(.*?)</div>', page, re.S
    )
    header_html = header_match.group(1) if header_match else ""
    name = capture(r'<span class="player-header--vitals-name">(.*?)</span>', header_html)
    if not name:
        # Some captured pages may contain metadata but not a fully rendered header.
        name = capture(r'<title>\s*(.*?)\s+Stats,\s*Age', page)

    player_id = capture(r"\bplayerId:\s*'([0-9]+)'", page)
    if not player_id:
        player_id = capture(
            r'<link\s+rel="canonical"\s+href="https://www\.mlb\.com/player/[^\"]*?-([0-9]+)"',
            page,
        )
    if not name or not player_id:
        raise ValueError("Could not identify an MLB player name and ID")

    # Not every downloaded page includes an expanded bio; missing fields stay blank.
    bio_match = re.search(r'<div class="player-bio"[^>]*>(.*?)</div>', page, re.S)
    bio = {}
    if bio_match:
        for li_html in re.findall(r'<li\b[^>]*>(.*?)</li>', bio_match.group(1), re.S):
            match = re.search(
                r'<span\b[^>]*class="label"[^>]*>(.*?)</span>(.*)', li_html, re.S
            )
            if match:
                field = text_only(match.group(1)).rstrip(":").lower()
                bio[field] = text_only(match.group(2))

    born = bio.get("born", "")
    birth_match = re.match(r"(\d{1,2}/\d{1,2}/\d{4})(?:\s+in\s+(.+))?", born)
    birth_date = iso_date(birth_match.group(1)) if birth_match else ""
    birthplace = (birth_match.group(2) or "") if birth_match else ""

    position = capture(r'<ul\b[^>]*>\s*<li\b[^>]*>(.*?)</li>', header_html)
    bt = re.search(r'B/T:\s*([RLS])\s*/\s*([RLS])', text_only(header_html))
    side = {"R": "Right", "L": "Left", "S": "Switch"}

    height_weight = capture(r'<li class="player-header--vitals-height">(.*?)</li>', header_html)
    hw = re.match(r'(\d+)\s*\'\s*(\d+)\s*"?\s*/\s*(\d+)', height_weight)
    height = f"{hw.group(1)}'{hw.group(2)}\"" if hw else ""
    weight = hw.group(3) if hw else ""

    transactions = re.search(r'<section id="transactions"[^>]*>(.*?)</section>', page, re.S)
    teams = []
    if transactions:
        # MLB displays newest entries first. Reverse to list older teams first.
        rows = re.findall(r'<tr\b[^>]*>(.*?)</tr>', transactions.group(1), re.S)
        for row in reversed(rows):
            cells = re.findall(r'<td\b[^>]*>(.*?)</td>', row, re.S)
            if len(cells) < 3:
                continue
            description = text_only(cells[2])
            match = re.fullmatch(r'(.+?) activated ' + re.escape(name) + r'\.', description)
            if match and match.group(1) not in teams:
                teams.append(match.group(1))

    return {
        "player_id": player_id,
        "name": name,
        "full_name": bio.get("fullname", ""),
        "nickname": bio.get("nickname", ""),
        "birth_date": birth_date,
        "birthplace": birthplace,
        "death_date": iso_date(bio.get("died", "")),
        "debut_date": iso_date(bio.get("debut", "")),
        "position_code": position,
        "bats": side.get(bt.group(1), bt.group(1)) if bt else "",
        "throws": side.get(bt.group(2), bt.group(2)) if bt else "",
        "height": height,
        "weight_lb": weight,
        "last_played_year": capture(r"\blastPlayedYear:\s*'([0-9]{4})'", page),
        "last_associated_team": capture(r"\bplayerTeamName:\s*'([^']*)'", page),
        "is_active": capture(r'\bplayerIsActive:\s*(true|false)', page),
        "teams_from_transactions": " | ".join(teams),
    }


def main():
    parser = argparse.ArgumentParser(description="Extract MLB player records from saved HTML")
    parser.add_argument("input", nargs="?", default="stored_players",
                        help="Root directory with ID/page.html files, or one HTML file")
    parser.add_argument("output", nargs="?", default=None,
                        help="Combined CSV filename (default: players.csv)")
    parser.add_argument("--failed", default="failed_pages.csv",
                        help="CSV listing files we couldn't extract")
    args = parser.parse_args()

    input_path = Path(args.input)
    if input_path.is_file():
        pages = [input_path]
        output_path = Path(args.output or "player.csv")
    elif input_path.is_dir():
        pages = sorted(p for p in input_path.rglob("page.html") if p.is_file())
        output_path = Path(args.output or "players.csv")
    else:
        parser.error(f"Input not found: {input_path}")

    if not pages:
        parser.error(f"No page.html files found under {input_path}")

    failure_path = Path(args.failed)
    if output_path.resolve() == failure_path.resolve():
        parser.error("Output CSV and failure log must be different files")

    succeeded = 0
    failed = 0
    partially_filled = 0
    bytes_total = 0
    seen_ids = set()

    with (output_path.open("w", encoding="utf-8", newline="") as output_csv,
          failure_path.open("w", encoding="utf-8", newline="") as failure_csv):
        writer = csv.DictWriter(output_csv, fieldnames=COLUMNS)
        error_writer = csv.writer(failure_csv)
        writer.writeheader()
        error_writer.writerow(["file_path", "error"])

        for count, page_path in enumerate(pages, start=1):
            try:
                bytes_total += page_path.stat().st_size
                page_html = page_path.read_text(encoding="utf-8", errors="replace")
                record = extract_player(page_html)
                if record["player_id"] in seen_ids:
                    raise ValueError(f"Duplicate player ID: {record['player_id']}")
                seen_ids.add(record["player_id"])
                if not record["birth_date"] or not record["full_name"]:
                    partially_filled += 1
                writer.writerow(record)
                succeeded += 1
            except (OSError, ValueError, UnicodeError) as exc:
                error_writer.writerow([str(page_path), str(exc)])
                failed += 1

            if count % 500 == 0 or count == len(pages):
                print(f"[{count}/{len(pages)}] extracted={succeeded}, failed={failed}", flush=True)

    print(f"\nCompleted. HTML files found: {len(pages)}")
    print(f"Successful player rows: {succeeded}")
    print(f"Failed/duplicate files: {failed}")
    print(f"Rows missing full name or birth date: {partially_filled}")
    print(f"Total HTML size: {bytes_total / (1024 * 1024):.2f} MiB")
    print(f"CSV: {output_path}")
    print(f"Failure log: {failure_path}")
    if succeeded == 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
