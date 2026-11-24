# Porovnanie dopytov: pôvodný index a PyLucene

## Rozsah porovnania

Pôvodný index: **20,161 hráčov**, MLB atribúty, vlastný TF-IDF v dvoch variantoch. 
Nový index: **17,449 hráčov**, MLB atribúty a wiki_description, BM25 a váhy polí.

**Korpusy sa líšia:** nový index obsahuje iba hráčov úspešne spojených s Wikipédiou. 
Rozdiely počtov preto môžu spôsobovať vyradení hráči aj nové biografické termy, nielen vyhľadávací algoritmus.

## Namerané výsledky

Top 3 sú zoradené výsledky; čísla v zátvorkách sú player_id. Časy sú mediány troch behov po zahriatí, v milisekundách.

| Dopyt | Index | Počet zhôd | Top 3 | Čas (ms) |
| --- | --- | ---: | --- | ---: |
| `Hank Aaron` | TF-IDF 1 | 122 | Hank Aaron (110001); Hank Greenberg (115096); Aaron Altherr (571437) | 0.087 |
| `Hank Aaron` | TF-IDF 2 | 122 | Hank Aaron (110001); Hank Greenberg (115096); Aaron Altherr (571437) | 0.076 |
| `Hank Aaron` | BM25 + boosty | 746 | Hank Aaron (110001); Hank Shanks (817300); Hank Blalock (408213) | 6.633 |
| `Bob Griffith` | TF-IDF 1 | 371 | Bob Griffith (819265); Bob Caruthers (112072); Bob Ewing (113955) | 0.222 |
| `Bob Griffith` | TF-IDF 2 | 371 | Bob Griffith (819265); Bob Caruthers (112072); Bob Ewing (113955) | 0.205 |
| `Bob Griffith` | BM25 + boosty | 1499 | Bob Griffith (819265); Clark Griffith (115150); Derrell Griffith (115152) | 4.417 |
| `position=P team="New York"` | TF-IDF 1 | 277 | Aaron Sele (121986); Al Holland (116070); Al Leiter (117652) | 12.124 |
| `position=P team="New York"` | TF-IDF 2 | 277 | Aaron Sele (121986); Al Holland (116070); Al Leiter (117652) | 11.319 |
| `position=P team="New York"` | BM25 + boosty | 253 | Aaron Sele (121986); Al Holland (116070); Al Leiter (117652) | 5.570 |
| `weight>=200 active=false` | TF-IDF 1 | 4427 | A.J. Burnett (150359); A.J. Ellis (454560); A.J. Hinch (134460) | 17.862 |
| `weight>=200 active=false` | TF-IDF 2 | 4427 | A.J. Burnett (150359); A.J. Ellis (454560); A.J. Hinch (134460) | 17.823 |
| `weight>=200 active=false` | BM25 + boosty | 4176 | A.J. Burnett (150359); A.J. Ellis (454560); A.J. Hinch (134460) | 2.800 |
| `birth_year=1912` | TF-IDF 1 | 159 | Ace Parker (120223); Al McLean (118805); Al Unser (123569) | 13.401 |
| `birth_year=1912` | TF-IDF 2 | 159 | Ace Parker (120223); Al McLean (118805); Al Unser (123569) | 12.440 |
| `birth_year=1912` | BM25 + boosty | 135 | Al McLean (118805); Al Unser (123569); Al Wright (124590) | 0.613 |
| `professional` | TF-IDF 1 | 0 | — | 0.010 |
| `professional` | TF-IDF 2 | 0 | — | 0.008 |
| `professional` | BM25 + boosty | 11942 | Matt Skrmetta (282882); Ad Swigler (123056); Al Bauer (110699) | 1.784 |
| `wiki_description="miss the entire 2023 season"` | TF-IDF 1 / 2 | nepodporované | Wiki pole neexistuje | — |
| `wiki_description="miss the entire 2023 season"` | BM25 + boosty | 3 | Aaron Ashby (676879); Jake Odorizzi (543606); Shane Baz (669358) | 3.536 |

## Kontrola vplyvu rozdielnych korpusov

Nasledujúce počty obmedzujú staré výsledky na ID dostupné v novom indexe. Ide o kontrolu množín výsledkov, nie o prepočítanie TF-IDF na rovnakom korpuse.

- `Hank Aaron`: staré zhody prítomné v novom korpuse **109**; zhody spoločné obom **109**; nové zhody navyše **637**; pôvodné dostupné zhody nenájdené novým indexom **0**.
- `Bob Griffith`: staré zhody prítomné v novom korpuse **341**; zhody spoločné obom **341**; nové zhody navyše **1158**; pôvodné dostupné zhody nenájdené novým indexom **0**.
- `position=P team="New York"`: staré zhody prítomné v novom korpuse **253**; zhody spoločné obom **253**; nové zhody navyše **0**; pôvodné dostupné zhody nenájdené novým indexom **0**.
- `weight>=200 active=false`: staré zhody prítomné v novom korpuse **4176**; zhody spoločné obom **4176**; nové zhody navyše **0**; pôvodné dostupné zhody nenájdené novým indexom **0**.
- `birth_year=1912`: staré zhody prítomné v novom korpuse **135**; zhody spoločné obom **135**; nové zhody navyše **0**; pôvodné dostupné zhody nenájdené novým indexom **0**.
- `professional`: staré zhody prítomné v novom korpuse **0**; zhody spoločné obom **0**; nové zhody navyše **11942**; pôvodné dostupné zhody nenájdené novým indexom **0**.

## Interpretácia

- Mená: nový index zvýhodňuje name (5), full_name (3) a nickname (2). BM25 zároveň zohľadňuje frekvenciu termu a dĺžku konkrétneho poľa; zmena poradia je očakávaná.
- Štruktúrované filtre: logika ostáva rovnaká. Rozdiel celkových počtov treba posudzovať spolu s kontrolou ID vyššie. Pri filtroch bez voľného textu je Lucene skóre 0 a poradie určuje meno a ID.
- Biografie: voľné slovo professional môže nájsť hráčov podľa wiki_description, hoci tento term nebol v pôvodných MLB atribútoch. To je rozšírenie obsahu vyhľadávania.
- Wiki fráza používa PhraseQuery: slová musia nasledovať bezprostredne za sebou v danom poradí, po normalizácii tokenizérom. Pôvodný index takýto dopyt nepodporuje.
- Skóre TF-IDF a BM25 nie sú na rovnakej škále. Porovnávam zhody a poradie, nie absolútne skóre.
- Meranie porovnáva aktuálne implementácie: starý backend prehľadáva metadáta a triedi kandidátov v Pythone, nový vykonáva Lucene query, presný count a načítava uložené záznamy. Malý počet zahriatych behov nie je dôkaz univerzálnej rýchlostnej prevahy ani meranie kvality relevancie.
- Bez ručne označených relevantných výsledkov netvrdím zlepšenie precision/recall. Pre izolované porovnanie rankingu treba indexovať rovnaký korpus a rovnaké polia.

## Ako zopakovať

Z koreňa projektu vo Fish:

```fish
source build-pylucene/env.fish
python part2/lucene/compare_indexes.py
```

Interaktívne porovnanie: `/compare Hank Aaron`. Súbor vygenerovaný týmto skriptom sa pri opakovaní prepíše aktuálnymi výsledkami.
