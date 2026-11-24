"""Open saved tables in an interactive Spark SQL demonstration."""
import argparse
from pathlib import Path
from code import interact

from common import OUTPUT, start_spark

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--wiki-dir", type=Path, default=OUTPUT, help="Wiki and join output folder")
args = parser.parse_args()
names = ("players", "player_errors", "wiki", "players_enriched", "ambiguous", "wiki_stats", "join_stats")
paths = {name: (OUTPUT if name in ("players", "player_errors") else args.wiki_dir) / name for name in names}
missing = [str(path) for path in paths.values() if not (path / "_SUCCESS").exists()]
if missing:
    parser.exit(1, "Tables are missing or their export is unfinished:\n" + "\n".join(missing)
                + "\nFor the prepared development tables use: python showcase.py --wiki-dir wiki_fast/output\n")
print("Wiki tables:", args.wiki_dir.resolve())
spark = start_spark()
for name in names[:-2]:
    table = spark.read.parquet(str(paths[name]))
    table.createOrReplaceTempView(name)
    print(f"\n{name}: {table.count()} rows")
    table.printSchema()
    table.show(5, truncate=50)
for name in ("wiki_stats", "join_stats"):
    spark.read.json(str(paths[name])).createOrReplaceTempView(name)
    spark.sql(f"SELECT * FROM {name}").show(truncate=False)

print('Try: spark.sql("SELECT name, wiki_title FROM players_enriched WHERE wiki_page_id IS NOT NULL LIMIT 10").show()')
print("Spark UI:", spark.sparkContext.uiWebUrl)
interact(local={"spark": spark})
spark.stop()
