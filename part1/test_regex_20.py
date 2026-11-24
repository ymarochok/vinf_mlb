#!/usr/bin/env python3
"""Bonus VINF: two unittest methods; primary one verifies 20 randomly selected
REAL saved MLB player HTML pages, with multiple assertions per page.

Run from the project directory:
    python3 -m unittest -v test_regex_20.py

Optional variables:
    MLB_PLAYERS_DIR=/other/path/stored_players
    MLB_PLAYERS_CSV=/other/path/players.csv
    MLB_TEST_SEED=12345

The fixed default seed makes the random selection reproducible. Change it to
select another random sample. There is no network access or synthetic HTML.
"""
import csv
import html
import os
import random
import re
import unittest
from datetime import date
from pathlib import Path

from extract_player import COLUMNS, extract_player

ROOT = Path(os.environ.get('MLB_PLAYERS_DIR', 'stored_players'))
CSV_FILE = Path(os.environ.get('MLB_PLAYERS_CSV', 'players.csv'))
SEED = int(os.environ.get('MLB_TEST_SEED', '20261027'))
FAILED_ID = '645261'  # failed_pages.csv from actual batch extraction


class TestRegexOnRandomPages(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Select from successfully extracted IDs to evaluate REGEX consistency.
        # The specifically known invalid profile is checked by the other test.
        cls.csv_rows = {}
        if CSV_FILE.is_file():
            with CSV_FILE.open(encoding='utf-8-sig', newline='') as handle:
                cls.csv_rows = {
                    row['player_id']: row
                    for row in csv.DictReader(handle)
                    if row.get('player_id')
                }

        if cls.csv_rows:
            candidates = [ROOT / pid / 'page.html' for pid in cls.csv_rows]
            candidates = [p for p in candidates if p.is_file()]
        else:
            candidates = [p for p in ROOT.glob('*/page.html')
                          if p.parent.name.isdigit() and p.parent.name != FAILED_ID]

        candidates = sorted(set(candidates), key=lambda p: (int(p.parent.name), str(p)))
        if len(candidates) < 20:
            raise AssertionError(
                f'At least 20 real saved player HTML pages are required; '
                f'found {len(candidates)} in {ROOT.resolve()}'
            )
        cls.pages = random.Random(SEED).sample(candidates, 20)
        selected_ids = [p.parent.name for p in cls.pages]
        if len(set(selected_ids)) != 20:
            raise AssertionError('Expected 20 distinct player IDs')
        print(f'\nRandomly selected 20 distinct player pages (seed={SEED}): '
              + ', '.join(selected_ids), flush=True)

    def test_20_random_html_profiles(self):
        """One unittest method, 20 independent page checks (subTests)."""
        for path in self.pages:
            with self.subTest(player_id=path.parent.name):
                source = path.read_text(encoding='utf-8', errors='replace')
                player = extract_player(source)

                # Exactly the expected 17 attributes, nonempty identity/position.
                self.assertEqual(set(player), set(COLUMNS))
                self.assertEqual(player['player_id'], path.parent.name)
                self.assertTrue(player['name'].strip())
                self.assertTrue(player['position_code'].strip())

                # Compare to independent HTML markers wherever present.
                canonical = re.search(
                    r'<link\s+rel="canonical"\s+href="https://www\.mlb\.com/player/[^\"]*?-(\d+)"',
                    source,
                )
                if canonical:
                    self.assertEqual(player['player_id'], canonical.group(1))

                title = re.search(r'<title>\s*(.*?)\s+Stats,\s*Age', source, re.S)
                if title:
                    self.assertEqual(player['name'], html.unescape(title.group(1).strip()))

                # Optional dates may be missing; present values must be real dates.
                for key in ('birth_date', 'death_date', 'debut_date'):
                    if player[key]:
                        self.assertRegex(player[key], r'^\d{4}-\d{2}-\d{2}$')
                        date.fromisoformat(player[key])

                # CSV regression: extracted row should match the saved batch output.
                # NOTE: this is consistency testing, NOT independent fact-checking.
                saved = self.csv_rows.get(path.parent.name)
                if saved:
                    for key in COLUMNS:
                        self.assertEqual(player[key], saved.get(key, ''),
                                         f'{path.parent.name}: CSV field {key} differs')

    def test_known_failed_profile(self):
        """Known bad profile should fail explicitly, not create a false player."""
        path = ROOT / FAILED_ID / 'page.html'
        if not path.is_file():
            self.skipTest(f'{path} not present on this machine')
        source = path.read_text(encoding='utf-8', errors='replace')
        with self.assertRaisesRegex(ValueError, 'Could not identify an MLB player name and ID'):
            extract_player(source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
