# SEC EDGAR drone keyword search

This project uses [SEC EDGAR Full Text Search](https://www.sec.gov/edgar/search/) to build a list of issuers whose annual reports mention drones. The first search found **2,959 issuers**. After refining the keywords and checking the latest reports, the final list had **677**.

## Step 1: Broad keyword search

I searched original 10-K and 20-F reports filed from January 1, 2001 through November 10, 2026. Amendments and matches found only in exhibits did not count. I searched for **“unmanned aerial,” “autonomous,” “loitering munition,” and “drone.”** The script also searched for “loitering munitions” and “drones.” It searched multiword terms as phrases, and a report only needed to match one term.

I kept one filing per issuer using its SEC company identifier, or CIK. For each issuer, I kept the newest report **that matched the search**, using the filing date. This meant an older report could qualify even if a newer report did not match. The first search produced `original_broad_companies.csv`, with **2,959 issuers**.

## Step 2: Refined keyword and latest-report screen

I added **“UAV”** and **“UAS”** and stopped counting **“autonomous”** as a qualifying term on its own. “Autonomous” can refer to things unrelated to drones, like autonomous vehicles or decentralized autonomous organizations. I kept searching for it so I could count how many issuers were dropped because their most recent search hit matched only that word.

For the remaining issuers, I checked whether the report that matched was also the issuer’s **latest original 10-K or 20-F** as of November 10, 2026. The script used the [SEC submissions API](https://www.sec.gov/search-filings/edgar-application-programming-interfaces) to look up that latest report, even if it did not appear in the keyword search. It compared the accession number, form, and filing date. If an older report matched but the latest one did not, I dropped the issuer.

The script saved the refined list as `drone_companies.csv` and recorded the reasons for dropping issuers in `excluded_companies.csv`.

| Search result | Issuers |
|---|---:|
| Initial broad search | 2,959 |
| Expanded search, after adding UAV and UAS | 3,011 |
| Dropped because the most recent search hit matched only “autonomous” | 2,125 |
| Dropped because a newer annual report had no qualifying match | 209 |
| **Final list** | **677** |

Of the original 2,959 issuers, **654 stayed on the list** and **2,305 were dropped**. The UAV and UAS searches added **23 issuers** that were not on the original list, bringing the final total to **677**: 417 with a 10-K and 260 with a 20-F. A keyword could appear anywhere in the indexed annual report.

## Appendix: How to run the search

**Python files:** Run [scan_filings.py](./scan_filings.py) for both searches. The folder also includes [test_scan_filings.py](./test_scan_filings.py), but you do not need to run it to get the CSVs.

1. Open the project folder in VS Code inside Amazon WorkSpaces. Go to **File > Open Folder**, then **Terminal > New Terminal**.
2. Run `python3 --version` to check that you have Python 3.10 or later. You do not need Jupyter, extra packages, or an API key. On Windows, use `py` instead of `python3` if needed.
3. Replace `Your Name analyst@example.com` in both commands with your name or organization and real contact email. Run the first command for the broad search and the second for the refined search:

   ```sh
   python3 scan_filings.py --user-agent "Your Name analyst@example.com" --start 2001-01-01 --end 2026-11-10 --broad
   python3 scan_filings.py --user-agent "Your Name analyst@example.com" --start 2001-01-01 --end 2026-11-10
   ```

4. Open `drone_companies.csv` for the final list. It includes the company name, CIK, filing type and date, reporting period, matched keywords, accession number, and links to the report and filing index. In Excel, use **Data > From Text/CSV** and set the CIK column to **Text** so Excel keeps the leading zeros.

Both runs share a local cache, so the second search can reuse results from the first. The script uses SEC search results and filing records without downloading the full reports. EDGAR’s index can change, so a rerun may give different counts even with the same cutoff date.
