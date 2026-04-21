# PER-HE Publications

**Automatically updating publication list for the UK & Ireland Physics Higher Education Research community.**

Live site: [nicolaslab.github.io/per-he-publications](https://nicolaslab.github.io/per-he-publications)  
Community hub: [per-he.org](https://per-he.org)

---

## Overview

This site aggregates publications from members of the UK & Ireland Physics Education Research (PER) community. Rather than maintaining a list by hand, it uses [ORCID](https://orcid.org) profiles as the data source and updates automatically every week via GitHub Actions.

Publications are fetched from ORCID, enriched with full author lists and missing metadata from [CrossRef](https://crossref.org), deduplicated, and classified as PER or non-PER. The results are rendered as a searchable, filterable website using [Quarto](https://quarto.org) and hosted for free on GitHub Pages.

---

## How it works

```
orcid_ids.csv
      │
      ▼
fetch_publications.py  ←── per_journals.txt
      │                ←── per_keywords.txt
      │  queries ORCID public API (one author at a time)
      │  enriches metadata via CrossRef API
      │  deduplicates by DOI and normalised title
      │  classifies papers as PER or non-PER
      │
      ▼
_data/publications.json
_data/authors.json
_data/per_journals_found.json
      │
      ▼
Quarto website (index.qmd, publications.qmd, authors.qmd, journals.qmd, about.qmd)
      │
      ▼
GitHub Pages → nicolaslab.github.io/per-he-publications
```

The entire pipeline runs weekly (every Monday at 6am UTC) via a GitHub Actions workflow, and can also be triggered manually from the Actions tab.

---

## Repository structure

```
per-he-publications/
│
├── orcid_ids.csv               # List of community members (name, ORCID, institution)
├── per_journals.txt            # Known PER journals (one per line, any capitalisation)
├── per_keywords.txt            # PER title keywords (one per line, any capitalisation)
│
├── fetch_publications.py       # Python script: fetches, deduplicates, classifies, saves
│
├── index.qmd                   # Website homepage
├── publications.qmd            # Searchable publication table
├── authors.qmd                 # Author list with publication counts
├── journals.qmd                # PER journals found in the data
├── about.qmd                   # How the site works, limitations, acknowledgements
│
├── _quarto.yml                 # Quarto site configuration (title, navbar, theme)
├── styles.css                  # Custom CSS
│
├── _data/                      # Generated data files (committed by the workflow)
│   ├── publications.json
│   ├── authors.json
│   └── per_journals_found.json
│
└── .github/
    └── workflows/
        └── update-publications.yml   # GitHub Actions workflow
```

---

## Adding a community member

1. Open `orcid_ids.csv` in the GitHub editor
2. Add a new line in the format:
   ```
   Full Name,0000-0000-0000-0000,Institution name
   ```
3. Commit the change
4. Go to **Actions → Update Publications → Run workflow** to rebuild immediately, or wait for the next Monday run

The person's publications will be fetched from their ORCID profile. Any papers they share with existing community members will be automatically deduplicated.

---

## Updating the PER classification lists

To add a new journal or keyword, simply edit the relevant plain-text file directly on GitHub — no Python knowledge needed.

**`per_journals.txt`** — one journal name per line. Any capitalisation works:
```
Physical Review Physics Education Research
European Journal of Physics
```

**`per_keywords.txt`** — one keyword or phrase per line. Matched against paper titles:
```
active learning
threshold concept
```

A paper is classified as PER if either its journal appears in `per_journals.txt` **or** its title contains a phrase from `per_keywords.txt`. Missing journal names are filled in from CrossRef and re-evaluated automatically.

---

## How deduplication works

The same paper can appear on multiple ORCID profiles (co-authors) or even twice on the same profile (e.g. as both a preprint and a published version). The script deduplicates using a two-stage approach:

1. **DOI matching** — if two entries share the same normalised DOI, they are the same paper. Author credits are merged.
2. **Title matching** — if two entries share the same normalised title (lowercased, punctuation removed) *and* pass a confirmation check (publication years within 1 year of each other, at least one shared author family name), they are treated as the same paper.

When a match is confirmed, the workflow log prints a line such as:
```
Merged (title+year+author match): 'Characterizing interactive engagement...'
```
You can review these in **Actions → [latest run] → update-and-deploy → Fetch publications from ORCID**.

---

## Data sources

| Source | What it provides |
|---|---|
| [ORCID public API](https://pub.orcid.org/v3.0) | Title, year, DOI, type, journal (where stored), URL |
| [CrossRef API](https://api.crossref.org) | Full author list, missing journal names |

Neither API requires authentication or an API key. Both are queried with a small delay between requests to respect rate limits.

---

## Technical requirements

The site runs entirely on free infrastructure:

- **GitHub Actions** — runs the weekly workflow (free for public repositories)
- **GitHub Pages** — hosts the static website (free for public repositories)
- **Python 3.12** — only the `requests` library is needed (installed by the workflow)
- **Quarto** — installed by the workflow via `quarto-dev/quarto-actions`

The website pages use **Observable JS**, which runs in the browser and requires no server-side processing. The Quarto build step converts the `.qmd` files to static HTML.

---

## Limitations

- Only works added to an author's ORCID profile will appear. Authors should keep their ORCID profiles up to date.
- Metadata quality depends on what is stored in ORCID and CrossRef. Some older papers may have incomplete information.
- There is up to a 7-day delay between an ORCID update and this site reflecting it.
- The PER classification is automatic and imperfect. Papers can be manually reclassified by editing `per_journals.txt` or `per_keywords.txt`, or by raising an issue on GitHub.

---

## Acknowledgements

This site was inspired by the [MVLS Learning, Teaching and Scholarship Network publications page](https://mvls-lts.github.io/outputs/), which demonstrated this approach for a different research community. We are grateful to its authors for making their [source code](https://github.com/mvls-lts/outputs) openly available.

---

## Licence

MIT — free to fork and adapt for other research communities.
