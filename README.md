# Methodology memo: SEC EDGAR drone-company keyword screen

**Purpose.** Build a reproducible sample of issuers whose latest original SEC annual report contains drone-related terminology. The historical screen covered filings dated **January 1, 2001 through November 10, 2026**, inclusive. The initial search returned **2,959 unique issuers**; the refined search returned **677**.

## Data sources and unit of observation

The script uses [SEC EDGAR Full Text Search](https://www.sec.gov/edgar/search/) to find keyword matches and the [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) to identify each candidate's latest annual report independently of the search results. The screen covers original **10-K** and **20-F** report documents, not amendments, other forms, or matches found only in attached exhibits. It uses the SEC's Central Index Key (**CIK**) to keep one row per issuer, rather than deduplicating by company name or ticker. Filing dates, not fiscal-year-end dates, determine eligibility and recency; accession number breaks same-date ties.

## Step 1: Broad keyword search

Search for **`unmanned aerial`**, **`autonomous`**, **`loitering munition`**, or **`drone`**. The script explicitly includes **`loitering munitions`** and **`drones`** as plural variants. Multiword terms are submitted as quoted phrases. A match for any one term is sufficient.

For each CIK, retain the newest **matching** original 10-K or 20-F in the date window. At this stage, an older report can qualify even if the issuer has filed a newer report that does not match. This produces `original_broad_companies.csv` (**2,959 issuers** in the historical run).

## Step 2: Refined keyword and latest-report screen

1. Use **`unmanned aerial`**, **`loitering munition` / `loitering munitions`**, **`drone` / `drones`**, **`UAV`**, or **`UAS`** as qualifying terms. `Autonomous` no longer qualifies on its own because it can refer to unrelated topics, such as autonomous vehicles or decentralized autonomous organizations. It is still searched for the exclusion audit.
2. Select the newest indexed report per CIK among the qualifying and audit-term hits. If that report matches only `autonomous`, omit it from the refined list. The exclusion describes the selected report, not every report the issuer has ever filed.
3. For the other candidates, use the SEC submissions API to find their latest original 10-K or 20-F filed on or before the cutoff, whether or not that filing appeared in the keyword search. Include the candidate only if the matching report's **accession, form, and filing date** agree with that latest report. This removes historical hits superseded by newer reports with no qualifying indexed hit.
4. Export one row per retained CIK to `drone_companies.csv`. Export omitted candidates to `excluded_companies.csv`, with reasons and the newer filing's accession and date where checked.

### Historical sample reconciliation

| Measure | Issuers |
|---|---:|
| Initial broad search | 2,959 |
| Candidates in expanded search, including `UAV` and `UAS` and the audit term | 3,011 |
| Omitted: selected report matched only `autonomous` | 2,125 |
| Omitted: newer annual report had no qualifying indexed match | 209 |
| **Retained in refined sample** | **677** |

The 677 issuers comprise **654 retained from the original list** and **23 new retained issuers** found through `UAV` or `UAS`. In other words, **2,305** issuers from the original 2,959 dropped out. The final sample contains **417 10-Ks** and **260 20-Fs**. These are keyword-screen results; a qualifying term can appear anywhere in the indexed annual report.

## How to run

1. Open this folder in VS Code via **File > Open Folder**. Open **Terminal > New Terminal**.
2. Run `python3 --version` to confirm **Python 3.10 or later** is installed. No Jupyter notebook, third-party packages, API key, or AI service is needed. On Windows, use `py` in place of `python3` if needed.
3. Replace `Your Name analyst@example.com` in **both** commands with your name or organization and real contact email for the SEC's User-Agent. Run the broad search first, then the refined search:

   ```sh
   python3 scan_filings.py --user-agent "Your Name analyst@example.com" --start 2001-01-01 --end 2026-11-10 --broad
   python3 scan_filings.py --user-agent "Your Name analyst@example.com" --start 2001-01-01 --end 2026-11-10
   ```

4. Open `drone_companies.csv` for the refined list. Its columns are company name, CIK, form, filing date, reporting period, matched keywords, accession number, primary report URL, and filing-index URL. Import CIK as text in spreadsheet software to preserve leading zeroes.

Both runs share the local `sec_search_cache.sqlite3` cache. The script makes paced SEC requests and retrieves search and filing metadata without downloading report bodies. The counts above are from the historical run; a new run uses the live index and can differ even with the same cutoff.

## Files to upload to GitHub

Upload the contents of this folder: [README.md](./README.md), [scan_filings.py](./scan_filings.py) (the only script needed to generate the lists), [test_scan_filings.py](./test_scan_filings.py) (optional test suite), and [.gitignore](./.gitignore). No credentials, cache, or generated CSVs are required in the repository.

To run the optional tests:

```sh
python3 -m unittest -q test_scan_filings.py
```
