"""
fetch_publications.py

Reads a list of researchers from orcid_ids.csv, fetches their publications
from the ORCID public API, deduplicates by DOI and title, classifies physics
education papers, and saves everything to _data/publications.json.
"""

import requests
import json
import csv
import time
import os
import html
import re
from datetime import datetime

# ── ORCID API settings ────────────────────────────────────────────────────────

ORCID_API = "https://pub.orcid.org/v3.0"
HEADERS   = {"Accept": "application/json"}

# ── Physics Education Research journal list ───────────────────────────────────

# Papers published in these journals are automatically tagged as PER

PER_JOURNALS = {
    "active learning in higher education",
    "american journal of physics",
    "cbe life sciences education",
    "european journal of physics",
    "Europhysics Letters",
    "higher education",
    "innovations in education and teaching international",
    "international journal of science education",
    "journal of college science teaching",
    "Journal of Perspectives in Applied Academic Practice",
    "journal of research in science teaching",
    "Journal of Science Education and Technology",
    "latin american journal of physics education",
    "physical review physics education research",
    "physical review special topics - physics education research",
    "physics education",
    "science education",
    "studies in higher education",
    "teaching in higher education",
}

# Papers with these words in the title are also tagged as PER
PER_KEYWORDS = [
    "active learning",
    "conceptual understanding",
    "evidence-based teaching",
    "flipped classroom",
    "higher education",
    "inquiry-based",
    "lecture",
    "peer instruction",
    "physics curriculum",
    "physics education",
    "physics learning",
    "physics pedagogy",
    "physics teaching",
    "postgraduate",
    "science communication",
    "science education",
    "stem education",
    "students",
    "student engagement", 
    "student understanding",
    "undergraduate physics",
    "writing skills", 
]

# ── Helper functions ──────────────────────────────────────────────────────────

def clean_text(s):
    """Decode HTML entities and strip whitespace."""
    return html.unescape(s or "").strip()


def normalise_title(title):
    """
    Produce a simplified version of a title for duplicate detection.
    Lowercases, removes punctuation and extra spaces.
    """
    t = clean_text(title).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def clean_doi(doi_str):
    """Normalise a DOI to bare lowercase form, stripping any URL prefix."""
    if not doi_str:
        return None
    doi = doi_str.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/",
                   "https://dx.doi.org/", "http://dx.doi.org/"):
        if doi.startswith(prefix):
            doi = doi[len(prefix):]
    return doi or None


def get_doi(work_summary):
    """Pull the DOI out of a work summary, if there is one."""
    ids = (work_summary.get("external-ids") or {}).get("external-id") or []
    for item in ids:
        if (item or {}).get("external-id-type") == "doi":
            return clean_doi(item.get("external-id-value") or "")
    return None


def get_url(work_summary):
    """
    Get the best available URL for a work.
    Prefers DOI link, falls back to any URL stored in ORCID.
    """
    doi = get_doi(work_summary)
    if doi:
        return f"https://doi.org/{doi}"

    # Try the url field stored directly on the work
    url_field = work_summary.get("url")
    if url_field:
        val = (url_field.get("value") or "").strip()
        if val:
            return val

    # Try external IDs for other link types (handle, uri, etc.)
    ids = (work_summary.get("external-ids") or {}).get("external-id") or []
    for item in ids:
        id_type = (item or {}).get("external-id-type", "")
        id_url  = (item or {}).get("external-id-url") or {}
        link    = (id_url.get("value") or "").strip()
        if link:
            return link

    return ""


def is_per_paper(title, journal):
    """Decide whether a paper looks like Physics Education Research."""
    title_lower   = (title   or "").lower()
    journal_lower = (journal or "").lower()
    if journal_lower in PER_JOURNALS:
        return True
    for keyword in PER_KEYWORDS:
        if keyword in title_lower:
            return True
    return False


# ── Data loading ──────────────────────────────────────────────────────────────

