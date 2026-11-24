"""Build a persistent PyLucene index from the Spark-exported player records."""
import argparse
import json
from pathlib import Path

from schema import BOOSTS, DATA, FILTER_FIELDS, INDEX, NUMERIC_FILTERS, filter_value, start_lucene, words

lucene = start_lucene()
from java.nio.file import Paths
from org.apache.lucene.analysis.core import WhitespaceAnalyzer
from org.apache.lucene.document import Document, Field, IntPoint, SortedDocValuesField, StoredField, StringField, TextField
from org.apache.lucene.index import IndexWriter, IndexWriterConfig
from org.apache.lucene.search.similarities import BM25Similarity
from org.apache.lucene.store import FSDirectory
from org.apache.lucene.util import BytesRef


def build_index(records, target=INDEX):
    records = list(records)
    ids = [r.get("player_id") for r in records]
    if not ids or any(not pid or not pid.strip() for pid in ids) or len(set(ids)) != len(ids):
        raise ValueError("Expected nonempty records with unique, nonempty player IDs")
    target = Path(target)
    target.mkdir(parents=True, exist_ok=True)
    directory = FSDirectory.open(Paths.get(str(target.resolve())))
    analyzer = WhitespaceAnalyzer()
    config = IndexWriterConfig(analyzer)
    config.setOpenMode(IndexWriterConfig.OpenMode.CREATE)
    config.setSimilarity(BM25Similarity(1.2, 0.75))
    writer = IndexWriter(directory, config)
    try:
        for row in records:
            doc = Document()
            doc.add(StringField("player_id", row["player_id"], Field.Store.YES))
            doc.add(StoredField("record", json.dumps(row, ensure_ascii=False)))
            for field in BOOSTS:
                tokens = " ".join(words(row.get(field) or ""))
                if tokens:
                    doc.add(TextField(field, tokens, Field.Store.NO))
            for key, field in FILTER_FIELDS.items():
                if key == "wiki_description":
                    continue  # Positional TextField above; never index an article as one giant term.
                value = (row.get(field) or "").strip()
                if key in NUMERIC_FILTERS:
                    try:
                        number = int(value[:4] if key.endswith("_year") else value)
                    except ValueError:
                        continue  # Missing values are not zero.
                    if -(2**31) <= number < 2**31:
                        doc.add(IntPoint("filter_" + key, lucene.JArray("int")([number])))
                else:
                    doc.add(StringField("filter_" + key, filter_value(key, value), Field.Store.NO))
            doc.add(SortedDocValuesField("sort_name", BytesRef((row.get("name") or "").casefold())))
            doc.add(SortedDocValuesField("sort_id", BytesRef(row["player_id"])))
            writer.addDocument(doc)
        writer.commit()
    except Exception:
        writer.rollback()
        raise
    else:
        writer.close()
    finally:
        directory.close()
        analyzer.close()
    print(f"Indexed {len(records):,} players -> {target}")
    return len(records)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--index", type=Path, default=INDEX)
    args = parser.parse_args()
    files = sorted(args.data.glob("part-*.json"))
    if not files or not (args.data / "_SUCCESS").exists():
        parser.error("Run export_index_data.py with the Spark environment first")
    def records():
        for file in files:
            with file.open(encoding="utf-8") as stream:
                for line in stream:
                    yield json.loads(line)
    build_index(records(), args.index)
