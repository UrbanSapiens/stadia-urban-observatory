#!/usr/bin/env python3
"""Fetch architect fields from Wikipedia infoboxes."""

import csv
import json
import re
import subprocess
import time
from pathlib import Path
from urllib.parse import quote


# Config
REVIEW = Path("outputs/architects_review.csv")
OUT = Path("outputs/architects_wikipedia.csv")
CACHE = Path("cache/wikipedia_infobox/")
USER_AGENT = "venue-architect-lookup/1.0"
PAUSE_S = 1.0
LANGS = ["en", "de", "pt", "es", "fr", "it"]
FIELDS_BY_LANG = {
    "en": ["architect", "architects"],
    "de": ["Architekt", "Architekten"],
    "pt": ["arquiteto", "arquitecto", "arquitetos"],
    "es": ["arquitecto", "arquitectos"],
    "fr": ["architecte", "architectes"],
    "it": ["progetto", "progettista", "architetto"],
}

OUTPUT_FIELDS = [
    "slug", "name", "wikidata_item", "lang", "page_title", "page_url",
    "status", "field", "raw_value", "architects_clean", "keep",
]
RETRY_WAITS = [10, 30, 60]
LIST_TEMPLATES = {
    "ubl", "unbulleted list", "plainlist", "plain list", "hlist", "flatlist"
}


def curl_once(args):
    """Return the response body, HTTP status, and curl return code."""
    command = [
        "curl", "--silent", "--show-error", "--user-agent", USER_AGENT,
        *args, "--write-out", "\n%{http_code}",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    body, separator, status_text = result.stdout.rpartition("\n")
    if not separator or not status_text.isdigit():
        return result.stdout, 0, result.returncode
    return body, int(status_text), result.returncode


def fetch_sitelinks(qids):
    """Fetch sitelinks in batches and stop if Wikidata cannot be read."""
    sitelinks = {}
    for start in range(0, len(qids), 50):
        batch = qids[start:start + 50]
        ids = quote("|".join(batch), safe="|")
        url = (
            "https://www.wikidata.org/w/api.php?action=wbgetentities&ids="
            f"{ids}&props=sitelinks&format=json"
        )
        body, status, returncode = curl_once([url])
        if returncode != 0 or status != 200:
            raise RuntimeError(
                f"Wikidata sitelinks fetch failed: curl={returncode}, HTTP={status}"
            )
        try:
            entities = json.loads(body)["entities"]
        except (json.JSONDecodeError, KeyError, TypeError) as error:
            raise RuntimeError(f"Invalid Wikidata sitelinks response: {error}") from error
        for qid in batch:
            entity_links = entities.get(qid, {}).get("sitelinks", {})
            sitelinks[qid] = {
                lang: entity_links[f"{lang}wiki"]["title"]
                for lang in LANGS
                if f"{lang}wiki" in entity_links
            }
    return sitelinks


def fetch_page(qid, lang, title):
    """Read a cached parse response or fetch it with specified retries."""
    cache_path = CACHE / f"{qid}_{lang}.json"
    if cache_path.exists():
        try:
            return json.loads(cache_path.read_text(encoding="utf-8")), True
        except (OSError, json.JSONDecodeError):
            return None, False

    args = [
        "-G", f"https://{lang}.wikipedia.org/w/api.php",
        "--data-urlencode", "action=parse",
        "--data-urlencode", f"page={title}",
        "--data-urlencode", "prop=wikitext",
        "--data-urlencode", "section=0",
        "--data-urlencode", "redirects=1",
        "--data-urlencode", "format=json",
    ]
    for attempt in range(4):
        body, status, returncode = curl_once(args)
        if returncode == 0 and status == 200:
            try:
                parsed = json.loads(body)
            except json.JSONDecodeError:
                return None, False
            cache_path.write_text(body, encoding="utf-8")
            time.sleep(PAUSE_S)
            return parsed, True
        if (status == 429 or 500 <= status <= 599) and attempt < 3:
            time.sleep(RETRY_WAITS[attempt])
            continue
        return None, False
    return None, False


def extract_field(wikitext, lang):
    """Return the first configured field and its multiline raw value."""
    lines = wikitext.splitlines()
    for field in FIELDS_BY_LANG[lang]:
        pattern = re.compile(rf"^\s*\|\s*{re.escape(field)}\s*=", re.IGNORECASE)
        for index, line in enumerate(lines):
            match = pattern.match(line)
            if not match:
                continue
            value_lines = [line[match.end():]]
            for following in lines[index + 1:]:
                if re.match(r"^\s*(?:\||}})", following):
                    break
                value_lines.append(following)
            raw = "\n".join(value_lines)
            if raw.strip():
                return field, raw
    return "", ""


def template_replacement(match):
    """Apply the specified rule to one template without nested templates."""
    content = match.group(1)
    parts = content.split("|")
    name = parts[0].strip().casefold()
    positional = [part.strip() for part in parts[1:] if "=" not in part]
    if name in LIST_TEMPLATES:
        return "; ".join(positional)
    return positional[-1] if positional else ""


def clean_value(raw):
    """Clean a raw infobox value in the order required by the spec."""
    value = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL)
    value = re.sub(r"<ref\b[^>]*/\s*>", "", value, flags=re.IGNORECASE)
    value = re.sub(r"<ref\b[^>]*>.*?</ref\s*>", "", value,
                   flags=re.IGNORECASE | re.DOTALL)
    value = re.sub(r"\[\[([^\]|]+)\|([^\]]+)]]", r"\2", value)
    value = re.sub(r"\[\[([^\]]+)]]", r"\1", value)
    value = re.sub(r"<br\s*/?>", "; ", value, flags=re.IGNORECASE)
    value = re.sub(r"{{\s*[·•]\s*}}", "; ", value)

    template_pattern = re.compile(r"{{([^{}]*)}}")
    while template_pattern.search(value):
        value = template_pattern.sub(template_replacement, value)

    lines = value.splitlines()
    if any(re.match(r"^\s*\*", line) for line in lines):
        list_items = [re.sub(r"^\s*\*\s*", "", line).strip()
                      for line in lines if re.match(r"^\s*\*", line)]
        other_lines = [line for line in lines if not re.match(r"^\s*\*", line)]
        value = " ".join(other_lines + ["; ".join(list_items)])
    value = value.replace("'''", "").replace("''", "")
    value = re.sub(r"\s+", " ", value).strip(";, ")
    return value


