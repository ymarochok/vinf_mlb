"""3. Join names + birth dates; reject ambiguous mappings; save only players with a wiki match."""
import argparse
from functools import partial
from pathlib import Path

from pyspark import StorageLevel
from pyspark.sql import functions as F

from common import OUTPUT, normalize_name, save as save_table, start_spark
from extract_wiki import WIKI_COLUMNS, extract_identity, extract_wiki


def join_players(spark, wiki_dir=OUTPUT):
    wiki_dir = Path(wiki_dir).resolve()
    save = partial(save_table, output=wiki_dir)
    players = spark.read.parquet(str(OUTPUT / "players"))
    # REGEX processing starts from exported wiki Parquet (full dump or filtered input).
    pages = spark.read.parquet(str(wiki_dir / "wiki"))
    # Scan identities only. Do not clean/cache 42 GB of article bodies before matching.
    identify = F.udf(extract_identity, "wiki_name STRING, wiki_birth_date STRING")
    identities = (pages.select("wiki_page_id", "wiki_title", identify("wiki_wikitext").alias("identity"))
                  .filter("identity.wiki_birth_date IS NOT NULL")
                  .select("wiki_page_id", "wiki_title", "identity.*"))
    normalize = F.udf(normalize_name, "string")
    p = (players.select("player_id", "birth_date", F.explode(F.array("name", "full_name")).alias("alias"))
         .withColumn("name_key", normalize("alias")).filter("name_key != ''").dropDuplicates())
    w = (identities.select("wiki_page_id", "wiki_birth_date", F.explode(F.array(
        F.regexp_replace("wiki_title", r"\s+\([^()]+\)$", ""), F.col("wiki_name"))).alias("alias"))
         .withColumn("name_key", normalize("alias")).filter("name_key != ''").dropDuplicates())
    candidates = (w.join(F.broadcast(p), "name_key").filter("birth_date = wiki_birth_date")
                  .select("player_id", "wiki_page_id").distinct().cache())
    # A match must be one-to-one in BOTH directions. No arbitrary first() match.
    ambiguous_players = candidates.groupBy("player_id").count().filter("count > 1")
    ambiguous_pages = candidates.groupBy("wiki_page_id").count().filter("count > 1")
    matches = (candidates.join(ambiguous_players.select("player_id"), "player_id", "left_anti")
               .join(ambiguous_pages.select("wiki_page_id"), "wiki_page_id", "left_anti")
               .withColumn("match_method", F.lit("name_and_birth_date")))
    ambiguous = candidates.join(matches.select("player_id"), "player_id", "left_anti")
    save(ambiguous, "ambiguous")
    # Full REGEX biography extraction runs only on accepted page IDs.
    matched_pages = pages.join(F.broadcast(matches), "wiki_page_id", "inner")
    parse = F.udf(extract_wiki, ", ".join(f"{c} STRING" for c in WIKI_COLUMNS))
    wiki = matched_pages.select("player_id", "match_method", parse(
        "wiki_page_id", "wiki_title", "wiki_revision_id", "wiki_wikitext").alias("wiki")).select(
            "player_id", "match_method", "wiki.*")
    enriched = (players.join(wiki, "player_id", "inner")
                .withColumnRenamed("wiki_text", "wiki_description")
                .drop("wiki_birth_date").persist(StorageLevel.DISK_ONLY))
    save(enriched, "players_enriched")
    stats = enriched.agg(F.count("*").alias("matched_players"),
                         F.countDistinct("wiki_page_id").alias("unique_wiki_pages"))
    stats = stats.withColumn("players", F.lit(players.count()))
    stats = (stats.withColumn("unmatched_players", F.col("players") - F.col("matched_players"))
             .withColumn("coverage_percent", F.round(100 * F.col("matched_players") / F.col("players"), 2)))
    stats = stats.withColumn("ambiguous_players", F.lit(ambiguous.select("player_id").distinct().count()))
    stats = stats.withColumn("ambiguous_wiki_pages", F.lit(ambiguous_pages.count()))
    stats = stats.withColumn("wiki_pages", F.lit(pages.count()))
    stats.write.mode("overwrite").json(str(wiki_dir / "join_stats"))
    stats.show(truncate=False)
    (enriched.filter("wiki_page_id IS NOT NULL").select("name", "birth_date", "wiki_title", "match_method")
     .orderBy("name").show(10, False))
    candidates.unpersist()
    enriched.unpersist()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wiki-dir", type=Path, default=OUTPUT, help="Folder containing wiki/; also receives join outputs")
    args = parser.parse_args()
    for path in (OUTPUT / "players", args.wiki_dir / "wiki"):
        if not (path / "_SUCCESS").exists():
            parser.error(f"Export not finished or missing: {path}. Wait for its exporter to finish.")
    spark = start_spark()
    join_players(spark, args.wiki_dir)
    spark.stop()
