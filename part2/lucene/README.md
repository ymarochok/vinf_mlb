# Spark + PyLucene player search

The prepared index contains **20,161 regular MLB players** from `part2/output/players/`.
It does not use the small wiki-enriched table. One player is one Lucene document.

## Run now (Fish)

From the project root:

```fish
cd /home/leonard/study/vinf
source build-pylucene/env.fish
python part2/lucene/search.py
```

Type queries directly in the console:

```text
Hank Aaron
position=P
position=P team="New York"
Yankees bats=R
birthplace="Liberty, TN"
weight>=200 active=false
birth_year=1912
birth_year>=1990 debut_year<2015
born=1934-02-05
id=110001
/configure output limit 5
/stats
/boosts
/compare Hank Aaron
/help
/exit
```

Or run one query (quote the whole query for the shell):

```fish
python part2/lucene/search.py 'position=P team="New York"' --limit 5
```

`/compare QUERY` prints the new BM25 results alongside both original TF-IDF rankings.
It reads Part 1's existing `indexes.json` and `players_data.json` without changing them.
`/compare` without an argument compares the three original example queries.
`/search QUERY` is also accepted; `/search` alone leaves the prompt ready for queries.
There is one search prompt rather than separate menu/search modes. Rebuilds use the
separate scripts below. BM25 replaces `/change_method`; original TF-IDF methods are
available through `/compare`. Output is plain text and the limit is per session.

## Boosting attributes

| Text field | Default boost |
| --- | ---: |
| `name` | 5 |
| `full_name` | 3 |
| `nickname` | 2 |
| `last_associated_team` | 1.5 |
| `teams_from_transactions`, `birthplace`, `position_code`, `bats`, `throws` | 1 |

Each free-text term is searched across these fields with a Lucene `BoostQuery`.
The matching field's BM25 contribution is multiplied by its boost; contributions
are added. A 5x name boost does not guarantee a particular player always ranks first:
term rarity, field length and matches in other fields still affect the final score.
Structured filters narrow the results without adding a score.

Change a boost for one run, or edit `BOOSTS` in `schema.py`. **No rebuild is needed**:

```fish
python part2/lucene/search.py 'Hank Aaron' --boost name=10
python part2/lucene/search.py 'Hank Aaron' --no-boosts
python part2/lucene/search.py 'Hank Aaron' --explain --limit 1
```

`--explain` displays the top result's actual Lucene calculation, including boosts,
IDF and term-frequency/length contributions. Boosts must be finite positive numbers.
BM25 is explicitly configured with `k1=1.2`, `b=0.75` in the writer and searcher.
These initial boosts are chosen weights, not an experimentally tuned optimum.

## Query compatibility with Part 1

- Free-text words use **OR**. All structured filters must match (**AND**).
- The original parser is wrapped to add the `wiki_description` filter. Original
  filters, `words()` and the side normalizer are reused; Part 1 itself is unchanged.
- Text is casefolded and tokenized with the original REGEX, then indexed with
  `WhitespaceAnalyzer`. There is no stemming, stopword removal or accent folding.
- Quotes group values containing spaces. `wiki_description="..."` is an ordered
  phrase filter. Other quoted filters retain substring matching; quoted free text
  is still OR between words rather than a phrase.
  `AND` and `OR` typed as free words are ordinary words, just as in Part 1.
- Exact filters: `position`, `bats`, `throws`, `active`, `id`, `born`.
  Original position aliases, R/L/S aliases and yes/no/1/0 aliases are preserved.
- Substring filters: `team`, `teams`, `birthplace`, `name`, `nickname`.
  Lowercased whole-value fields plus escaped `WildcardQuery` implement literal
  contains-matching. User `*`, `?` and backslashes are literal characters.
  Leading-wildcard queries can be slower on large indexes; this dataset is small.
- Numeric filters: `weight`, `birth_year`, `debut_year`, `last_year`, supporting
  `=`, `>`, `<`, `>=`, `<=`. Years are derived from the first four date characters.
  Numeric values use `IntPoint`; missing values are omitted rather than set to zero.
- Result order is score descending, then casefolded player name and ID ascending.
  Counts are exact using `IndexSearcher.count()`, not approximate top-hit counts.
- Ranking differs deliberately: BM25 per field + boosts replaces aggregated TF-IDF.
  Full-name terms are indexed in their own field, including terms also in `name`.
  A word matching both names can therefore contribute through both fields.

## Files and rebuild

| File | Role |
| --- | --- |
| `schema.py` | Shared paths, field boosts, filter normalization and original parser imports |
| `export_index_data.py` | Spark reads Parquet, validates IDs and writes JSON Lines |
| `build_index.py` | One PyLucene writer builds the on-disk index |
| `search.py` | Lucene queries, interactive CLI and original TF-IDF comparison |

Rebuild after changing player data:

