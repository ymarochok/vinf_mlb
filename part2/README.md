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

## Čo ukázať na konzultácii

Vo `showcase.py` sú dostupné SQL views s rovnakými menami ako nižšie.

```python
# Počet hráčov, počet pripojených unikátnych wiki stránok a pokrytie.
spark.sql("SELECT * FROM join_stats").show(truncate=False)

# Počet všetkých stránok exportovaných zo vstupného XML dumpu.
spark.sql("SELECT * FROM wiki_stats").show(truncate=False)
spark.sql("SELECT wiki_page_id, wiki_title, ns FROM wiki LIMIT 5").show()

# Päť príkladov spojenia: meno, MLB dátum narodenia a pravidlo zhody.
spark.sql("""SELECT player_id, name, birth_date, wiki_page_id,
             wiki_title, match_method
             FROM players_enriched WHERE wiki_page_id IS NOT NULL
             ORDER BY name LIMIT 5""").show(truncate=False)

# Nové atribúty, ktoré môžu obohatiť budúce vyhľadávanie.
spark.sql("""SELECT name, wiki_teams, wiki_highlights, wiki_categories
             FROM players_enriched WHERE wiki_page_id IS NOT NULL
             ORDER BY name LIMIT 5""").show(truncate=100)

# Počet neprázdnych wiki atribútov medzi spojenými hráčmi.
spark.sql("""SELECT count(wiki_teams) AS teams, count(wiki_highlights) AS highlights,
             count(wiki_categories) AS categories FROM players_enriched
             WHERE wiki_page_id IS NOT NULL""").show()

spark.sql("SELECT * FROM player_errors").show(truncate=False)
spark.sql("SELECT * FROM ambiguous LIMIT 10").show(truncate=False)
```

Spark UI je na adrese vypísanej v konzole (zvyčajne `http://localhost:4040`) počas behu procesu.
Tam možno ukázať jobs, stages, úlohy a SQL plán. `exit()` ukončí interaktívnu ukážku.

## Výstupy

| `part2/output/` | Obsah |
| --- | --- |
| `players/` | 17 pôvodných atribútov + cesta k HTML a MLB URL. Pôvodné hodnoty sú stringy, chýbajúce hodnoty sú null. |
| `player_errors/` | Cesty a vysvetlenia neúspešnej extrakcie. |
| `wiki/` | Všetky stránky dumpu: ID, titulok, namespace, cieľ redirectu, ID revízie a surový wikitext. Bez filtrovania. |
| `players_enriched/` | Iba MLB hráči s jednoznačnou wiki zhodou, wiki atribúty vrátane `wiki_description` + pravidlo spojenia. Wiki dátum narodenia sa používa iba na zhodu; vo výsledku ostáva len MLB `birth_date`. |
| `ambiguous/` | Odmietnuté nejednoznačné dvojice. Počet nepripojených hráčov je v `join_stats`. |
| `wiki_stats/`, `join_stats/` | Malé JSON výstupy s reálne vypočítanými počtami. |

`unique_wiki_pages` sa počíta cez **countDistinct(wiki_page_id)**, samostatne od počtu pripojených hráčov.
Výstupy sú Spark adresáre s part súbormi; opakovaný beh danej fázy prepíše jej výstupy.
Načítané MLB HTML sa v prvej fáze dočasne držia len ako extrahované riadky cez `DISK_ONLY`, nie ako celý 16 GB obsah v pamäti.

## Stav overenia v tejto relácii

Implementácia je pripravená na spustenie, ale **celý Spark pipeline ani wiki JOIN tu ešte neboli spustené**.
Relácia blokuje sieťové sťahovanie aj lokálne sockety potrebné pre Spark JVM (`Operation not permitted`).
Preto tu nie je stiahnutý dump ani vymyslený počet pripojených stránok. Skutočné čísla vytvorí `join.py` po vyššie uvedených krokoch.

Lokálne kontroly prešli: Python syntax, Bash syntax, nezmenená kópia extraktora, pôvodné testy na 20 náhodných MLB profiloch a známom chybnom profile; navyše 6 testov wiki extraktora, dátumov, normalizácie a wrappera na ďalších 20 reálnych profiloch.
Dočasné testy sú mimo `part2`, v `/tmp/vinf-check/`.
Na disku je 20 162 HTML súborov; pôvodný CSV obsahuje 20 161 unikátnych hráčov. Očakávaná chyba je profil `645261`. Sú to overené počty Part 1, nie výsledok nového Spark behu.

## Alternatívny wiki vstup, spoločný JOIN a ukážka

`join.py` aj `showcase.py` sú spoločné pre oba scenáre. Zmení sa iba adresár:

```bash
# Z adresára part2: otvorenie už pripraveného vývojového podvýberu.
python showcase.py --wiki-dir wiki_fast/output
# Rovnaká logika spojenia nad týmto podvýberom.
python join.py --wiki-dir wiki_fast/output
```

Bez `--wiki-dir` sa používa pôvodný úplný export v `part2/output/`.
Neukončené exporty sa kontrolujú cez `_SUCCESS` ešte pred spustením Sparku.
Podrobnosti filtrovania celého dumpu aj vývojových limitov sú v `wiki_fast/README.md`.

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

## Zdroje

- Oficiálny dump: https://dumps.wikimedia.org/enwiki/latest/
- Spark 4.0.1 XML reader: https://spark.apache.org/docs/4.0.1/sql-data-sources-xml.html
- Wikipedia text: CC BY-SA; výstup zachováva názov, odkaz na článok a ID revízie.

## Full-dump memory configuration

The full wiki export can exhaust the default vectorized Parquet reader on large
text batches. The shared Spark session disables that reader and defaults to two
local tasks. `join.py` scans REGEX identity fields first, broadcasts the small MLB
key table, rejects ambiguous matches, and extracts full biographies only for
accepted wiki IDs in a second read. It never caches all wiki article bodies.
Only candidates and the small joined result are persisted; joined descriptions
use DISK_ONLY storage. Match rules and final columns are unchanged.
