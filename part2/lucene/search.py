"""BM25 search, field boosts and the Part 1 query/filter syntax."""
import argparse
import json
import math
import re
import textwrap
from pathlib import Path

from schema import BOOSTS, FILTER_FIELDS, INDEX, NUMERIC_FILTERS, PART1, filter_value, parse_query, start_lucene, words
from indexer import get_line

start_lucene()
from java.nio.file import Paths
from org.apache.lucene.document import IntPoint
from org.apache.lucene.index import DirectoryReader, Term
from org.apache.lucene.search import BooleanClause, BooleanQuery, BoostQuery, IndexSearcher, MatchNoDocsQuery, PhraseQuery, Sort, SortField, TermQuery, WildcardQuery
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.store import FSDirectory


def make_query(text, boosts=BOOSTS):
    text, filters = parse_query(text)
    tokens = sorted(set(words(text)))
    if not tokens and not filters:
        return MatchNoDocsQuery()
    query = BooleanQuery.Builder()
    if tokens:
        free = BooleanQuery.Builder()
        for token in tokens:
            for field, boost in boosts.items():
                free.add(BoostQuery(TermQuery(Term(field, token)), float(boost)), BooleanClause.Occur.SHOULD)
        query.add(free.build(), BooleanClause.Occur.MUST)
    for key, op, value in filters:
        field = "filter_" + key
        if key in NUMERIC_FILTERS:
            try:
                number = int(value)
            except ValueError:
                return MatchNoDocsQuery()
            low = number + (op == ">") if op in ("=", ">", ">=") else -(2**31)
            high = number - (op == "<") if op in ("=", "<", "<=") else 2**31 - 1
            low, high = max(low, -(2**31)), min(high, 2**31 - 1)
            if low > high:
                return MatchNoDocsQuery()
            clause = IntPoint.newRangeQuery(field, low, high)
        elif key == "wiki_description":
            tokens = words(value)
            if not tokens:
                return MatchNoDocsQuery()
            phrase = PhraseQuery.Builder()
            for token in tokens:
                phrase.add(Term("wiki_description", token))
            clause = phrase.build()
        elif key in ("position", "bats", "throws", "active", "id", "born"):
            clause = TermQuery(Term(field, filter_value(key, value)))
        else:
            # Literal, case-insensitive substring, just like Part 1. Escape wildcard syntax.
            literal = re.sub(r"([\\*?])", r"\\\1", value.casefold())
            clause = WildcardQuery(Term(field, "*" + literal + "*"))
        query.add(clause, BooleanClause.Occur.FILTER)
    return query.build()


class PlayerSearch:
    def __init__(self, index=INDEX, boosts=None):
        self.boosts = dict(BOOSTS if boosts is None else boosts)
        self.directory = FSDirectory.open(Paths.get(str(Path(index).resolve())))
        self.reader = DirectoryReader.open(self.directory)
        self.searcher = IndexSearcher(self.reader)
        self.searcher.setSimilarity(BM25Similarity(1.2, 0.75))
        self.sort = Sort([SortField.FIELD_SCORE, SortField("sort_name", SortField.Type.STRING),
                          SortField("sort_id", SortField.Type.STRING)])

    def search(self, text, limit=10):
        query = make_query(text, self.boosts)
        hits = self.searcher.search(query, limit, self.sort, True)
        stored = self.searcher.storedFields()
        rows = [(json.loads(stored.document(hit.doc).get("record")), hit.score, hit.doc) for hit in hits.scoreDocs]
        return self.searcher.count(query), rows

    def player(self, player_id):
        hits = self.searcher.search(TermQuery(Term("player_id", player_id)), 1)
        if not hits.scoreDocs:
            return None
        return json.loads(self.searcher.storedFields().document(hits.scoreDocs[0].doc).get("record"))

    def close(self):
        self.reader.close()
        self.directory.close()


def show(engine, text, limit, explain=False):
    total, rows = engine.search(text, limit)
    print(f"\n{text!r}: {total:,} matches (BM25); showing {len(rows)}")
    for row, score, _ in rows:
        print(f"{row['player_id']:>7}  {score:8.4f}  {(row.get('name') or ''):<28}  "
              f"{(row.get('position_code') or ''):<3}  {row.get('last_associated_team') or ''}")
    if explain and rows:
        print(engine.searcher.explain(make_query(text, engine.boosts), rows[0][2]))


