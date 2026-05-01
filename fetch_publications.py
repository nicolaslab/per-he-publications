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
import fnmatch

# ── API settings ──────────────────────────────────────────────────────────────

ORCID_API    = "https://pub.orcid.org/v3.0"
CROSSREF_API = "https://api.crossref.org/works"
HEADERS      = {"Accept": "application/json",
                "User-Agent": "PER-HE-Publications/1.0 (https://per-he.org; mailto:per-he@per-he.org)"}

# ── Physics Education Research classification lists ───────────────────────────
# Edit per_journals.txt and per_keywords.txt to add new entries.
# No need to touch this script.

def load_classification_lists():
    """Read PER journals and keywords from plain-text files."""
    def read_lines(filepath):
        with open(filepath, encoding="utf-8") as f:
            return [line.strip().lower() for line in f if line.strip()]

    journals = read_lines("per_journals.txt")
    keywords = read_lines("per_keywords.txt")
    print(f"Loaded {len(journals)} PER journals and {len(keywords)} PER keywords.")
    return journals, keywords


PER_JOURNALS, PER_KEYWORDS = load_classification_lists()


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
    """
    Decide whether a paper looks like Physics Education Research.
    Both journal names and keywords support * and ? wildcards.
    If no wildcard is present, journal matching is exact and
    keyword matching checks whether the phrase appears anywhere in the title.
    """
    title_lower   = (title   or "").lower()
    journal_lower = (journal or "").lower()

    for pattern in PER_JOURNALS:
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(journal_lower, pattern):
                return True
        else:
            # No wildcard: exact match
            if journal_lower == pattern:
                return True

    for pattern in PER_KEYWORDS:
        if "*" in pattern or "?" in pattern:
            if fnmatch.fnmatch(title_lower, pattern):
                return True
        else:
            # No wildcard: substring match (original behaviour)
            if pattern in title_lower:
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


