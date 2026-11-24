#!/usr/bin/env python3
"""Export real top-5 ranking results for three queries in DokuWiki syntax."""
import argparse
from pathlib import Path
from indexer import load_index, query_index

def main():
    p = argparse.ArgumentParser()
    p.add_argument('queries', nargs='*', default=['Bob Griffith','New York','Schoolboy'])
    p.add_argument('--out', type=Path, default=Path('comparison_wiki.txt'))
    args = p.parse_args()
    index, players = load_index()
    if not index or not players:
        raise SystemExit('No index available. Run: python3 indexer.py -> /build')
    result = ['===== Porovnanie dvoch metód IDF =====',
              '^ Dopyt ^ Metóda ^ Pozícia ^ Meno ^ ID ^ Skóre ^']
    for query in args.queries:
        for method in (1, 2):
            ranked, total = query_index(index, players, query, method, 5)
            if not ranked:
                result.append(f'| {query} | IDF {method} | - | Žiadne výsledky | - | - |')
            for rank, (pid, score) in enumerate(ranked, 1):
                name = players[pid].get('name','').replace('|','/')
                result.append(f'| {query.replace("|","/")} | IDF {method} | {rank} | {name} | {pid} | {score:.5f} |')
            result.append(f'//Počet nájdených dokumentov pre {query} (metóda {method}): {total}.//')
    args.out.write_text('\n'.join(result)+'\n', encoding='utf-8')
    print(f'Saved {args.out}')

if __name__ == '__main__':
    main()
