# Fall 2026 Transition Guide

**Sukanya Vasudevan**

## Methodology memo: SEC EDGAR drone-company keyword screen

### Project goal

The larger project examines how the ownership of companies has changed from
their initial public offering (IPO) to the present, with a focus on investors
who file beneficial ownership reports on Schedule 13D or 13G and institutional
investment reports on Form 13F. We chose the drone sector because it is a
relatively new, innovation-intensive industry.

The first step was to build a repeatable sample of public companies whose
latest original SEC annual report (10-K or 20-F) matched drone-related search
terms. This broad keyword sample was a starting point for the larger project;
a keyword match by itself does not establish that an issuer manufactures
drones. We later used the sample to identify 81 companies with drone-related
products or technology and marked which companies were “pure play,” meaning
that a majority of their revenue came from drone-related business activities.
We checked this 81-company list against the Department of Defense “Blue List”
and the VettaFi Drone Index.

The Excel workbook containing the Blue List, VettaFi Drone Index, keyword
sample, and 81-company list is located at:

`K:\_DERACollaboration\Alexc\_Vesudevan\Scraping\Keyword_search_drone_companies.xlsx`

The archived CSV files from the keyword screen are located in the `CSV Sample
Files` folder:

- Refined list: `K:\_DERACollaboration\Alexc\_Vesudevan\Scraping\CSV Sample Files\SEC_drone_companies.csv`
- Excluded candidates: `K:\_DERACollaboration\Alexc\_Vesudevan\Scraping\CSV Sample Files\SEC_excluded_companies.csv`

The keyword search covered filings dated **January 1, 2001 through November 10,
2026**, inclusive. The initial search returned **2,959 issuers**; the refined
search returned **677**.
I chose January 1, 2001 as the start date because the earliest 10-K keyword
hit for “drone” identified during this project was from 2001.

### Data sources and what counts as a match

