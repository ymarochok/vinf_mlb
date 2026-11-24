#!/usr/bin/env python3
"""MLB corpus statistics + optional real tiktoken counts.

Examples (run from the project root):
    python3 stats_and_tokens.py --tokens both
    python3 stats_and_tokens.py --tokens text
    python3 stats_and_tokens.py --tokens extracted
    python3 stats_and_tokens.py --tokens html  # very large corpus: expensive
    python3 stats_and_tokens.py --tokens none

"text" counts the saved VISIBLE TEXT in stored_players/*/page.txt.
"extracted" counts concatenated nonempty CSV values, excluding player_id.
"html" counts raw saved page.html, including markup and JavaScript.
The tokenization scheme is tiktoken/cl100k_base; each document is counted
independently, which avoids artificial joins across documents.
"""
import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path


def size_str(n):
    return f'{n / 1048576:.2f} MiB ({n:,} B)'


def get_tokenizer():
    try:
        import tiktoken
    except ImportError as exc:
        raise RuntimeError(
            'Missing tiktoken. Create a virtual environment and install it:\n'
            '  python3 -m venv .venv\n'
            '  source .venv/bin/activate\n'
            '  python -m pip install tiktoken'
        ) from exc
    try:
        return tiktoken.get_encoding('cl100k_base')
    except Exception as exc:
        raise RuntimeError(
            'Could not load cl100k_base. Its encoding data may need to be '
            'downloaded on first use. Check your connection and rerun. '
            f'Original error: {exc}'
        ) from exc


def count_file_tokens(files, tokenizer, label, progress=500):
    if not files:
        raise RuntimeError(f'No {label} files found; cannot count tokens.')
    total = 0
    for i, path in enumerate(files, 1):
        content = path.read_text(encoding='utf-8', errors='replace')
        # No chunking: token boundaries must remain correct inside a document.
        total += len(tokenizer.encode(content, disallowed_special=()))
        if progress and (i % progress == 0 or i == len(files)):
            print(f'[{label}] {i}/{len(files)} files; tokens so far: {total:,}', flush=True)
    return total


