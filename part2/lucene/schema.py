"""Shared paths and fields; reuse the original query parser and word tokenizer."""
import sys
import re
import shlex
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PART2 = ROOT.parent
PART1 = PART2.parent / "part1"
DATA = ROOT / "data" / "players"
INDEX = ROOT / "index"
sys.path.insert(0, str(PART1))
from indexer import FILTER_FIELDS as ORIGINAL_FILTERS, NUMERIC_FILTERS, normalized_side, parse_query as original_parse, words

FILTER_FIELDS = dict(ORIGINAL_FILTERS, wiki_description="wiki_description")


def parse_query(query):
    """Extend the unchanged Part 1 parser with the biography phrase filter."""
    remaining, descriptions = [], []
    for part in shlex.split(query):
        match = re.fullmatch(r"wiki_description(>=|<=|=|>|<)(.+)", part, re.I)
        if match:
            if match[1] != "=":
                raise ValueError("wiki_description supports = only")
            descriptions.append(("wiki_description", "=", match[2]))
        else:
            remaining.append(part)
    text, filters = original_parse(shlex.join(remaining))
    return text, filters + descriptions

# Query-time weights: changing these does not require rebuilding the index.
BOOSTS = {
    "name": 5.0, "full_name": 3.0, "nickname": 2.0,
    "last_associated_team": 1.5, "teams_from_transactions": 1.0,
    "birthplace": 1.0, "position_code": 1.0, "bats": 1.0, "throws": 1.0,
    "wiki_description": 0.5,
}


def filter_value(key, value):
    value = value.casefold()
    if key in ("bats", "throws"):
        return normalized_side(value)
    if key == "position":
        return {"pitcher": "p", "catcher": "c", "shortstop": "ss", "first baseman": "1b",
                "second baseman": "2b", "third baseman": "3b"}.get(value, value)
    if key == "active":
        return {"1": "true", "yes": "true", "0": "false", "no": "false"}.get(value, value)
    return value


def start_lucene():
    import lucene
    if lucene.getVMEnv() is None:
        lucene.initVM()
    return lucene