def fetch_works(orcid_id, max_retries=3, retry_delay=10):
    """Ask the ORCID API for all works, with retry logic for empty responses."""
    url = f"{ORCID_API}/{orcid_id}/works"
    for attempt in range(1, max_retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            groups = r.json().get("group", [])
            if groups:
                return groups
            # Got a valid response but empty — may be rate limiting
            if attempt < max_retries:
                print(f"  Empty response on attempt {attempt}, retrying in {retry_delay}s...")
                time.sleep(retry_delay)
            else:
                print(f"  Warning: {max_retries} attempts all returned 0 works for {orcid_id}.")
                print(f"  This may be rate limiting — try re-running the workflow shortly.")
                return []
        except requests.RequestException as e:
            print(f"  Warning: could not fetch works for {orcid_id} (attempt {attempt}): {e}")
            if attempt < max_retries:
                time.sleep(retry_delay)
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
    Look up a DOI on CrossRef and return (display_string, full_string, journal).
    Returns (None, None, None) if the lookup fails or no authors are found.
    """
    url = f"{CROSSREF_API}/{doi}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=20)
        if r.status_code == 404:
            return None, None, None
        r.raise_for_status()
        data       = r.json().get("message", {})
        cr_authors = data.get("author", [])

        # Journal name is stored as a list in container-title
        container  = data.get("container-title") or []
        journal    = container[0].strip() if container else None

        if not cr_authors:
            return None, None, journal

        names = [format_author_name(a) for a in cr_authors]
        display, full = format_author_list(names)
        return display, full, journal

    except requests.RequestException:
        return None, None, None

def enrich_with_crossref(all_pubs):
    """
    For every publication that has a DOI, fetch the full author list and
    journal name from CrossRef, filling in any gaps left by ORCID.
    """
    total = sum(1 for p in all_pubs if p.get("doi"))
    done  = 0
    print(f"\nFetching full author lists from CrossRef for {total} DOI-linked papers...")

    for pub in all_pubs:
        doi = pub.get("doi")
        if not doi:
            display, full = format_author_list(pub["authors"])
            pub["authors_display"] = display
            pub["authors_full"]    = full
            continue

        display, full, journal = fetch_authors_from_crossref(doi)

        if display:
            pub["authors_display"] = display
            pub["authors_full"]    = full
        else:
            display, full = format_author_list(pub["authors"])
            pub["authors_display"] = display
            pub["authors_full"]    = full

        # Fill in missing journal name from CrossRef
        if not pub.get("journal") and journal:
            pub["journal"] = journal

        # Always re-evaluate PER classification with the best available metadata
        pub["is_per"] = is_per_paper(pub["title"], pub.get("journal", ""))

        done += 1
        if done % 20 == 0:
            print(f"  {done}/{total} done...")
        time.sleep(0.2)

    return all_pubs
  
def extract_family_names(authors_list):
    """
    Extract a set of normalised family names from a list of author name strings.
    Works for both 'Labrosse, N.' and 'Nicolas Labrosse' formats.
    """
    family_names = set()
    for name in (authors_list or []):
        name = name.strip()
        if not name:
            continue
        # Handle "Family, Given" format
        if "," in name:
            family = name.split(",")[0].strip()
        else:
            # Handle "Given Family" format — take the last word
            family = name.split()[-1].strip()
        family_names.add(re.sub(r"[^a-z]", "", family.lower()))
    return family_names


def years_compatible(year_a, year_b, tolerance=1):
    """Return True if two year strings are within `tolerance` years of each other."""
    try:
        return abs(int(year_a) - int(year_b)) <= tolerance
    except (TypeError, ValueError):
        return True   # if year is missing, don't rule out the match


def is_likely_duplicate(pub_a, pub_b):
    """
    Given two publications whose normalised titles already match,
    confirm they are the same paper by checking year and shared authors.
    Returns True if they are almost certainly the same work.
    """
    # Check 1: year must be compatible (within 1 year)
    if not years_compatible(pub_a.get("year"), pub_b.get("year"), tolerance=1):
        return False

    # Check 2: at least one author family name must be shared
    names_a = extract_family_names(pub_a.get("authors", []))
    names_b = extract_family_names(pub_b.get("authors", []))
    if names_a and names_b and names_a.isdisjoint(names_b):
        return False

    return True

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    researchers = load_researchers()

    by_doi       = {}   # normalised DOI   → publication
    by_title     = {}   # normalised title → publication (no DOI)
    title_to_doi = {}   # normalised title → DOI (reverse index)
    no_key       = []   # neither (very rare)

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
                # Exact DOI match — definitely the same paper
                existing = by_doi[doi]
                if name not in existing["authors"]:
                    existing["authors"].append(name)
                if orcid not in existing["orcids"]:
                    existing["orcids"].append(orcid)
                if not existing["url"] and pub["url"]:
                    existing["url"] = pub["url"]

            elif doi:
                # New DOI — but check if a no-DOI version already exists
                if title_key in by_title:
                    existing = by_title[title_key]
                    if is_likely_duplicate(existing, pub):
                        # Confirmed duplicate: upgrade the no-DOI entry
                        by_title.pop(title_key)
                        existing["doi"] = doi
                        if name not in existing["authors"]:
                            existing["authors"].append(name)
                        if not existing["url"] and pub["url"]:
                            existing["url"] = pub["url"]
                        by_doi[doi] = existing
                        print(f"    Merged (title+year+author match): '{pub['title'][:60]}...'")
                    else:
                        # Title matched but year/author check failed — treat as separate
                        by_doi[doi] = pub
                else:
                    by_doi[doi] = pub
                if title_key:
                    title_to_doi[title_key] = doi

            elif title_key and title_key in title_to_doi:
                # No DOI, but title matches a DOI-keyed entry
                existing = by_doi[title_to_doi[title_key]]
                if is_likely_duplicate(existing, pub):
                    if name not in existing["authors"]:
                        existing["authors"].append(name)
                    if orcid not in existing["orcids"]:
                        existing["orcids"].append(orcid)
                    if not existing["url"] and pub["url"]:
                        existing["url"] = pub["url"]
                    print(f"    Merged (title+year+author match): '{pub['title'][:60]}...'")
                else:
                    # Looks different enough — keep as separate entry
                    no_key.append(pub)

            elif title_key and title_key in by_title:
                # No DOI on either version
                existing = by_title[title_key]
                if is_likely_duplicate(existing, pub):
                    if name not in existing["authors"]:
                        existing["authors"].append(name)
                    if not existing["url"] and pub["url"]:
                        existing["url"] = pub["url"]
                    print(f"    Merged (title+year+author match): '{pub['title'][:60]}...'")
                else:
                    no_key.append(pub)

            elif title_key:
                by_title[title_key] = pub

            else:
                no_key.append(pub)

        time.sleep(1)

    all_pubs = list(by_doi.values()) + list(by_title.values()) + no_key
    print(f"\nTotal unique publications after deduplication: {len(all_pubs)}")

    # Enrich with full author lists from CrossRef
    all_pubs = enrich_with_crossref(all_pubs)

# ── Build authors summary ──────────────────────────────────────────────
    # Count publications per researcher using the orcid_ids.csv list
    researcher_index = {r["orcid"]: r for r in researchers}
    author_stats = {r["orcid"]: {"name":        r["name"],
                                  "orcid":       r["orcid"],
                                  "institution": r["institution"],
                                  "pub_count":   0,
                                  "per_count":   0}
                    for r in researchers}

    for p in all_pubs:
        for orcid_id in (p.get("orcids") or []):
            if orcid_id in author_stats:
                author_stats[orcid_id]["pub_count"] += 1
                if p.get("is_per"):
                    author_stats[orcid_id]["per_count"] += 1

    authors_output = sorted(author_stats.values(),
                            key=lambda a: a["name"].split()[-1].lower())

    with open("_data/authors.json", "w", encoding="utf-8") as f:
        json.dump(authors_output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(authors_output)} authors to _data/authors.json")

    # ── Build PER journals summary ─────────────────────────────────────────
    from collections import Counter
    journal_counts = Counter()
    for p in all_pubs:
        if p.get("is_per") and p.get("journal"):
            journal_counts[p["journal"].strip()] += 1

    journals_output = [{"journal": j, "count": c}
                       for j, c in journal_counts.most_common()]

    with open("_data/per_journals_found.json", "w", encoding="utf-8") as f:
        json.dump(journals_output, f, indent=2, ensure_ascii=False)
    print(f"Saved {len(journals_output)} PER journals to _data/per_journals_found.json")

    # ── Clean up internal fields before saving publications ────────────────
    for p in all_pubs:
        p.pop("_title_key", None)
        p.pop("authors",    None)
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