def count_csv_tokens(rows, tokenizer):
    if not rows:
        raise RuntimeError('players.csv contains no player rows.')
    total = 0
    for row in rows:
        text = ' '.join(str(v) for key, v in row.items() if key != 'player_id' and v)
        total += len(tokenizer.encode(text, disallowed_special=()))
    print(f'[CSV] {len(rows)} rows; total tokens: {total:,}', flush=True)
    return total


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('stored_players'))
    parser.add_argument('--csv', type=Path, default=Path('players.csv'))
    parser.add_argument('--index', type=Path, default=Path('indexes.json'))
    parser.add_argument('--tokens', choices=['none', 'text', 'extracted', 'both', 'html'], default='both',
                        help='both = visible page.txt + extracted CSV; html = raw source markup')
    parser.add_argument('--out', type=Path, default=Path('statistics_wiki.txt'))
    parser.add_argument('--progress-every', type=int, default=500)
    args = parser.parse_args()

    if not args.root.is_dir():
        parser.error(f'Missing data directory: {args.root.resolve()}')
    if not args.csv.is_file():
        parser.error(f'Missing CSV file: {args.csv.resolve()}')

    pages = sorted(args.root.rglob('page.html'))
    texts = sorted(args.root.rglob('page.txt'))
    metas = sorted(args.root.rglob('metadata.json'))
    all_files = [p for p in args.root.rglob('*') if p.is_file()]
    size_html = sum(p.stat().st_size for p in pages)
    size_txt = sum(p.stat().st_size for p in texts)
    total_size = sum(p.stat().st_size for p in all_files)

    with args.csv.open(newline='', encoding='utf-8-sig') as source:
        players = list(csv.DictReader(source))
    unique_ids = {p.get('player_id', '') for p in players if p.get('player_id')}
    complete_fields = {key: sum(bool((r.get(key) or '').strip()) for r in players)
                       for key in (players[0].keys() if players else ())}

    failed = Path('failed_pages.csv')
    failed_count = None
    if failed.is_file():
        with failed.open(newline='', encoding='utf-8-sig') as source:
            failed_count = sum(1 for _ in csv.DictReader(source))

    terms = postings = None
    if args.index.is_file():
        with args.index.open(encoding='utf-8') as source:
            inv = json.load(source)
        terms = len(inv)
        postings = sum(len(x) for x in inv.values())

    # The real link-following crawler uses crawl_state.json. It has no
    # `completed` flag; a previous stats script incorrectly checked the
    # independent all_time_only_state.json and printed False as a conclusion.
    state = args.root / 'crawl_state.json'
    state_summary = 'nedostupný'
    if state.is_file():
        try:
            record = json.loads(state.read_text(encoding='utf-8'))
            state_summary = (f"spracovaných stránok: {record.get('pages_processed', '—')}; "
                             f"URL vo fronte: {len(record.get('queue', []))}")
        except (OSError, ValueError, TypeError, AttributeError):
            state_summary = 'nečitateľný'

    counts = {}
    if args.tokens != 'none':
        enc = get_tokenizer()  # fail loudly; no misleading blank success
        if args.tokens in ('text', 'both'):
            counts['text'] = count_file_tokens(texts, enc, 'TXT', args.progress_every)
        if args.tokens in ('extracted', 'both'):
            counts['extracted'] = count_csv_tokens(players, enc)
        if args.tokens == 'html':
            counts['html'] = count_file_tokens(pages, enc, 'HTML', args.progress_every)

    def value(x):
        return x if x is not None else '__________'

    lines = [
        '===== Štatistiky dát – namerané =====',
        '^ Metrika ^ Hodnota ^',
        f'| HTML profily | {len(pages)} |',
        f'| Unikátne číselné adresáre | {len({p.parent.name for p in pages if p.parent.name.isdigit()})} |',
        f'| Súbory metadata.json | {len(metas)} |',
        f'| HTML veľkosť | {size_str(size_html)} |',
        f'| page.txt veľkosť | {size_str(size_txt)} |',
        f'| stored_players celkovo | {size_str(total_size)} |',
        f'| CSV záznamy | {len(players)} |',
        f'| CSV unikátne player_id | {len(unique_ids)} |',
        f'| CSV veľkosť | {size_str(args.csv.stat().st_size)} |',
        f'| Zlyhania / duplicity | {value(failed_count)} |',
        f'| Potenciálne relevantné (ID + meno) | {sum(bool(p.get("player_id") and p.get("name")) for p in players)} |',
        f'| Index termíny | {value(terms)} |',
        f'| Postings | {value(postings)} |',
        f'| indexes.json veľkosť | {size_str(args.index.stat().st_size) if args.index.is_file() else "__________"} |',
        f'| Tokeny page.txt (cl100k_base) | {value(counts.get("text"))} |',
        f'| Tokeny CSV polí (cl100k_base) | {value(counts.get("extracted"))} |',
        f'| Tokeny surového HTML (cl100k_base) | {value(counts.get("html"))} |',
        f'| Stav crawlera (crawl_state.json) | {state_summary} |',
        '',
        '**Poznámka:** Tokeny sa sčítavajú zvlášť za každý dokument. Text=viditeľný obsah page.txt; CSV=neprázdne polia bez ID; HTML=zdrojový kód vrátane skriptov. Tieto hodnoty sa nedajú navzájom zamieňať.',
        '**Relevantnosť:** ako automatický odhad používame záznamy s neprázdnym ID a menom, nie manuálnu kontrolu správnosti.',
        '',
        '===== Úplnosť polí =====',
        '^ Pole ^ Vyplnené ^ Prázdne ^',
    ]
    for key, complete in complete_fields.items():
        lines.append(f'| {key} | {complete} | {len(players)-complete} |')
    args.out.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    print(f'\nStatistics saved: {args.out.resolve()}')
    for key, count in counts.items():
        print(f'Tokens [{key}, cl100k_base]: {count:,}')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, UnicodeError, ValueError) as exc:
        print(f'ERROR: {exc}', file=sys.stderr)
        sys.exit(1)