I used [SEC EDGAR Full Text Search](https://www.sec.gov/edgar/search/) to find
annual reports returned for the search terms. In this memo, a “keyword match”
means that EDGAR returned the annual report itself for one of those terms. If
EDGAR did not return the report, it was a nonmatch for this screen. The script
does not download the full reports to search their contents separately.

I used the [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces)
to check each candidate’s latest original 10-K or 20-F, including cases where
that latest report was not returned by the keyword search. Amendments and other
form types do not qualify. A keyword match found only in an exhibit does not
qualify.

Each row represents one issuer. The SEC Central Index Key (CIK) identifies the
issuer, rather than its name or ticker. Filing date is used for dates and
recency, not fiscal-year-end date. An accession number is the SEC identifier
for an individual filing; it is used to verify that the keyword-matched report
is the same filing as the latest annual report.

## How the script works

### Step 1: Broad keyword search

The broad search looks for **“unmanned aerial,” “autonomous,” “loitering
munition,”** or **“drone.”** The script also searches for **“loitering
munitions”** and **“drones.”** Multiword terms are sent as quoted phrases. A
report only needs to match one term.

For each CIK, the script keeps the newest original 10-K or 20-F that matched
the search in the date range. This means an older report can qualify even when
the issuer filed a newer report that did not match. The broad search produced
`original_broad_companies.csv`, with **2,959 issuers**. Many results were not
relevant to the refined sample, so I applied the additional screen below.

### Step 2: Refined keyword and latest-report screen

1. I added **“UAV”** and **“UAS”** because these abbreviations also appear in
   annual reports. The qualifying terms became **“unmanned aerial,” “loitering
   munition,” “loitering munitions,” “drone,” “drones,” “UAV,”** and **“UAS.”**
2. I stopped treating **“autonomous”** as a qualifying term on its own because
   it can refer to unrelated topics, such as autonomous vehicles or
   decentralized autonomous organizations. I kept it in the search as an
   audit term, so companies whose latest search match contained only
   “autonomous” could be identified and removed.
3. For the other candidates, the script uses the SEC submissions API to find
   the issuer’s latest original 10-K or 20-F filed on or before the cutoff.
   The latest filing must be the same accession number, form, and filing date
   as the report returned by the keyword search. **I omitted issuers whose
   older annual reports matched the keywords but whose latest annual report
   did not.**
4. The script saves one row per retained CIK in `SEC_drone_companies.csv`. It
   saves omitted candidates in `SEC_excluded_companies.csv`, with the reason
   for exclusion and newer report details when available.

### Historical sample reconciliation

| Search result | Issuers |
|---|---:|
| Initial broad search | 2,959 |
| Expanded search, including UAV and UAS | 3,011 |
| Removed: latest search match contained only “autonomous” | 2,125 |
| Removed: older annual report matched, but latest annual report did not | 209 |
| **Final refined sample** | **677** |

The 677 issuers include **654** retained from the original list and **23** new
issuers found by adding UAV and UAS. In total, **2,305** issuers from the
original 2,959 were removed. The final sample has **417 10-Ks** and **260
20-Fs**. A keyword may appear anywhere in the indexed annual report.

## Appendix: How to run the searches in Windows WorkSpaces

### Python files

Run `scan_broad.py` for the broad search and `scan_refined.py` for the refined
search. Both launchers use the shared `scan_filings.py` code. The optional
`test_scan_filings.py` file tests the scripts; it does not create the CSVs.

### Set up the folder

1. Download the project files from:

   `K:\_DERACollaboration\Alexc\_Vesudevan\Scraping\Python Scripts`

   If downloading a ZIP, extract it in Amazon WorkSpaces. Make sure the
   extracted folder is named **`Python Scripts`**.
2. Keep `scan_broad.py`, `scan_refined.py`, and `scan_filings.py` together
   directly inside `Python Scripts`. Do not move one script out by itself.
   Keep the README, tests, and `.gitignore` in the same folder as well.
3. In VS Code, choose **File > Open Folder** and open the `Python Scripts`
   folder containing the files above. Then choose **Terminal > New Terminal**.
   The terminal needs to run from this folder. In the terminal, run:

   ```powershell
   dir
   ```

   Confirm that `scan_broad.py` appears in the listing.

### Check Python and run the searches

1. Check that Python 3.10 or later is installed:

   ```powershell
   py -3 --version
   ```

2. Replace `Your Name your.SEC.email@sec.gov` with your name and your assigned
   SEC email address. Use your SEC email in the User-Agent; do not use the
   example address as written. Run the broad search first, then the refined
   search:

   ```powershell
   py -3 scan_broad.py --user-agent "Your Name your.SEC.email@sec.gov" --start 2001-01-01 --end 2026-11-10
   py -3 scan_refined.py --user-agent "Your Name your.SEC.email@sec.gov" --start 2001-01-01 --end 2026-11-10
   ```

   You can change the start and end dates if needed. January 1, 2001 was
   selected as the beginning of the search period based on the early EDGAR
   full-text results available for this project.
3. The CSV files are saved in the current `Python Scripts` folder:
   - `original_broad_companies.csv` — results of the broad search.
   - `SEC_drone_companies.csv` — refined company list.
   - `SEC_excluded_companies.csv` — candidates omitted during refinement.
4. To open `SEC_drone_companies.csv` in Excel, use **Data > From Text/CSV**.
   Set the CIK column to **Text** so Excel preserves its leading zeros.

Both searches share a local `sec_search_cache.sqlite3` cache, so the refined
search can reuse results from the broad search. The script spaces out SEC
requests and retrieves search and filing metadata; it does not download the
full annual reports. EDGAR’s live index can change, so rerunning the searches
may produce different counts.

### Test the scripts

In the VS Code terminal, from the `Python Scripts` folder, run:

```powershell
py -3 -m unittest -q test_scan_filings.py
```

The tests use sample data and mocked SEC responses. They do not contact the SEC
or download filings. A successful run ends with `Ran 30 tests` and `OK`.

### Files for GitHub

Upload `README.md`, `scan_broad.py`, `scan_refined.py`, `scan_filings.py`,
`test_scan_filings.py`, and `.gitignore`. Do not upload the local cache or
generated CSV files. Each intern should use their own assigned SEC email when
running the searches.
