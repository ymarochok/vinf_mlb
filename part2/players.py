"""1. Re-extract the saved MLB HTML in Spark with the Part 1 REGEX function."""
import sys

from pyspark import StorageLevel
from pyspark.sql import functions as F

from common import ROOT, save, start_spark
from extract_player import COLUMNS, extract_player


def extract_files(rows):
    for row in rows:
        result = dict.fromkeys(COLUMNS)
        error = None
        try:
            result = extract_player(bytes(row.content).decode("utf-8", errors="replace"))
            if result["player_id"] != row.path.split("/")[-2]:
                raise ValueError("Player ID does not match source directory")
        except ValueError as exc:
            error = str(exc)
        yield (*[result.get(c) or None for c in COLUMNS], row.path, error)


def export_players(spark, source):
    files = (spark.read.format("binaryFile").option("recursiveFileLookup", "true")
             .option("pathGlobFilter", "page.html").load(source))
    schema = ", ".join(f"{c} STRING" for c in COLUMNS + ["source_path", "error"])
    rows = spark.createDataFrame(files.select("path", "content").rdd.mapPartitions(
        extract_files), schema).persist(StorageLevel.DISK_ONLY)
    errors = rows.filter("error IS NOT NULL").select("source_path", "error")
    save(errors, "player_errors")
    players = (rows.filter("error IS NULL").drop("error")
               .withColumn("mlb_url", F.concat(F.lit("https://www.mlb.com/player/"),
                                              F.col("player_id"))))
    if players.groupBy("player_id").count().filter("count > 1").count():
        raise ValueError("Duplicate player IDs; inspect the saved source files")
    save(players, "players")
    print({"source_files": rows.count(), "players": players.count(), "errors": errors.count()})
    players.select("player_id", "name", "birth_date", "position_code").show(10, False)
    rows.unpersist()


if __name__ == "__main__":
    spark = start_spark()
    export_players(spark, sys.argv[1] if len(sys.argv) > 1 else str(ROOT.parent / "part1/stored_players"))
    spark.stop()
