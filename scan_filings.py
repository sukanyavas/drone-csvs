#!/usr/bin/env python3
"""Screen SEC annual reports using search metadata, without downloading reports."""

import argparse
import csv
import datetime as dt
import email.utils
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


ENDPOINT = "https://efts.sec.gov/LATEST/search-index"
FORMS = {"10-K", "20-F"}
DEFAULT_KEYWORDS = [
    "unmanned aerial", "loitering munition", "drone", "UAV", "UAS"
]
BROAD_KEYWORDS = ["unmanned aerial", "autonomous", "loitering munition", "drone"]
ALIASES = {
    "drone": ["drone", "drones"],
    "loitering munition": ["loitering munition", "loitering munitions"],
}
LOG = logging.getLogger("sec-screen")


class SearchError(Exception):
    """An incomplete search must not be reported as a successful screen."""


def keyword_query(keyword):
    phrases = ALIASES.get(keyword.lower(), [keyword])
    return " OR ".join(f'"{phrase}"' for phrase in phrases)


def validate_response(data):
    try:
        hits = data["hits"]["hits"]
        total = data["hits"]["total"]
        value = total["value"]
        relation = total["relation"]
        failed = data["_shards"]["failed"]
    except (KeyError, TypeError) as exc:
        raise SearchError("SEC search response format changed.") from exc
    if data.get("timed_out") is not False or failed != 0:
        raise SearchError("SEC returned a timed-out or partially failed search.")
    if (not isinstance(hits, list) or type(value) is not int or value < 0
            or relation not in {"eq", "gte"}):
        raise SearchError("SEC returned invalid search results.")
    return hits, value, relation


class Client:
    def __init__(self, user_agent, interval=0.6, retries=5):
        self.user_agent = user_agent
        self.interval = interval
        self.retries = retries
        self.last_request = 0.0
        self.requests = 0

    def search(self, query, start, end, cik=None):
        params = {
            "q": query, "forms": "10-K,20-F", "dateRange": "custom",
            "startdt": start.isoformat(), "enddt": end.isoformat(),
            "from": 0,
        }
        if cik is not None:
            params["ciks"] = cik
        url = ENDPOINT + "?" + urllib.parse.urlencode(params)
        return self.get_json(url, validate_response)

    def get_json(self, url, validator=None, decoder=json.load):
        for attempt in range(self.retries + 1):
            time.sleep(max(0, self.interval - (time.monotonic() - self.last_request)))
            request = urllib.request.Request(
                url, headers={"User-Agent": self.user_agent, "Accept": "application/json"}
            )
            retry_after = 0
            try:
                self.last_request = time.monotonic()
                self.requests += 1
                with urllib.request.urlopen(request, timeout=60) as response:
                    data = decoder(response)
                if validator is not None:
                    validator(data)
                return data
            except urllib.error.HTTPError as exc:
                exc.close()
                if exc.code not in {408, 429, 500, 502, 503, 504}:
                    raise SearchError(
                        f"SEC HTTP {exc.code}. For 403, verify your contact "
                        "User-Agent and wait before trying again; do not bypass blocks."
                    ) from exc
                header = exc.headers.get("Retry-After", "")
                try:
                    retry_after = max(0, float(header))
                except ValueError:
                    if header:
                        try:
                            retry_after = max(
                                0, email.utils.parsedate_to_datetime(header).timestamp()
                                - time.time()
                            )
                        except (TypeError, ValueError, OverflowError):
                            LOG.warning("SEC returned an invalid Retry-After header.")
                problem = f"HTTP {exc.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError,
                    json.JSONDecodeError, UnicodeDecodeError, SearchError) as exc:
                problem = str(exc)
            if attempt == self.retries:
                raise SearchError(
                    f"Search failed after {self.retries + 1} attempts: {problem}"
                )
            delay = max(retry_after, min(60, 2 ** (attempt + 1)))
            LOG.warning("%s; retrying in %.1f seconds.", problem, delay)
            time.sleep(delay)
        raise AssertionError("unreachable")


