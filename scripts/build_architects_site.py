#!/usr/bin/env python3
"""Merge every architect source into the one file the site build reads.

Run from the repo root:
    python3 scripts/build_architects_site.py
In:   outputs/architects_review_v2.csv   Wikidata pass with the hand-edited `keep` column
      outputs/architects_review.csv      the same file as the script wrote it (to spot hand entries)
      outputs/architects_wikipedia.csv   infobox values in six languages, not yet pruned
Out:  outputs/architects_site.csv        slug, architects, source, source_url

Order of trust: the `keep` column of the review file first, then Wikipedia in the
language order below (first language with a usable value). Wikipedia values are shown
as written on the page, only stripped of markup, so they can be reviewed on the site.
The source travels with every value because the two are not equally reliable.
"""
import csv, re
from pathlib import Path

# ---------------- config ----------------
ROOT = Path(__file__).resolve().parent.parent
REVIEW = ROOT / "outputs" / "architects_review_v2.csv"
REVIEW_ORIGINAL = ROOT / "outputs" / "architects_review.csv"
WIKIPEDIA = ROOT / "outputs" / "architects_wikipedia.csv"
OUT = ROOT / "outputs" / "architects_site.csv"
LANG_ORDER = ["en", "de", "fr", "es", "pt", "it"]
# Infobox fragments that are not a name: a field label, a template word, a description
# of the cooperation that built the stadium. Dropped piece by piece, case-insensitive.
NOT_A_NAME = {"ausführende gesellschaft", "lista", "chino-malienne"}
# ----------------------------------------


def clean(text):
    """Strip wiki and HTML markup left in an infobox value. Returns '' if nothing usable remains."""
    t = text or ""
    t = re.sub(r"<[^>]*>", " ", t)                          # <small>, <br>
    t = re.sub(r"\[https?://\S+\s+([^\]]+)\]", r"\1", t)    # [http://url label] -> label
    t = re.sub(r"\[https?://\S+\]", " ", t)
    t = re.sub(r"\{\{[^|{}]*\|", " ", t)                    # opening of a template such as {{Plain list|
    t = t.replace("{{", " ").replace("}}", " ").replace("[[", "").replace("]]", "")
    parts = [re.sub(r"\s+", " ", p).strip(" ,|") for p in t.split(";")]
    parts = [p for p in parts if re.search(r"[^\W\d_]{2,}", p)]   # needs at least two letters in a row
    parts = [p for p in parts if not re.fullmatch(r"\(.*\)", p)]  # drops a lone "(2006 renovation)"
    parts = [p for p in parts if p.lower() not in NOT_A_NAME]
    return "; ".join(parts)


def main():
    original = {r["slug"]: r["architects"].strip() for r in csv.DictReader(open(REVIEW_ORIGINAL, encoding="utf-8-sig"))}
    rows, slugs = {}, []
    for r in csv.DictReader(open(REVIEW, encoding="utf-8-sig")):
        slugs.append(r["slug"])
        keep = (r["keep"] or "").strip()
        if not keep:
            continue
        if original.get(r["slug"]):
            rows[r["slug"]] = (keep, "Wikidata", r["source"])
        else:   # Wikidata had nothing: the value was typed into the review file
            rows[r["slug"]] = (keep, "entered by hand", "")
    wiki = {}
    for r in csv.DictReader(open(WIKIPEDIA, encoding="utf-8")):
        if r["status"] == "found":
            wiki.setdefault(r["slug"], {})[r["lang"]] = (clean(r["architects_clean"]), r["page_url"])
    for slug, by_lang in wiki.items():
        if slug in rows:
            continue
        for lang in LANG_ORDER:
            value, url = by_lang.get(lang, ("", ""))
            if value:
                rows[slug] = (value, f"Wikipedia, {lang}", url)
                break
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["slug", "architects", "source", "source_url"])
        for slug in slugs:
            if slug in rows:
                w.writerow([slug, *rows[slug]])
    kinds = {}
    for _, source, _ in rows.values():
        key = source.split(",")[0]
        kinds[key] = kinds.get(key, 0) + 1
    print(f"{len(rows)} of {len(slugs)} venues with an architect: {kinds}; {len(slugs) - len(rows)} without")


if __name__ == "__main__":
    main()