def load_researchers(filepath="orcid_ids.csv"):
    """Read the list of researchers from the CSV file."""
    researchers = []
    with open(filepath, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            researchers.append({
                "name":        row["name"].strip(),
                "orcid":       row["orcid"].strip(),
                "institution": row.get("institution", "").strip(),
            })
    print(f"Loaded {len(researchers)} researchers.")
    return researchers


def fetch_works(orcid_id):
    """Ask the ORCID API for all works belonging to one researcher."""
    url = f"{ORCID_API}/{orcid_id}/works"
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        r.raise_for_status()
        return r.json().get("group", [])
    except requests.RequestException as e:
        print(f"  Warning: could not fetch works for {orcid_id}: {e}")
        return []


def parse_work_group(group, author_name, author_orcid):
    """Extract the useful fields from one ORCID work group."""
    summaries = group.get("work-summary", [])
    if not summaries:
        return None
    s = summaries[0]

    title   = clean_text((s.get("title",  {})
                            .get("title", {})
                            .get("value", "Untitled")))
    journal = clean_text((s.get("journal-title") or {}).get("value", ""))
    w_type  = s.get("type", "")
    doi     = get_doi(s)
    url     = get_url(s)

    year = None
    pub_date = s.get("publication-date")
    if pub_date and pub_date.get("year"):
        year = pub_date["year"].get("value")

    return {
        "title":   title,
        "year":    year,
        "journal": journal,
        "type":    w_type,
        "doi":     doi,
        "url":     url,
        "is_per":  is_per_paper(title, journal),
        "authors": [author_name],
        "orcids":  [author_orcid],
        "_title_key": normalise_title(title),  # used for dedup, removed later
    }


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    researchers = load_researchers()

    by_doi      = {}   # normalised DOI  → publication
    by_title    = {}   # normalised title → publication (for works with no DOI)
    no_key      = []   # works with neither DOI nor title (very rare)

    for person in researchers:
        name  = person["name"]
        orcid = person["orcid"]
        print(f"Fetching works for {name} ({orcid}) ...")

        groups = fetch_works(orcid)
        print(f"  Found {len(groups)} work groups.")

        for group in groups:
            pub = parse_work_group(group, name, orcid)
            if pub is None:
                continue

            doi       = pub["doi"]
            title_key = pub["_title_key"]

            if doi and doi in by_doi:
                # Same DOI seen before — merge authors, prefer the version
                # that has a URL
                existing = by_doi[doi]
                if name not in existing["authors"]:
                    existing["authors"].append(name)
                if orcid not in existing["orcids"]:
                    existing["orcids"].append(orcid)
                if not existing["url"] and pub["url"]:
                    existing["url"] = pub["url"]

            elif doi:
                # Check if we've already seen this title without a DOI
                if title_key in by_title:
                    # Upgrade the existing title-keyed entry to a DOI-keyed one
                    existing = by_title.pop(title_key)
                    existing["doi"] = doi
                    if not existing["url"] and pub["url"]:
                        existing["url"] = pub["url"]
                    by_doi[doi] = existing
                else:
                    by_doi[doi] = pub

            elif title_key and title_key in by_title:
                # Same title, no DOI — merge authors
                existing = by_title[title_key]
                if name not in existing["authors"]:
                    existing["authors"].append(name)
                if orcid not in existing["orcids"]:
                    existing["orcids"].append(orcid)
                if not existing["url"] and pub["url"]:
                    existing["url"] = pub["url"]

            elif title_key:
                by_title[title_key] = pub

            else:
                no_key.append(pub)

        time.sleep(0.5)

    all_pubs = list(by_doi.values()) + list(by_title.values()) + no_key

    # Remove the internal dedup key before saving
    for p in all_pubs:
        p.pop("_title_key", None)

    # Sort: most recent first, then alphabetically by title
    all_pubs.sort(
        key=lambda p: (-(int(p["year"]) if p["year"] else 0), p["title"].lower())
    )

    output = {
        "last_updated": datetime.utcnow().strftime("%Y-%m-%d"),
        "total":        len(all_pubs),
        "publications": all_pubs,
    }

    os.makedirs("_data", exist_ok=True)
    with open("_data/publications.json", "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    per_count = sum(1 for p in all_pubs if p["is_per"])
    print(f"\nSaved {len(all_pubs)} publications to _data/publications.json")
    print(f"Of these, {per_count} are classified as physics education related.")


if __name__ == "__main__":
    main()