def base_row(venue, lang=""):
    return {
        "slug": venue["slug"],
        "name": venue["name"],
        "wikidata_item": venue["wikidata_item"].strip(),
        "lang": lang,
        "page_title": "",
        "page_url": "",
        "status": "",
        "field": "",
        "raw_value": "",
        "architects_clean": "",
        "keep": "",
    }


def page_row(venue, lang, title):
    row = base_row(venue, lang)
    row["page_title"] = title
    row["page_url"] = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"
    response, fetched = fetch_page(row["wikidata_item"], lang, title)
    if not fetched:
        row["status"] = "fetch_failed"
        return row
    try:
        wikitext = response["parse"]["wikitext"]["*"]
    except (KeyError, TypeError):
        row["status"] = "fetch_failed"
        return row
    field, raw = extract_field(wikitext, lang)
    if not raw:
        row["status"] = "not_in_infobox"
        return row
    cleaned = clean_value(raw)
    row["field"] = field
    row["raw_value"] = raw.replace("\n", " \\n ")
    row["architects_clean"] = cleaned
    row["status"] = "found" if cleaned else "empty_after_clean"
    return row


def main():
    CACHE.mkdir(parents=True, exist_ok=True)
    with REVIEW.open(newline="", encoding="utf-8-sig") as handle:
        venues = [row for row in csv.DictReader(handle) if not row["architects"].strip()]

    qids = list(dict.fromkeys(
        row["wikidata_item"].strip() for row in venues if row["wikidata_item"].strip()
    ))
    sitelinks = fetch_sitelinks(qids)
    output_rows = []
    for venue in venues:
        qid = venue["wikidata_item"].strip()
        if not qid:
            row = base_row(venue)
            row["status"] = "no_item"
            output_rows.append(row)
            continue
        for lang in LANGS:
            title = sitelinks.get(qid, {}).get(lang)
            if not title:
                row = base_row(venue, lang)
                row["status"] = "no_page"
                output_rows.append(row)
                continue
            output_rows.append(page_row(venue, lang, title))

    lang_order = {lang: index for index, lang in enumerate(LANGS)}
    output_rows.sort(key=lambda row: (row["slug"], lang_order.get(row["lang"], -1)))
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_FIELDS)
        writer.writeheader()
        writer.writerows(output_rows)
    print(f"Wrote {len(output_rows)} rows to {OUT}")


if __name__ == "__main__":
    main()
