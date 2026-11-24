"""Shared Spark session, name normalization and Parquet output."""
import os
import re
import unicodedata
from pathlib import Path

from pyspark.sql import SparkSession

ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "output"


def start_spark():
    # spark-submit --master takes precedence over the local default.
    os.environ.setdefault("PYSPARK_PYTHON", os.sys.executable)
    os.environ.setdefault("PYSPARK_SUBMIT_ARGS", "--master local[2] --driver-memory 4g pyspark-shell")
    spark = (SparkSession.builder.appName("MLB consultation 3")
             .config("spark.sql.shuffle.partitions", "16")
             .config("spark.sql.parquet.enableVectorizedReader", "false")
             .config("spark.sql.execution.python.udf.maxRecordsPerBatch", "16").getOrCreate())
    spark.sparkContext.setLogLevel("WARN")
    for name in ("extract_player.py", "extract_wiki.py", "common.py"):
        spark.sparkContext.addPyFile(str(ROOT / name))
    return spark


def normalize_name(name):
    name = unicodedata.normalize("NFKD", name or "")
    name = "".join(c for c in name if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w\s]", " ", name.casefold()).split())


def save(table, name, output=OUTPUT):
    table.write.mode("overwrite").parquet(str(output / name))
