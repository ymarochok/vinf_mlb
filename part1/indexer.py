#!/usr/bin/env python3
"""MLB Player Explorer -- interactive custom inverted-index search CLI.

Run: python3 indexer.py
Data: players.csv -> indexes.json + players_data.json
No external Python packages required.
"""

import codecs
import csv
import json
import math
import os
import re
import shlex
import sys
from collections import Counter, defaultdict
from pathlib import Path

CSV_PATH = Path('players.csv')
INDEX_PATH = Path('indexes.json')
PLAYERS_PATH = Path('players_data.json')
CONFIG_PATH = Path('indexer_config.json')

# Restrict full-text indexing to textual attributes. Numbers/dates remain
# available through structured filters, without bloating the vocabulary.
TEXT_FIELDS = (
    'name', 'nickname', 'birthplace', 'last_associated_team',
    'teams_from_transactions', 'position_code', 'bats', 'throws',
)
DEFAULT_SETTINGS = {'method': 1, 'output_limit': 10, 'colors': True}

# Friendly filter names -> actual headers from our REGEX extractor.
FILTER_FIELDS = {
    'position': 'position_code', 'bats': 'bats', 'throws': 'throws',
    'team': 'last_associated_team', 'teams': 'teams_from_transactions',
    'birthplace': 'birthplace', 'name': 'name', 'nickname': 'nickname',
    'id': 'player_id', 'active': 'is_active', 'born': 'birth_date',
    'birth_year': 'birth_date', 'debut_year': 'debut_date',
    'last_year': 'last_played_year', 'weight': 'weight_lb',
}
NUMERIC_FILTERS = {'weight', 'birth_year', 'debut_year', 'last_year'}


def words(value):
    return re.findall(r'[^\W_]+', str(value).casefold(), flags=re.UNICODE)


def write_json(path, data):
    temp = path.with_name(path.name + '.tmp')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, separators=(',', ':'))
    temp.replace(path)


