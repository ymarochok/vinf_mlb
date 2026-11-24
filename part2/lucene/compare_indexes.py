"""Write measured old/new query comparisons for consultation 4."""
import json
from pathlib import Path
from statistics import median
from time import perf_counter

from schema import PART1, ROOT, BOOSTS
from search import PlayerSearch
from indexer import query_index

QUERIES = ['Hank Aaron', 'Bob Griffith', 'position=P team="New York"',
           'weight>=200 active=false', 'birth_year=1912',
           'professional', 'wiki_description="miss the entire 2023 season"']


def timed(call):
    result = call()  # Warm-up, outside measurement.
    times = []
    for _ in range(3):
        begin = perf_counter()
        result = call()
        times.append(1000 * (perf_counter() - begin))
    return result, median(times)


def main():
    index = json.loads((PART1 / 'indexes.json').read_text())
    players = json.loads((PART1 / 'players_data.json').read_text())
    engine = PlayerSearch()
    try:
        stored = engine.searcher.storedFields()
        ids = {stored.document(i).get('player_id') for i in range(engine.reader.maxDoc())}
        lines = ['# Porovnanie dopytov: pôvodný index a PyLucene', '',
                 '## Rozsah porovnania', '',
                 f'Pôvodný index: **{len(players):,} hráčov**, MLB atribúty, vlastný TF-IDF v dvoch variantoch. ',
                 f'Nový index: **{engine.reader.numDocs():,} hráčov**, MLB atribúty a wiki_description, BM25 a váhy polí.', '',
                 '**Korpusy sa líšia:** nový index obsahuje iba hráčov úspešne spojených s Wikipédiou. ',
                 'Rozdiely počtov preto môžu spôsobovať vyradení hráči aj nové biografické termy, nielen vyhľadávací algoritmus.', '',
                 '## Namerané výsledky', '',
                 'Top 3 sú zoradené výsledky; čísla v zátvorkách sú player_id. Časy sú mediány troch behov po zahriatí, v milisekundách.', '',
                 '| Dopyt | Index | Počet zhôd | Top 3 | Čas (ms) |',
                 '| --- | --- | ---: | --- | ---: |']
        notes = []
        for query in QUERIES:
            if not query.startswith('wiki_description='):
                for method in (1, 2):
                    (rows, total), ms = timed(lambda: query_index(index, players, query, method, 3))
                    top = '; '.join(f"{players[pid]['name']} ({pid})" for pid, _ in rows) or '—'
                    lines.append(f'| `{query}` | TF-IDF {method} | {total} | {top} | {ms:.3f} |')
                old_rows, _ = query_index(index, players, query, 2, len(players))
                retained = {pid for pid, _ in old_rows} & ids
                new_total, new_all = engine.search(query, max(1, engine.reader.numDocs()))
                new_ids = {row['player_id'] for row, _, _ in new_all}
                notes.append(f'- `{query}`: staré zhody prítomné v novom korpuse **{len(retained)}**; '
                             f'zhody spoločné obom **{len(retained & new_ids)}**; '
                             f'nové zhody navyše **{len(new_ids - retained)}**; '
                             f'pôvodné dostupné zhody nenájdené novým indexom **{len(retained - new_ids)}**.')
            else:
                lines.append(f'| `{query}` | TF-IDF 1 / 2 | nepodporované | Wiki pole neexistuje | — |')
            (total, rows), ms = timed(lambda: engine.search(query, 3))
            top = '; '.join(f"{row['name']} ({row['player_id']})" for row, _, _ in rows) or '—'
            lines.append(f'| `{query}` | BM25 + boosty | {total} | {top} | {ms:.3f} |')
        lines += ['', '## Kontrola vplyvu rozdielnych korpusov', '',
                  'Nasledujúce počty obmedzujú staré výsledky na ID dostupné v novom indexe. '
                  'Ide o kontrolu množín výsledkov, nie o prepočítanie TF-IDF na rovnakom korpuse.', '', *notes, '',
                  '## Interpretácia', '',
                  '- Mená: nový index zvýhodňuje name (5), full_name (3) a nickname (2). '
                  'BM25 zároveň zohľadňuje frekvenciu termu a dĺžku konkrétneho poľa; zmena poradia je očakávaná.',
                  '- Štruktúrované filtre: logika ostáva rovnaká. Rozdiel celkových počtov treba posudzovať spolu s kontrolou ID vyššie. '
                  'Pri filtroch bez voľného textu je Lucene skóre 0 a poradie určuje meno a ID.',
                  '- Biografie: voľné slovo professional môže nájsť hráčov podľa wiki_description, '
                  'hoci tento term nebol v pôvodných MLB atribútoch. To je rozšírenie obsahu vyhľadávania.',
                  '- Wiki fráza používa PhraseQuery: slová musia nasledovať bezprostredne za sebou v danom poradí, '
                  'po normalizácii tokenizérom. Pôvodný index takýto dopyt nepodporuje.',
                  '- Skóre TF-IDF a BM25 nie sú na rovnakej škále. Porovnávam zhody a poradie, nie absolútne skóre.',
                  '- Meranie porovnáva aktuálne implementácie: starý backend prehľadáva metadáta a triedi kandidátov v Pythone, '
                  'nový vykonáva Lucene query, presný count a načítava uložené záznamy. '
                  'Malý počet zahriatych behov nie je dôkaz univerzálnej rýchlostnej prevahy ani meranie kvality relevancie.',
                  '- Bez ručne označených relevantných výsledkov netvrdím zlepšenie precision/recall. '
                  'Pre izolované porovnanie rankingu treba indexovať rovnaký korpus a rovnaké polia.', '',
                  '## Ako zopakovať', '', 'Z koreňa projektu vo Fish:', '', '```fish',
                  'source build-pylucene/env.fish', 'python part2/lucene/compare_indexes.py', '```', '',
                  'Interaktívne porovnanie: `/compare Hank Aaron`. Súbor vygenerovaný týmto skriptom '
                  'sa pri opakovaní prepíše aktuálnymi výsledkami.', '']
        output = ROOT / 'comparative_analysis.md'
        output.write_text('\n'.join(lines))
        print(output)
    finally:
        engine.close()


if __name__ == '__main__':
    main()
