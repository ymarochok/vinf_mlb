#!/usr/bin/env python3
"""Export 5 verified CSV profiles and on-disk URL metadata into wiki tables."""
import csv
import json
from pathlib import Path

def main():
    root = Path('stored_players')
    with open('players.csv',encoding='utf-8-sig',newline='') as f:
        rows=list(csv.DictReader(f))
    out=['===== Päť reálnych ukážok extrahovaných hráčov =====']
    selected = rows[:5]  # first five actual CSV records, not invented
    for i,row in enumerate(selected,1):
        pid=row.get('player_id','')
        meta=root/pid/'metadata.json'
        url='__________'
        if meta.exists():
            try:
                url=json.loads(meta.read_text(encoding='utf-8')).get('url') or url
            except (OSError,ValueError): pass
        out.extend([f'==== Ukážka {i}: {row.get("name", "")} ====',
                    f'**Zdrojová URL:** {url}',
                    '^ Atribút ^ Hodnota ^'])
        for key,val in row.items():
            val=(val or '').replace('|',' / ').replace('\n',' ')
            out.append(f'| {key} | {val} |')
    Path('five_examples_wiki.txt').write_text('\n'.join(out)+'\n',encoding='utf-8')
    print('Saved five_examples_wiki.txt with exactly the first five REAL rows.')

if __name__=='__main__':
    main()