def show_player(engine, player_id):
    player_id = player_id.strip()
    if not player_id.isdigit():
        raise ValueError("Use /player PLAYER_ID, for example /player 110001")
    row = engine.player(player_id)
    if row is None:
        print(f"Player {player_id} was not found in this index.")
        return
    print(f"\nFull stored record for {row.get('name', player_id)}:")
    for field, value in row.items():
        value = "<missing>" if value is None else str(value)
        print(textwrap.fill(value, width=100, initial_indent=f"{field}: ", subsequent_indent="  "))
    if not row.get("wiki_description"):
        print("No wiki_description stored for this player. Rebuild from players_enriched to include biographies.")


def compare(engine, text, limit):
    from indexer import query_index
    show(engine, text, limit)
    if any(key == "wiki_description" for key, _, _ in parse_query(text)[1]):
        print("Part 1 has no wiki_description field; this query is available only in the new index.")
        return
    with (PART1 / "indexes.json").open() as stream:
        index = json.load(stream)
    with (PART1 / "players_data.json").open() as stream:
        players = json.load(stream)
    for method in (1, 2):
        rows, total = query_index(index, players, text, method, limit)
        print(f"\nPart 1 TF-IDF #{method}: {total:,} matches")
        for pid, score in rows:
            print(f"{pid:>7}  {score:8.4f}  {players[pid]['name']}")


HELP = """Plain text: OR between words. Filters: AND with each other and the text.
Examples: Aaron Ruth | position=P team="New York" | Yankees bats=R
          weight>=200 active=false | birth_year=1912 | id=110001
Filters: """ + ", ".join(FILTER_FIELDS) + """
Commands: /search QUERY, /player PLAYER_ID (all fields and full biography), /stats, /boosts, /compare QUERY,
          /configure output limit N, /help, /back, /exit
wiki_description="miss the entire 2023 season" matches consecutive biography words, in order.
Other quoted filters retain their original substring behavior.
"""


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("query", nargs="?", help="Quote the whole query in your shell; omit for interactive mode")
    parser.add_argument("--index", type=Path, default=INDEX)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--boost", action="append", default=[], metavar="FIELD=WEIGHT")
    parser.add_argument("--no-boosts", action="store_true", help="Use weight 1 for every text field")
    parser.add_argument("--explain", action="store_true", help="Explain the top BM25 result")
    parser.add_argument("--player", metavar="PLAYER_ID", help="Display all stored player fields and the full wiki biography")
    args = parser.parse_args()
    if args.player is not None and args.query is not None:
        parser.error("Use either a search query or --player PLAYER_ID")
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")
    boosts = dict.fromkeys(BOOSTS, 1.0) if args.no_boosts else dict(BOOSTS)
    for setting in args.boost:
        try:
            field, weight = setting.split("=", 1)
            weight = float(weight)
            if field not in boosts or not math.isfinite(weight) or weight <= 0:
                raise ValueError()
            boosts[field] = weight
        except ValueError:
            parser.error(f"Invalid boost: {setting}; use a known field and a positive finite weight")
    if not (args.index / "segments.gen").exists() and not list(args.index.glob("segments_*")):
        parser.error("No Lucene index found; run build_index.py first")
    engine = PlayerSearch(args.index, boosts)
    try:
        if args.player is not None:
            try:
                show_player(engine, args.player)
            except ValueError as exc:
                parser.error(str(exc))
        elif args.query is not None:
            try:
                show(engine, args.query, args.limit, args.explain)
            except ValueError as exc:
                parser.error(str(exc))
        else:
            print(f"MLB PyLucene: {engine.reader.numDocs():,} players. Type /help for syntax.")
            while True:
                try:
                    text = get_line("lucene> ")
                    if text in ("/exit", "/quit"):
                        break
                    if text in ("/help", "/search", "/back", ""):
                        if text == "/help":
                            print(HELP)
                    elif text in ("/stats", "/boosts"):
                        print({"players": engine.reader.numDocs(), "ranking": "BM25", "boosts": engine.boosts})
                    elif text == "/player" or text.startswith("/player "):
                        show_player(engine, text[len("/player"):])
                    elif text.startswith("/configure output limit "):
                        limit = int(text.split()[-1])
                        if not 1 <= limit <= 1000:
                            raise ValueError("Output limit must be between 1 and 1000")
                        args.limit = limit
                    elif text == "/compare" or text.startswith("/compare "):
                        queries = [text[len("/compare "):]] if text != "/compare" else ["Bob Griffith", "New York", "Schoolboy"]
                        for query in queries:
                            compare(engine, query, args.limit)
                    elif text.startswith("/") and not text.startswith("/search "):
                        print("Unknown command. Type /help.")
                    else:
                        show(engine, text.removeprefix("/search "), args.limit, args.explain)
                except ValueError as exc:
                    print(f"Query error: {exc}")
                except (EOFError, KeyboardInterrupt):
                    break
    finally:
        engine.close()
