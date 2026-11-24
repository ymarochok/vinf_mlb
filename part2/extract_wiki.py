"""REGEX extraction from wikitext, after Spark reads the surrounding XML."""
import html
import re
from datetime import date, datetime
from urllib.parse import quote

WIKI_COLUMNS = ["wiki_page_id", "wiki_title", "wiki_url", "wiki_revision_id",
                "wiki_name", "wiki_birth_date", "wiki_birthplace", "wiki_teams",
                "wiki_highlights", "wiki_categories", "wiki_text"]


def clean(text):
    text = re.sub(r"<!--.*?-->|<ref\b[^>]*>.*?</ref\s*>|<ref\b[^>]*/>", " ", text, flags=re.S | re.I)
    text = re.sub(r"\[\[(?:Category|File|Image):[^\]]*\]\]", " ", text, flags=re.I)
    text = re.sub(r"\[\[([^\[\]]+)\]\]", lambda m: m[1].split("|")[-1], text)
    # Remove innermost templates repeatedly: useful cleanup, not a full wiki parser.
    for _ in range(10):
        text = re.sub(r"\{\{[^{}]*\}\}", " ", text)
    text = re.sub(r"\[https?://\S+\s*([^\]]*)\]", r"\1", text)
    text = re.sub(r"<[^>]+>|'{2,}|={2,}", " ", text)
    return " ".join(html.unescape(text).split())


def birth_date(value):
    template = re.search(r"\{\{\s*(?:birth date(?: and age)?|bda)\s*\|"
                         r"(?:\s*(?:df|mf)\s*=\s*[^|{}]+\|)*\s*"
                         r"(\d{4})\s*\|\s*(\d{1,2})\s*\|\s*(\d{1,2})", value, re.I)
    if template:
        try:
            return date(*map(int, template.groups())).isoformat()
        except ValueError:
            return None
    value = clean(value)
    for fmt in ("%Y-%m-%d", "%B %d, %Y", "%d %B %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def infobox_fields(text):
    text = text or ""
    # Isolate this infobox, including its nested templates, before reading fields.
    start = re.search(r"\{\{\s*Infobox\s+(?:baseball|MLB)\s+biography\b", text, re.I)
    infobox = ""
    if start:
        depth = 0
        for token in re.finditer(r"\{\{|\}\}", text[start.start():]):
            depth += 1 if token[0] == "{{" else -1
            if depth == 0:
                infobox = text[start.start():start.start() + token.end()]
                break
    fields = dict(re.findall(r"^\s*\|\s*(\w+)\s*=\s*(.*?)(?=^\s*\||^\}\}|\Z)",
                             infobox, re.M | re.S))
    return {k.lower(): v for k, v in fields.items()}


def extract_identity(text):
    fields = infobox_fields(text)
    return clean(fields.get("name", "")) or None, birth_date(fields.get("birth_date", ""))


def extract_wiki(page_id, title, revision_id, text):
    text = text or ""
    fields = infobox_fields(text)
    categories = re.findall(r"\[\[Category:([^\]|]+)(?:\|[^\]]*)?\]\]", text, re.I)
    return (str(page_id), title, "https://en.wikipedia.org/wiki/" + quote(title.replace(" ", "_")),
            str(revision_id) if revision_id else None,
            clean(fields.get("name", "")) or None, birth_date(fields.get("birth_date", "")),
            clean(fields.get("birth_place", "")) or None, clean(fields.get("teams", "")) or None,
            clean(fields.get("highlights", "")) or None, " | ".join(categories) or None,
            clean(text))