```fish
cd /home/leonard/study/vinf
.venv-spark/bin/python part2/lucene/export_index_data.py
source build-pylucene/env.fish
python part2/lucene/build_index.py
python part2/lucene/search.py
```

Spark and PyLucene run in separate Python processes/environments. Spark performs
the data export; PyLucene indexes its small JSON dataset in one process.
This avoids requiring Spark and PyLucene in the same environment or sharing one
index directory between Spark workers. Search uses the saved index and needs no
running Spark session.

`data/players/` is Spark's JSON directory (`part-*.json`, one object per line).
`data/source.json` records the source and export count. `index/` contains Lucene's
persistent index. Re-export and rebuild overwrite their generated outputs.
The builder validates all IDs before replacing the index and rolls back writer
changes if indexing fails. Original values are stored as JSON in each document
for result display; analyzed text, filter fields and sort fields are additional
Lucene representations of those values.

## Verification completed

- Spark exported 20,161 records; all 17 original fields for every player matched
  the Part 1 CSV, treating absent Parquet values as empty CSV values.
- Reopened Lucene index contains all 20,161 players.
- 27 real-data queries returned exactly the same player sets as Part 1.
- Fixture checks cover literal wildcard characters, numeric boundaries, missing
  values, aliases, invalid filters, persistence and metadata preservation.
- Changing only the query-time boosts reversed the intended fixture ranking;
  the index was not rebuilt between those searches.
- Tests are outside `part2`, under `/tmp/vinf-lucene-checks/`.

## Official references

- [BoostQuery](https://lucene.apache.org/core/10_0_0/core/org/apache/lucene/search/BoostQuery.html)
- [BM25Similarity](https://lucene.apache.org/core/10_0_0/core/org/apache/lucene/search/similarities/BM25Similarity.html)
- [StringField](https://lucene.apache.org/core/10_0_0/core/org/apache/lucene/document/StringField.html)
- [WildcardQuery](https://lucene.apache.org/core/10_0_0/core/org/apache/lucene/search/WildcardQuery.html)

## Switch to the full wiki-enriched player table

After `part2/join.py` has finished successfully, run from the project root:

```fish
.venv-spark/bin/python part2/lucene/export_index_data.py --input part2/output/players_enriched
source build-pylucene/env.fish
python part2/lucene/build_index.py
python part2/lucene/search.py
```

This replaces the JSON export and Lucene index with matched/enriched players.
`wiki_description` is searchable with default boost 0.5; names retain their higher
weights. Original metadata and wiki descriptions are stored in each index document.
`/compare` still searches the full original Part 1 corpus, so differing result sets
are expected after switching to an enriched-only corpus. Use the same corpus for
controlled ranking comparisons.

## Inspect one player and their full biography

In the search console, use the player ID printed in the result list:

```text
Hank Aaron
/player 110001
```

Or from the project root in Fish:

```fish
source build-pylucene/env.fish
python part2/lucene/search.py --player 110001
```

This reads the complete stored record from Lucene, including all original and
wiki-derived fields. `wiki_description` is displayed in full, wrapped to the
terminal width of 100 characters without truncation. A missing biography is
explicitly reported. Restart an already-open CLI to load the new command/index.
To confirm a biography word contributes to search, search for that word with
`--explain` and inspect the `wiki_description:WORD` contribution.

## Search within biographies

Restart the search CLI after this update. No reindexing is needed for an existing
index built with the `wiki_description` TextField.

```text
wiki_description="miss the entire 2023 season"
wiki_description="miss the entire 2023 season" position=P
Aaron wiki_description="miss the entire 2023 season"
```

From the project root in Fish, quote the entire query for the shell:

```fish
python part2/lucene/search.py 'wiki_description="miss the entire 2023 season"'
```

This uses native Lucene `PhraseQuery` over stored token positions, with the same
casefolding/REGEX tokenization as the index. Words must be adjacent and in order;
punctuation is ignored by tokenization. It is a phrase-in-text condition, not
equality against the whole article and not a raw character substring. Only `=`
is supported. Multiple biography filters combine with AND. Missing descriptions
do not match. The filter narrows results without adding a score, like other
filters; add free words to obtain boosted BM25 ranking.

Verified against the current enriched index: the example returns Aaron Ashby,
Jake Odorizzi and Shane Baz. `/compare` reports that Part 1 has no biography
field instead of attempting an unsupported legacy query.

## Comparative analysis for consultation 4

[Porovnanie dopytov: pôvodný index a PyLucene](comparative_analysis.md) contains
measured results for seven queries: both original TF-IDF variants versus BM25,
match counts, top-three rankings, warm-query timings and interpretation.
It explicitly accounts for the different corpus sizes and added wiki content.
Regenerate with `python part2/lucene/compare_indexes.py` from the project root
in the PyLucene environment.