def open_cache(path):
    db = sqlite3.connect(path)
    db.execute("PRAGMA foreign_keys = ON")
    db.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY,
            query TEXT NOT NULL,
            start TEXT NOT NULL,
            end TEXT NOT NULL,
            UNIQUE(query, start, end)
        );
        CREATE TABLE IF NOT EXISTS matches (
            job_id INTEGER NOT NULL REFERENCES jobs(id),
            cik TEXT NOT NULL,
            company TEXT NOT NULL,
            accession TEXT NOT NULL,
            document TEXT NOT NULL,
            form TEXT NOT NULL,
            filed TEXT NOT NULL,
            period TEXT NOT NULL,
            PRIMARY KEY(job_id, cik, accession, document)
        );
        CREATE TABLE IF NOT EXISTS latest_checks (
            cik TEXT NOT NULL,
            as_of TEXT NOT NULL,
            result TEXT NOT NULL,
            PRIMARY KEY(cik, as_of)
        );
    """)
    return db


def parse_hit(hit, start, end):
    try:
        source = hit["_source"]
        if source["file_type"] not in FORMS or source["form"] not in FORMS:
            return []
        accession = source["adsh"]
        hit_accession, document = hit["_id"].split(":", 1)
        filed = dt.date.fromisoformat(source["file_date"])
        ciks = source["ciks"]
        names = source["display_names"]
        period = source.get("period_ending") or ""
        if (hit_accession != accession
                or not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
                or not document or "/" in document or "\\" in document
                or not start <= filed <= end
                or not isinstance(ciks, list) or not ciks
                or not isinstance(names, list) or len(ciks) != len(names)):
            raise ValueError("Invalid filing identifiers, dates, or company mapping")
        if period:
            dt.date.fromisoformat(period)
        rows = []
        for cik, name in zip(ciks, names):
            if (not isinstance(cik, str) or not re.fullmatch(r"\d{1,10}", cik)
                    or not isinstance(name, str) or not name.strip()):
                raise ValueError("Invalid company identity")
            rows.append((
                cik.zfill(10), name, accession, document, source["form"],
                filed.isoformat(), period,
            ))
        return rows
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise SearchError(f"Cannot safely interpret SEC result: {hit.get('_id')!r}") from exc


def collect_range(client, db, query, start, end, seen_jobs):
    key = (query, start.isoformat(), end.isoformat())
    cached = db.execute(
        "SELECT id FROM jobs WHERE query=? AND start=? AND end=?", key
    ).fetchone()
    if cached:
        seen_jobs.add(cached[0])
        return
    data = client.search(query, start, end)
    hits, total, relation = validate_response(data)
    if not hits and total:
        raise SearchError("SEC returned an empty page for a nonempty result set.")
    # Relevance ties can move between pages, even for historical date ranges.
    # Partition by date instead, so every accepted range fits on one page.
    if relation != "eq" or total > len(hits):
        if start == end:
            raise SearchError(
                f"More than one page of results for {query} on {start}; "
                "cannot guarantee completeness without stable SEC pagination."
            )
        middle = start + (end - start) // 2
        LOG.info("Splitting large result range: %s to %s (%s).", start, end, query)
        collect_range(client, db, query, start, middle, seen_jobs)
        collect_range(client, db, query, middle + dt.timedelta(days=1), end, seen_jobs)
        return
    LOG.info("%s to %s: %s search-document hits for %s", start, end, total, query)
    rows = []
    seen_hits = set()
    if len(hits) != total:
        raise SearchError("SEC result count does not match the returned documents.")
    for hit in hits:
        if not isinstance(hit, dict) or not isinstance(hit.get("_id"), str):
            raise SearchError("SEC returned a result without a document identifier.")
        if hit["_id"] in seen_hits:
            raise SearchError("SEC returned a duplicate document in a result page.")
        seen_hits.add(hit["_id"])
        rows.extend(parse_hit(hit, start, end))
    with db:
        job_id = db.execute(
            "INSERT INTO jobs(query,start,end) VALUES(?,?,?)", key
        ).lastrowid
        db.executemany(
            "INSERT OR IGNORE INTO matches VALUES(?,?,?,?,?,?,?,?)",
            [(job_id, *row) for row in rows],
        )
    seen_jobs.add(job_id)


def screen(client, db, keywords, start, end):
    selected = {}
    for keyword in keywords:
        jobs = set()
        # Year-sized initial ranges keep the search backend's result window manageable.
        for year in range(end.year, start.year - 1, -1):
            lower = max(start, dt.date(year, 1, 1))
            upper = min(end, dt.date(year, 12, 31))
            collect_range(client, db, keyword_query(keyword), lower, upper, jobs)
        for job in sorted(jobs):
            for row in db.execute(
                "SELECT cik,company,accession,document,form,filed,period "
                "FROM matches WHERE job_id=?", (job,)
            ):
                cik, name, accession, document, form, filed, period = row
                rank = (filed, accession)
                current = selected.get(cik)
                if current is None or rank > current["rank"]:
                    archive = f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"
                    selected[cik] = {
                        "rank": rank, "cik": cik, "company": name, "form": form,
                        "filing_date": filed, "report_period": period,
                        "accession": accession, "matched_keywords": {keyword},
                        "report_url": archive + "/" + urllib.parse.quote(document, safe=""),
                        "filing_index_url": archive + "/" + accession + "-index.html",
                    }
                elif rank == current["rank"]:
                    current["matched_keywords"].add(keyword)
    return sorted(selected.values(), key=lambda row: row["company"].casefold())


def annual_filings(table, as_of):
    try:
        fields = ("form", "filingDate", "accessionNumber")
        columns = [table[key] for key in fields]
        if not all(isinstance(column, list) for column in columns):
            raise ValueError("Submission columns must be lists")
        if len({len(column) for column in columns}) != 1:
            raise ValueError("Submission column lengths differ")
        results = []
        for form, filed, accession in zip(*columns):
            date = dt.date.fromisoformat(filed)
            if form in FORMS and date <= as_of:
                if not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession):
                    raise ValueError("Invalid submission accession")
                results.append({"form": form, "filed": filed, "accession": accession})
        return results
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchError("Invalid SEC submissions metadata.") from exc


def latest_annual(client, db, cik, as_of):
    cached = db.execute(
        "SELECT result FROM latest_checks WHERE cik=? AND as_of=?",
        (cik, as_of.isoformat()),
    ).fetchone()
    if cached:
        return json.loads(cached[0])
    data = client.get_json(f"https://data.sec.gov/submissions/CIK{cik}.json")
    try:
        if str(data["cik"]).zfill(10) != cik:
            raise ValueError("SEC returned metadata for a different CIK")
        filings = data["filings"]
        results = annual_filings(filings["recent"], as_of)
        files = filings["files"]
        if not isinstance(files, list):
            raise ValueError("Invalid archived submission list")
        archives = []
        for archive in files:
            first = dt.date.fromisoformat(archive["filingFrom"])
            last = dt.date.fromisoformat(archive["filingTo"])
            name = archive["name"]
            if (first > last or not re.fullmatch(
                    rf"CIK{cik}-submissions-\d+\.json", name)):
                raise ValueError("Invalid archived submission reference")
            archives.append((first, last, name))
        for first, last, name in sorted(archives, key=lambda item: item[1], reverse=True):
            best_date = max((item["filed"] for item in results), default="2001-01-01")
            if first > as_of or last.isoformat() < best_date:
                continue
            archived = client.get_json("https://data.sec.gov/submissions/" + name)
            results.extend(annual_filings(archived, as_of))
    except (KeyError, TypeError, ValueError) as exc:
        raise SearchError(f"Cannot interpret submissions for CIK {cik}.") from exc
    latest = max(results, key=lambda item: (item["filed"], item["accession"]), default=None)
    with db:
        db.execute(
            "INSERT INTO latest_checks VALUES(?,?,?)",
            (cik, as_of.isoformat(), json.dumps(latest)),
        )
    return latest


def filter_latest(client, db, rows, qualifying_keywords, as_of):
    included, excluded = [], []
    for index, row in enumerate(rows, 1):
        row = dict(row)
        if not row["matched_keywords"].intersection(qualifying_keywords):
            row["exclusion_reason"] = "Only autonomous matched; no drone-specific term"
            row["latest_annual_accession"] = ""
            row["latest_annual_filing_date"] = ""
            excluded.append(row)
            continue
        LOG.info("Checking latest annual report: %s/%s %s", index, len(rows), row["company"])
        latest = latest_annual(client, db, row["cik"], as_of)
        if latest is None:
            raise SearchError(
                f"No annual report in SEC submissions for matching CIK {row['cik']}; "
                "cannot verify the search result."
            )
        if (latest["accession"] == row["accession"]
                and latest["form"] == row["form"]
                and latest["filed"] == row["filing_date"]):
            row["matched_keywords"] = row["matched_keywords"].intersection(qualifying_keywords)
            included.append(row)
        else:
            if (latest["filed"], latest["accession"]) < row["rank"]:
                raise SearchError(
                    f"Search and submissions disagree about newest report for CIK {row['cik']}."
                )
            row["exclusion_reason"] = "A newer annual report does not match the qualifying search"
            row["latest_annual_accession"] = latest["accession"]
            row["latest_annual_filing_date"] = latest["filed"]
            excluded.append(row)
    return included, excluded


COLUMNS = [
    "company", "cik", "form", "filing_date", "report_period",
    "matched_keywords", "accession", "report_url", "filing_index_url",
]


EXCLUDED_COLUMNS = COLUMNS + [
    "exclusion_reason", "latest_annual_accession", "latest_annual_filing_date",
]


def write_csv(path, rows, columns=COLUMNS):
    temporary = path.with_name(path.name + ".tmp")
    try:
        with temporary.open("w", newline="", encoding="utf-8-sig") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                output = dict(row)
                if not isinstance(output["matched_keywords"], str):
                    output["matched_keywords"] = "; ".join(sorted(row["matched_keywords"]))
                # SEC-supplied names are untrusted input when opened in spreadsheets.
                for column in columns:
                    value = str(output[column])
                    if value.lstrip().startswith(("=", "+", "-", "@")):
                        output[column] = "'" + value
                writer.writerow(output)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def date_argument(value):
    try:
        return dt.date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Use a date like 2026-01-01.") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--user-agent", default=os.environ.get("SEC_USER_AGENT"),
        help='Contact identity, e.g. "Your Name you@example.com", or set SEC_USER_AGENT.',
    )
    parser.add_argument("--start", type=date_argument, default=dt.date(2001, 1, 1))
    parser.add_argument("--end", type=date_argument, default=dt.date.today())
    parser.add_argument(
        "--keyword", action="append",
        help="Repeat to replace the five default qualifying keywords. Simple words/phrases only.",
    )
    parser.add_argument(
        "--broad", action="store_true",
        help="Reproduce the initial screen: four original terms and latest matching report.",
    )
    parser.add_argument("--output", type=Path)
    parser.add_argument("--excluded", type=Path, default=Path("excluded_companies.csv"))
    parser.add_argument("--cache", type=Path, default=Path("sec_search_cache.sqlite3"))
    args = parser.parse_args(argv)
    if args.broad and args.keyword:
        parser.error("--broad uses the four original terms; do not combine it with --keyword.")
    if args.output is None:
        args.output = Path("original_broad_companies.csv" if args.broad else "drone_companies.csv")
    if (not args.user_agent
            or not re.search(r"[^\s@]+@[^\s@]+\.[^\s@]+", args.user_agent)
            or "\n" in args.user_agent or "\r" in args.user_agent):
        parser.error("--user-agent must contain your real contact email.")
    if not dt.date(2001, 1, 1) <= args.start <= args.end <= dt.date.today():
        parser.error("Dates must be ordered, start in 2001 or later, and not be future dates.")
    keywords = list(dict.fromkeys(k.strip() for k in (args.keyword or DEFAULT_KEYWORDS)))
    if any(not k or any(c in k for c in '"*\\\r\n') for k in keywords):
        parser.error("Keywords must be nonempty words/phrases without quotes or wildcards.")
    if any(k.lower() == "autonomous" for k in keywords):
        parser.error("'autonomous' alone is no longer a qualifying keyword.")
    if len({p.resolve() for p in (args.output, args.excluded, args.cache)}) != 3:
        parser.error("--output, --excluded and --cache must be different files.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    db = None
    try:
        db = open_cache(args.cache)
        client = Client(args.user_agent)
        if args.broad:
            rows = screen(client, db, BROAD_KEYWORDS, args.start, args.end)
            write_csv(args.output, rows)
            LOG.info("COMPLETE: %s broad-screen companies saved to %s (%s network requests).",
                     len(rows), args.output.resolve(), client.requests)
            return 0
        audit_keywords = keywords if args.keyword else keywords + ["autonomous"]
        rows = screen(client, db, audit_keywords, args.start, args.end)
        rows, excluded = filter_latest(client, db, rows, set(keywords), args.end)
        write_csv(args.excluded, excluded, EXCLUDED_COLUMNS)
        write_csv(args.output, rows)
        LOG.info("COMPLETE: %s companies saved to %s; %s excluded saved to %s "
                 "(%s network requests).",
                 len(rows), args.output.resolve(), len(excluded),
                 args.excluded.resolve(), client.requests)
        return 0
    except KeyboardInterrupt:
        LOG.error("Interrupted. Completed ranges are cached; rerun the same command to resume.")
        return 130
    except (SearchError, sqlite3.Error, OSError) as exc:
        LOG.error("INCOMPLETE: %s", exc)
        LOG.error("The run did not complete. Do not use the output pair as a new result; "
                  "existing files may be from an earlier run. "
                  "Completed checks remain cached; rerun to resume.")
        return 1
    finally:
        if db is not None:
            db.close()


if __name__ == "__main__":
    sys.exit(main())
