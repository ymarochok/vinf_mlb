# Konzultácia 3 — MLB + Wikipedia v Apache Spark

Tri kroky: znovu extrahovať uložené MLB HTML → exportovať všetky wiki stránky → vybrať biografie a spojiť hráčov s článkami.
Part 1 sa nemení. Každý krok sa spúšťa samostatne a ukladá Parquet, takže pri ukážke netreba opakovať celú extrakciu.

## Súbory na ukážku

| Súbor | Úloha |
| --- | --- |
| `extract_player.py` | Nezmenená kópia REGEX extraktora z Part 1, vrátane pôvodného CLI. |
| `players.py` | Spark načíta každý `page.html`; extraktor beží v `mapPartitions` na workeroch. |
| `extract_wiki.py` | REGEX funkcie pre dátum narodenia, meno, miesto narodenia, tímy, úspechy, kategórie a text. |
| `wiki.py` | Spark XML reader exportuje všetky `<page>` z `.xml.bz2` do Parquet bez filtrovania; zachová surový wikitext, namespace a redirect. |
| `join.py` | Na uložený wiki Parquet aplikuje REGEX bez predbežného filtrovania a vykoná JOIN podľa mena a dátumu narodenia; INNER JOIN uloží iba hráčov s wiki zhodou. |
| `showcase.py` | Načíta uložené tabuľky, vypíše schémy a príklady a otvorí Python konzolu so `spark`. |
| `common.py` | Spoločná Spark session, normalizácia mien a zápis Parquet. |
| `download_wiki.sh` | Obnoviteľné stiahnutie celého anglického wiki dumpu s kontrolou SHA-1. |

## Inštalácia a spustenie

Z koreňa projektu `vinf`. Potrebný je **Python 3.11, Java 17+**, `uv`, `curl` a `sha1sum`.

```bash
uv venv .venv-spark --python 3.11
uv pip install --python .venv-spark/bin/python -r part2/requirements.txt
source .venv-spark/bin/activate
export JAVA_HOME=/usr/lib/jvm/java-17-openjdk
```

Ak je Java inde, upraviť `JAVA_HOME`. Spark je pripnutý na **4.0.1**.
Predvolený beh používa `local[4]`, driver s 4 GB pamäte a 16 shuffle partitions.
Ide o paralelný beh Sparku na jednom počítači; neprezentujem ho ako meranie na viacerých strojoch.

```bash
# 1. Spracuje všetky pôvodné HTML, nie staré players.csv.
python part2/players.py

# 2. Približne 27 GB komprimovaných dát. Prerušené sťahovanie obnoví rovnaký príkaz.
bash part2/download_wiki.sh

# 3. Export VŠETKÝCH stránok do Parquet bez filtrovania. Vstup zostáva komprimovaný.
python part2/wiki.py

# 4. Výber baseballových biografií z Parquet, REGEX extrakcia, JOIN a štatistiky.
python part2/join.py

# 5. Rýchla ukážka už hotových tabuliek a interaktívne SQL.
python part2/showcase.py
```

Downloader najprv uloží očakávaný checksum, sťahuje do `.part`, overí SHA-1 a až potom premenuje na `.xml.bz2`.
`part2/input/source.txt` obsahuje URL, čas stiahnutia a checksum skutočného vstupu.
Pre konkrétny dostupný snapshot možno odovzdať jeho adresár ako argument downloadera.
Ak sa `latest` počas prerušeného sťahovania zmení, checksum ho odmietne; použiť dátumový snapshot alebo odstrániť iba neplatný `.part` a stiahnuť znova.
V `input/` má byť jeden celý snapshot, nie zároveň celý dump a jeho duplicitné shardy.

Iný vstup možno odovzdať ako prvý argument:

```bash
python part2/players.py /cesta/stored_players
python part2/wiki.py '/cesta/wiki/*.xml.bz2'
```

Pre existujúci Spark cluster možno použiť `spark-submit --master spark://HOST:7077 part2/players.py /shared/stored_players` a analogicky ďalšie fázy.
Python 3.11, vstupy a adresár projektu/výstupov musia byť dostupné workerom na rovnakých zdieľaných cestách.
Súbory s Python funkciami sa posielajú cez `addPyFile`; nenastavujem počet workerov ani konfiguráciu cudzieho klastra.

Export `wiki.py` zachová všetky stránky zo vstupného dumpu vrátane iných namespace a redirectov.
`join.py` načíta tento hotový Parquet a extrahuje wiki atribúty cez REGEX priamo v pamäti Sparku; neukladá medzitabuľku `wiki_players`. Pre aktuálny vývoj používa fixných 7 272 stránok cez `--wiki-dir wiki_fast/output`.
Úplný export potrebuje dodatočné miesto na disku a dlhší zápis než pôvodný filtrovaný export; veľkosť závisí od obsahu a kompresie.

## Prečo JOIN a podľa čoho

MLB dáva identitu hráča a základné vlastnosti. Wikipedia dopĺňa biografický kontext, kariérne tímy, úspechy a kategórie, ktoré v MLB CSV chýbajú alebo sú obmedzené.
Ide o rovnakú entitu z dvoch rôznych zdrojov; nevytváram kartézsky súčin všetkých profilov a článkov.

1. Pre MLB použijem `name` aj `full_name`; pre wiki titulok bez zátvorkového rozlíšenia a infoboxové `name`.
2. Zjednotím veľkosť písmen, diakritiku, interpunkciu a medzery. Originálne mená zachovám.
3. Vyžadujem rovnaké normalizované meno **aj rovnaký dátum narodenia**.
4. Viac článkov pre jedného hráča alebo viac hráčov pre jeden článok → záznamy v `ambiguous`, bez automatického výberu.
5. Jednoznačné dvojice pripojím cez INNER JOIN k MLB. Výstup obsahuje iba hráčov s wiki zhodou; počet ostatných sa uloží v štatistikách.

Chýbajúci dátum, odlišné meno a nepodporovaný formát wiki dátumu znižujú pokrytie. Tieto prípady sa nezapisujú do finálnej tabuľky; nehádajú sa fuzzy zhody.
Redirecty sa nepočítajú ako ďalšie články a nepoužívajú sa ako aliasy. REGEX extraktor rozpoznáva polia zo šablón `Infobox baseball biography` / `Infobox MLB biography`. Vstupné wiki riadky pred extrakciou nefiltrujem.
Stránky mimo týchto šablón sa nespracujú ako hráčske biografie.
REGEX čistenie podporuje bežné odkazy, referencie a vnorené šablóny; nie je to úplný MediaWiki renderer.
Hodnoty z Wikidata, ktoré nie sú priamo vo wikitexte, nezískavam.

## PyLucene: index nad všetkými hráčmi

Nový vyhľadávač je v `lucene/`. Index obsahuje 20 161 hráčov z pôvodnej tabuľky
`output/players/`, nie iba wiki zhody. Spark exportuje JSON Lines a samostatný
PyLucene proces vytvorí index. Vyhľadávanie používa BM25 s váhami textových polí.

Z koreňa projektu vo Fish:

```fish
source build-pylucene/env.fish
python part2/lucene/search.py
```

Podrobný postup, syntax dotazov, zmena boostov, porovnanie s Part 1 a overenie
sú v [lucene/README.md](lucene/README.md).
DISK_ONLY storage. Match rules and final columns are unchanged.