def build_index(csv_file=CSV_PATH):
    """Creates term -> {player_id: TF}, and a separate player metadata file."""
    inverted = defaultdict(dict)
    players = {}
    skipped = 0
    with Path(csv_file).open('r', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or 'player_id' not in reader.fieldnames:
            raise ValueError('CSV must have a player_id column')

        for record in reader:
            pid = (record.get('player_id') or '').strip()
            if not pid or pid in players:
                skipped += 1
                continue
            players[pid] = record
            counts = Counter()
            for column in TEXT_FIELDS:
                counts.update(words(record.get(column) or ''))
            # Full names sometimes add a middle name; include only words that
            # were not already indexed from the display name.
            display_name_tokens = set(words(record.get('name') or ''))
            counts.update(w for w in words(record.get('full_name') or '')
                          if w not in display_name_tokens)
            for term, tf in counts.items():
                inverted[term][pid] = tf

    if not players:
        raise ValueError('No player rows found')
    write_json(INDEX_PATH, inverted)
    write_json(PLAYERS_PATH, players)
    return dict(inverted), players, skipped


def load_index():
    if not INDEX_PATH.is_file() or not PLAYERS_PATH.is_file():
        return None, None
    with INDEX_PATH.open(encoding='utf-8') as f:
        inverted = json.load(f)
    with PLAYERS_PATH.open(encoding='utf-8') as f:
        players = json.load(f)
    if not isinstance(inverted, dict) or not isinstance(players, dict):
        raise ValueError('Saved index has an invalid format; run /build')
    return inverted, players


def idf(N, df, method):
    if N <= 0 or df <= 0:
        return 0.0
    if method == 1:
        return math.log(N / df)  # Standard IDF
    if method == 2:
        return math.log((N + 1) / (df + 1)) + 1  # Smoothed IDF
    raise ValueError('Method must be 1 or 2')


def parse_query(query):
    """Split free text and attribute filters. Quoted values can have spaces."""
    parts = shlex.split(query)
    free_text, filters = [], []
    for part in parts:
        match = re.fullmatch(r'([a-zA-Z_]+)(>=|<=|=|>|<)(.+)', part)
        if match:
            key, op, value = match.groups()
            key = key.lower()
            if key not in FILTER_FIELDS:
                raise ValueError(f'Unknown filter: {key}. Type /help for available fields.')
            if op != '=' and key not in NUMERIC_FILTERS:
                raise ValueError(f'{key} supports = only')
            filters.append((key, op, value))
        else:
            free_text.append(part)
    return ' '.join(free_text), filters


def normalized_side(value):
    value = value.strip().casefold()
    return {'r': 'right', 'l': 'left', 's': 'switch',
            'switch-hitter': 'switch'}.get(value, value)


def passes_filters(row, filters):
    for key, op, expected in filters:
        actual = (row.get(FILTER_FIELDS[key]) or '').strip()
        if key in NUMERIC_FILTERS:
            if key.endswith('_year'):
                actual = actual[:4]
            try:
                a, b = int(actual), int(expected)
            except ValueError:
                return False
            if not {'=': a == b, '>': a > b, '<': a < b,
                    '>=': a >= b, '<=': a <= b}[op]:
                return False
        elif key == 'position':
            # Position may be saved as "P" or "Pitcher" depending on the page.
            aliases = {'pitcher': 'p', 'catcher': 'c', 'shortstop': 'ss',
                       'first baseman': '1b', 'second baseman': '2b',
                       'third baseman': '3b'}
            a = aliases.get(actual.casefold(), actual.casefold())
            b = aliases.get(expected.casefold(), expected.casefold())
            if a != b:
                return False
        elif key in ('bats', 'throws'):
            if normalized_side(actual) != normalized_side(expected):
                return False
        elif key == 'active':
            def norm_bool(x):
                return {'1': 'true', 'yes': 'true', '0': 'false',
                        'no': 'false'}.get(x.casefold(), x.casefold())
            if norm_bool(actual) != norm_bool(expected):
                return False
        elif key in ('id', 'born'):
            if actual.casefold() != expected.casefold():
                return False
        else:
            # Text filters: case-insensitive substring. For example:
            # team="New York" or birthplace="Liberty, TN".
            if expected.casefold() not in actual.casefold():
                return False
    return True


def query_index(index, players, query, method, limit):
    """Returns ranked (player_id, score) and total matches.

    Free words use inverted posting lists (OR search).
    Filters can further narrow those candidates; filters alone scan player metadata.
    """
    text, filters = parse_query(query)
    terms = set(words(text))
    if not terms and not filters:
        return [], 0

    scores = defaultdict(float)
    if terms:
        for term in terms:
            postings = index.get(term, {})
            if not postings:
                continue
            term_idf = idf(len(players), len(postings), method)
            for pid, tf in postings.items():
                if pid in players:
                    scores[pid] += tf * term_idf
        candidates = scores.keys()
    else:
        candidates = players.keys()

    matching = [(pid, scores.get(pid, 0.0)) for pid in candidates
                if passes_filters(players[pid], filters)]
    matching.sort(key=lambda x: (-x[1],
                (players[x[0]].get('name') or '').casefold(), x[0]))
    return matching[:limit], len(matching)


def clean_input(value):
    """Safety fallback when input isn't an interactive terminal (pipe/IDE)."""
    value = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', value)
    value = re.sub(r'\^\[\[[ABCD]', '', value)
    return value.strip()


def get_line(prompt):
    """Terminal line editor: arrow keys are ignored, never inserted as ^[[A.

    Supports typing, Unicode, Backspace, Enter and Ctrl+C/Ctrl+D.
    Uses a normal input() fallback in non-TTY environments.
    """
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return clean_input(input(prompt))
    if os.name == 'nt':
        import msvcrt
        sys.stdout.write(prompt)
        sys.stdout.flush()
        buffer = []
        while True:
            ch = msvcrt.getwch()
            if ch in ('\x00', '\xe0'):
                msvcrt.getwch()  # swallow arrow/function key
                continue
            if ch in ('\r', '\n'):
                print()
                return clean_input(''.join(buffer))
            if ch == '\x03':
                raise KeyboardInterrupt
            if ch == '\x1a' and not buffer:
                raise EOFError
            if ch in ('\b', '\x7f'):
                if buffer:
                    buffer.pop()
                    sys.stdout.write('\b \b')
            elif ch.isprintable():
                buffer.append(ch)
                sys.stdout.write(ch)
            sys.stdout.flush()

    import select
    import termios
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    attrs = termios.tcgetattr(fd)
    attrs[3] &= ~(termios.ECHO | termios.ICANON)  # keep ISIG for Ctrl+C
    attrs[6][termios.VMIN] = 1
    attrs[6][termios.VTIME] = 0
    chars = []
    decoder = codecs.getincrementaldecoder('utf-8')('replace')
    try:
        termios.tcsetattr(fd, termios.TCSANOW, attrs)
        sys.stdout.write(prompt)
        sys.stdout.flush()
        while True:
            chunk = os.read(fd, 1)
            if not chunk:
                raise EOFError
            byte = chunk[0]
            if byte == 27:  # Escape: discard ANSI sequences (arrow keys etc.)
                ready, _, _ = select.select([fd], [], [], 0.08)
                if ready:
                    next_char = os.read(fd, 1)
                    if next_char in (b'[', b'O'):
                        # Read a standard CSI/SS3 sequence to its final byte.
                        for _ in range(32):
                            ready, _, _ = select.select([fd], [], [], 0.08)
                            if not ready:
                                break
                            c = os.read(fd, 1)[0]
                            if 64 <= c <= 126:
                                break
                continue
            if byte in (10, 13):
                print()
                return clean_input(''.join(chars))
            if byte == 4 and not chars:
                print()
                raise EOFError
            if byte in (8, 127):
                if chars:
                    chars.pop()
                    sys.stdout.write('\b \b')
                    sys.stdout.flush()
                continue
            if byte < 32:
                continue
            ch = decoder.decode(chunk)
            if ch and ch.isprintable():
                chars.append(ch)
                sys.stdout.write(ch)
                sys.stdout.flush()
    finally:
        termios.tcsetattr(fd, termios.TCSANOW, old)


class CLI:
    def __init__(self):
        self.index, self.players = None, None
        self.mode = 'menu'
        self.settings = dict(DEFAULT_SETTINGS)
        if CONFIG_PATH.exists():
            try:
                saved = json.loads(CONFIG_PATH.read_text(encoding='utf-8'))
                if saved.get('method') in (1, 2):
                    self.settings['method'] = saved['method']
                if isinstance(saved.get('output_limit'), int) and 1 <= saved['output_limit'] <= 1000:
                    self.settings['output_limit'] = saved['output_limit']
                if isinstance(saved.get('colors'), bool):
                    self.settings['colors'] = saved['colors']
            except (ValueError, OSError, TypeError, AttributeError):
                pass

    def color(self, value, color):
        if not self.settings['colors'] or not sys.stdout.isatty() or os.getenv('NO_COLOR'):
            return str(value)
        codes = {'blue': '94', 'green': '92', 'yellow': '93',
                 'red': '91', 'cyan': '96', 'bold': '1', 'dim': '2'}
        return f'\033[{codes[color]}m{value}\033[0m'

    def save_settings(self):
        write_json(CONFIG_PATH, self.settings)

    def show_results(self, query, method=None):
        if self.players is None:
            print(self.color('Build an index first using /build.', 'yellow'))
            return
        method = method or self.settings['method']
        try:
            ranked, total = query_index(self.index, self.players, query, method,
                                        self.settings['output_limit'])
        except ValueError as error:
            print(self.color(f'Invalid search: {error}', 'red'))
            return
        print(self.color(f'Query: {query}  |  IDF #{method}  |  '
                         f'Matches: {total}  |  Showing: {len(ranked)}', 'cyan'))
        if not ranked:
            print(self.color('  No matching players.', 'yellow'))
            return
        for rank, (pid, score) in enumerate(ranked, 1):
            row = self.players[pid]
            name = row.get('name') or row.get('full_name') or 'Unknown player'
            team = row.get('last_associated_team') or 'unknown team'
            position = row.get('position_code') or '?'
            print(f'  {self.color(f"{rank:>3}.", "yellow")} '
                  f'{self.color(name, "green")} '
                  f'{self.color(f"#{pid}", "dim")}  '
                  f'[{position}]  {team}  '
                  f'{self.color(f"score={score:.3f}", "blue")}')
        print()

    def stats(self):
        if self.players is None:
            print('No index loaded; run /build')
            return
        postings = sum(len(p) for p in self.index.values())
        print(self.color('Index statistics', 'cyan'))
        print(f'  Players:       {len(self.players):,}')
        print(f'  Unique terms:  {len(self.index):,}')
        print(f'  Postings:      {postings:,}')
        print(f'  IDF method:    {self.settings["method"]}')
        print(f'  Output limit:  {self.settings["output_limit"]}')
        for file in (INDEX_PATH, PLAYERS_PATH):
            if file.exists():
                print(f'  {file}: {file.stat().st_size / 1048576:.2f} MiB')

    def help(self):
        print('''
Commands:
  /build [file.csv]             Build and save custom inverted index
  /search                       Enter search mode; plain text searches directly
  /search Bob Griffith          Run one search without entering search mode
  /change_method 1             Standard IDF: ln(N / DF)
  /change_method 2             Smoothed IDF: ln((N+1)/(DF+1)) + 1
  /configure output limit 50   Show up to 50 results (1..1000)
  /configure color off         Disable colors (on to re-enable)
  /compare New York            Compare both methods on one query
  /compare                     Compare 3 example queries
  /stats                       Show players, terms and postings counts
  /help                        Display this help
  /back                        Leave search mode and return to main menu
  /exit                        Quit

Inside search mode, type queries WITHOUT /search:
  Bob Griffith
  position=P
  position=P team="New York"
  Yankees bats=R
  birthplace="Liberty, TN"
  weight>=200 active=false
  birth_year=1912
  /compare position=P Yankees

Filters: position, team, teams, bats, throws, birthplace, name,
         nickname, id, active, born, birth_year, debut_year,
         last_year, weight.
Search terms use OR matching; multiple filters must all match (AND).
Arrow keys are ignored by the built-in terminal input handler.
''')

    def dispatch(self, line):
        line = line.strip()
        if not line:
            return True
        if not line.startswith('/'):
            if self.mode == 'search':
                self.show_results(line)
            else:
                print(self.color('Type /search to enter search mode, or /help.', 'yellow'))
            return True

        command, _, argument = line.partition(' ')
        command, argument = command.lower(), argument.strip()
        if command in ('/exit', '/quit'):
            print(self.color('Goodbye!', 'green'))
            return False
        if command == '/help':
            self.help()
        elif command in ('/back', '/menu'):
            self.mode = 'menu'
            print(self.color('Main menu mode.', 'cyan'))
        elif command == '/search':
            if argument:
                self.show_results(argument)
            else:
                self.mode = 'search'
                print(self.color('Search mode ON. Type any query, or /back to return.', 'green'))
        elif command == '/build':
            try:
                self.index, self.players, skipped = build_index(argument or CSV_PATH)
                print(self.color(f'Index built: {len(self.players):,} players, '
                                 f'{len(self.index):,} words. Skipped {skipped} rows.', 'green'))
                print('Saved indexes.json and players_data.json.')
            except (OSError, ValueError, csv.Error) as exc:
                print(self.color(f'Build failed: {exc}', 'red'))
        elif command == '/change_method':
            if argument not in ('1', '2'):
                print('Usage: /change_method 1  OR  /change_method 2')
            else:
                self.settings['method'] = int(argument)
                self.save_settings()
                label = 'Standard' if argument == '1' else 'Smoothed'
                print(self.color(f'Active ranking: {label} IDF (method {argument}).', 'green'))
        elif command == '/configure':
            match = re.fullmatch(r'output\s+limit\s+(\d+)', argument, re.I)
            colors = re.fullmatch(r'colou?r\s+(on|off)', argument, re.I)
            if match:
                limit = int(match.group(1))
                if not 1 <= limit <= 1000:
                    print(self.color('Output limit must be 1..1000.', 'red'))
                else:
                    self.settings['output_limit'] = limit
                    self.save_settings()
                    print(self.color(f'Output limit set to {limit}.', 'green'))
            elif colors:
                self.settings['colors'] = colors.group(1).lower() == 'on'
                self.save_settings()
                print('Colors: ' + ('ON' if self.settings['colors'] else 'OFF'))
            else:
                print('Usage: /configure output limit 50  OR  /configure color on|off')
        elif command == '/stats':
            self.stats()
        elif command == '/compare':
            queries = [argument] if argument else ['Bob Griffith', 'New York', 'Schoolboy']
            for query in queries:
                for method in (1, 2):
                    self.show_results(query, method=method)
        else:
            print(self.color('Unknown command. Type /help.', 'red'))
        return True

    def run(self):
        print(self.color('=== MLB PLAYER EXPLORER ===', 'bold'))
        print('Custom inverted index | TF-IDF | Type /help for commands')
        try:
            self.index, self.players = load_index()
            if self.players is not None:
                print(self.color(f'Loaded {len(self.players):,} players and '
                                 f'{len(self.index):,} unique terms.', 'green'))
            else:
                print(self.color('No index found. Run /build.', 'yellow'))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(self.color(f'Cannot load saved index: {exc}. Use /build.', 'red'))
        while True:
            prompt = self.color('mlb/search> ', 'cyan') if self.mode == 'search' else self.color('mlb> ', 'blue')
            try:
                line = get_line(prompt)
            except (EOFError, KeyboardInterrupt):
                print('\nGoodbye!')
                break
            try:
                if not self.dispatch(line):
                    break
            except (OSError, ValueError, csv.Error, json.JSONDecodeError) as exc:
                print(self.color(f'Error: {exc}', 'red'))


if __name__ == '__main__':
    CLI().run()
