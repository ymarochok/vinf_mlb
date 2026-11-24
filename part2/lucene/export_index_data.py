"""Spark -> JSON Lines, for the separate PyLucene process. No new extraction."""
import json
import sys
import argparse
from pathlib import Path

from schema import DATA, PART2, ROOT

sys.path.insert(0, str(PART2))
from common import start_spark


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=PART2 / "output/players")
    args = parser.parse_args()
    source = args.input.resolve()
    if not (source / "_SUCCESS").exists():
        parser.error(f"Input table is missing or unfinished: {source}")
    spark = start_spark()
    players = spark.read.parquet(str(source)).cache()
    count = players.count()
    if not count or players.filter("player_id IS NULL OR trim(player_id) = ''").count():
        raise ValueError("Empty player table or missing player IDs")
    if players.select("player_id").distinct().count() != count:
        raise ValueError("Duplicate player IDs")
    # Only ~20,000 small records; one part file makes the bridge easy to inspect.
    players.orderBy("player_id").coalesce(1).write.mode("overwrite").json(str(DATA))
    (ROOT / "data/source.json").write_text(json.dumps({"source": str(source), "players": count}, indent=2) + "\n")
    print(f"Exported {count:,} players from Spark to {DATA}")
    players.unpersist()
    spark.stop()
