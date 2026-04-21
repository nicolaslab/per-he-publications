"""
fetch_publications.py

Reads a list of researchers from orcid_ids.csv, fetches their publications
from the ORCID public API, deduplicates by DOI and title, fetches full author
lists from CrossRef, classifies physics education papers, and saves everything
to _data/publications.json for the website.
"""

import requests
import json
import csv
import time
import os
import html
import re
from datetime import datetime

# ── API settings ──────────────────────────────────────────────────────────────

ORCID_API    = "https://pub.orcid.org/v3.0"
CROSSREF_API = "https://api.crossref.org/works"
HEADERS      = {"Accept": "application/json",
                "User-Agent": "PER-HE-Publications/1.0 (https://per-he.org; mailto:per-he@per-he.org)"}

# ── Physics Education Research journal list ───────────────────────────────────
# Update this list in the repo directly — no need to edit this script

# Papers published in these journals are automatically tagged as PER

PER_JOURNALS = {
    "active learning in higher education",
    "american journal of physics",
    "cbe life sciences education",
    "european journal of physics",
    "Europhysics Letters",
    "higher education",
    "innovations in education and teaching international",
    "International Journal for Academic Development",
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

# ── Text helpers ──────────────────────────────────────────────────────────────

def clean_text(s):
    """Decode HTML entities and strip whitespace."""
    return html.unescape(s or "").strip()


def normalise_title(title):
    """Simplified title for duplicate detection — lowercase, no punctuation."""
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


def format_author_name(author):
    """
    Turn a CrossRef author object into a readable name string.
    CrossRef gives {'given': 'Nicolas', 'family': 'Labrosse'} or just {'name': 'Some Org'}
    """
    if "family" in author:
        given  = author.get("given", "").strip()
        family = author.get("family", "").strip()
        if given:
            # Abbreviate given names to initials: "Nicolas" → "N."
            initials = " ".join(f"{n[0]}." for n in given.split() if n)
            return f"{family}, {initials}"
        return family
    return author.get("name", "Unknown").strip()


def format_author_list(authors):
    """
    Format a list of author name strings using et al. after 3 names.
    Returns a display string and a full string (for searching).
    """
    if not authors:
        return "", ""
    full    = "; ".join(authors)
    if len(authors) <= 3:
        display = "; ".join(authors)
    else:
        display = "; ".join(authors[:3]) + " et al."
    return display, full


# ── ORCID fetching ────────────────────────────────────────────────────────────

def get_doi(work_summary):
    """Pull the DOI out of a work summary, if there is one."""
    ids = (work_summary.get("external-ids") or {}).get("external-id") or []
    for item in ids:
        if (item or {}).get("external-id-type") == "doi":
            return clean_doi(item.get("external-id-value") or "")
    return None


def get_url(work_summary):
    """Get the best available URL for a work (DOI link preferred)."""
    doi = get_doi(work_summary)
    if doi:
        return f"https://doi.org/{doi}"
    url_field = work_summary.get("url")
    if url_field:
        val = (url_field.get("value") or "").strip()
        if val:
            return val
    ids = (work_summary.get("external-ids") or {}).get("external-id") or []
    for item in ids:
        id_url = (item or {}).get("external-id-url") or {}
        link   = (id_url.get("value") or "").strip()
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
                           .get("title",  {})
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
        "title":        title,
        "year":         year,
        "journal":      journal,
        "type":         w_type,
        "doi":          doi,
        "url":          url,
        "is_per":       is_per_paper(title, journal),
        "authors":      [author_name],       # ORCID-known authors only (temporary)
        "authors_full": "",                  # filled in by CrossRef lookup
        "authors_display": author_name,      # display string, filled in later
        "orcids":       [author_orcid],
        "_title_key":   normalise_title(title),
    }


# ── CrossRef author enrichment ────────────────────────────────────────────────

def fetch_authors_from_crossref(doi):
    """
    Look up a DOI on CrossRef and return (display_string, full_string).
    Returns (None, None) if the lookup fails or no authors are found.
    """
    url = f"{CROSSREF_API}/{doi}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return None, None
        r.raise_for_status()
        data    = r.json().get("message", {})
        cr_authors = data.get("author", [])
        if not cr_authors:
            return None, None
        names = [format_author_name(a) for a in cr_authors]
        return format_author_list(names)
    except requests.RequestException:
        return None, None


def enrich_with_crossref(all_pubs):
    """
    For every publication that has a DOI, fetch the full author list
    from CrossRef and update the authors_display and authors_full fields.
    Works without a DOI keep the ORCID-derived author list.
    """
    total  = sum(1 for p in all_pubs if p.get("doi"))
    done   = 0
    print(f"\nFetching full author lists from CrossRef for {total} DOI-linked papers...")

    for pub in all_pubs:
        doi = pub.get("doi")
        if not doi:
            # No DOI: use whatever ORCID gave us
            display, full = format_author_list(pub["authors"])
            pub["authors_display"] = display
            pub["authors_full"]    = full
            continue

        display, full = fetch_authors_from_crossref(doi)
        if display:
            pub["authors_display"] = display
            pub["authors_full"]    = full
        else:
            # CrossRef lookup failed: fall back to ORCID authors
            display, full = format_author_list(pub["authors"])
            pub["authors_display"] = display
            pub["authors_full"]    = full

        done += 1
        if done % 20 == 0:
            print(f"  {done}/{total} done...")
        time.sleep(0.2)   # polite rate limiting for CrossRef

    return all_pubs


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    researchers = load_researchers()

    by_doi   = {}   # normalised DOI   → publication
    by_title = {}   # normalised title → publication (no DOI)
    no_key   = []   # neither (very rare)

    for person in researchers:
        name  = person["name"]
        orcid = person["orcid"]
        print(f"\nFetching works for {name} ({orcid}) ...")

        groups = fetch_works(orcid)
        print(f"  Found {len(groups)} work groups.")

        for group in groups:
            pub = parse_work_group(group, name, orcid)
            if pub is None:
                continue

            doi       = pub["doi"]
            title_key = pub["_title_key"]

            if doi and doi in by_doi:
                # Already seen this DOI — just merge the ORCID author credit
                existing = by_doi[doi]
                if name not in existing["authors"]:
                    existing["authors"].append(name)
                if orcid not in existing["orcids"]:
                    existing["orcids"].append(orcid)
                if not existing["url"] and pub["url"]:
                    existing["url"] = pub["url"]

            elif doi:
                if title_key in by_title:
                    # Upgrade a title-matched entry to a DOI-keyed one
                    existing = by_title.pop(title_key)
                    existing["doi"] = doi
                    if name not in existing["authors"]:
                        existing["authors"].append(name)
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
                if not existing["url"] and pub["url"]:
                    existing["url"] = pub["url"]

            elif title_key:
                by_title[title_key] = pub
            else:
                no_key.append(pub)

        time.sleep(0.5)  # polite pause between ORCID requests

    all_pubs = list(by_doi.values()) + list(by_title.values()) + no_key
    print(f"\nTotal unique publications after deduplication: {len(all_pubs)}")

    # Enrich with full author lists from CrossRef
    all_pubs = enrich_with_crossref(all_pubs)

    # Clean up internal fields before saving
    for p in all_pubs:
        p.pop("_title_key", None)
        p.pop("authors",    None)   # ORCID-only list no longer needed
        p.pop("orcids",     None)

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
