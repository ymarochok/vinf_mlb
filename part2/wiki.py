"""2. Export EVERY page from the compressed Wikipedia XML to Spark Parquet."""
import sys

from pyspark.sql import functions as F

from common import OUTPUT, ROOT, save, start_spark


def export_wiki(spark, source):
    # Only the required XML fields; the <text> value also has XML attributes.
    schema = ("id STRING, title STRING, ns INT, redirect STRUCT<_title:STRING>, "
              "revision STRUCT<id:STRING,text:STRUCT<_VALUE:STRING>>")
    pages = (spark.read.format("xml").option("rowTag", "page").option("mode", "FAILFAST")
             .schema(schema).load(source))
    # No filtering: preserve all namespaces, redirects and raw wikitext.
    wiki = pages.select(F.col("id").alias("wiki_page_id"), F.col("title").alias("wiki_title"),
                        "ns", F.col("redirect._title").alias("redirect_title"),
                        F.col("revision.id").alias("wiki_revision_id"),
                        F.col("revision.text._VALUE").alias("wiki_wikitext"))
    save(wiki, "wiki")
    stats = {"xml_pages": spark.read.parquet(str(OUTPUT / "wiki")).count(),
             "source": source}
    spark.createDataFrame([stats]).write.mode("overwrite").json(str(OUTPUT / "wiki_stats"))
    print(stats)


if __name__ == "__main__":
    spark = start_spark()
    export_wiki(spark, sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "input/*.xml.bz2"))
    spark.stop()
