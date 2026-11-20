import contextlib
import datetime as dt
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import scan_filings as scan


START = dt.date(2024, 1, 1)
END = dt.date(2024, 12, 31)


def hit(accession="0000000001-24-000001", filed="2024-02-01",
        file_type="10-K", cik="0000000001"):
    return {
        "_id": accession + ":report.htm",
        "_source": {
            "adsh": accession, "file_date": filed, "file_type": file_type,
            "form": "10-K", "ciks": [cik], "display_names": ["Example Inc."],
            "period_ending": "2023-12-31",
        },
    }


def response(hits, total=None, relation="eq"):
    return {
        "timed_out": False, "_shards": {"failed": 0},
        "hits": {"hits": hits, "total": {
            "value": len(hits) if total is None else total, "relation": relation,
        }},
    }


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def search(self, *args):
        self.calls.append(args)
        return next(self.responses)

    def get_json(self, url):
        self.calls.append(url)
        return next(self.responses)


def submissions_table(forms, dates, accessions):
    return {"form": forms, "filingDate": dates, "accessionNumber": accessions}


def submissions(recent, files=None):
    return {"cik": 1, "filings": {"recent": recent, "files": files or []}}


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.db = scan.open_cache(":memory:")

    def tearDown(self):
        self.db.close()

    def test_primary_only(self):
        for kind in ("EX-99.1", "10-K/A", "20-F/A"):
            self.assertEqual(scan.parse_hit(hit(file_type=kind), START, END), [])
        self.assertEqual(len(scan.parse_hit(hit(), START, END)), 1)
        foreign = hit(file_type="20-F")
        foreign["_source"]["form"] = "20-F"
        self.assertEqual(scan.parse_hit(foreign, START, END)[0][4], "20-F")

    def test_latest_match_and_cached_resume(self):
        first = hit()
        second = hit("0000000001-24-000002", "2024-03-01")
        client = FakeClient([response([first, second])])
        rows = scan.screen(client, self.db, ["drone"], START, END)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["filing_date"], "2024-03-01")
        cached_client = FakeClient([])
        self.assertEqual(scan.screen(cached_client, self.db, ["drone"], START, END), rows)
        self.assertEqual(cached_client.calls, [])

    def test_keywords_belong_to_selected_report_only(self):
        older = hit()
        newer = hit("0000000001-24-000002", "2024-03-01")
        client = FakeClient([
            response([older]), response([newer]), response([newer])
        ])
        rows = scan.screen(client, self.db, ["drone", "autonomous", "unmanned aerial"], START, END)
        self.assertEqual(rows[0]["matched_keywords"], {"autonomous", "unmanned aerial"})

    def test_split_multiple_pages(self):
        client = FakeClient([
            response([hit()], 2),
            response([]), response([]),
        ])
        jobs = set()
        scan.collect_range(client, self.db, '"drone"', START, END, jobs)
        self.assertEqual(len(jobs), 2)
        self.assertEqual(client.calls[1][2] + dt.timedelta(days=1), client.calls[2][1])
        self.assertEqual(client.calls[2][2], END)

    def test_single_day_overflow_fails(self):
        with self.assertRaises(scan.SearchError):
            scan.collect_range(
                FakeClient([response([hit()], 2)]),
                self.db, '"drone"', START, START, set(),
            )

    def test_repeated_results_fail_without_checkpoint(self):
        client = FakeClient([response([hit(), hit()])])
        with self.assertRaises(scan.SearchError):
            scan.collect_range(client, self.db, '"drone"', START, END, set())
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0], 0)

    def test_invalid_page_counts_fail(self):
        for page in (response([], 2), response([hit()], 0)):
            with self.subTest(page=page), self.assertRaises(scan.SearchError):
                scan.collect_range(
                    FakeClient([page]),
                    self.db, '"drone"', START, END, set(),
                )

    def test_failed_or_changed_response_rejected(self):
        bad = response([])
        bad["_shards"]["failed"] = 1
        for item in ({}, bad, {"error": "backend error"}):
            with self.subTest(item=item), self.assertRaises(scan.SearchError):
                scan.validate_response(item)

    def test_ambiguous_company_mapping_fails(self):
        item = hit()
        item["_source"]["ciks"].append("0000000002")
        with self.assertRaises(scan.SearchError):
            scan.parse_hit(item, START, END)

    def test_coregistrants_preserve_name_to_cik_mapping(self):
        item = hit()
        item["_source"]["ciks"].append("0000000002")
        item["_source"]["display_names"].append("Other Inc.")
        rows = scan.parse_hit(item, START, END)
        self.assertEqual([(row[0], row[1]) for row in rows], [
            ("0000000001", "Example Inc."), ("0000000002", "Other Inc.")
        ])

    def test_cached_keywords_do_not_leak_into_other_searches(self):
        scan.screen(FakeClient([response([hit()])]), self.db, ["drone"], START, END)
        rows = scan.screen(FakeClient([response([])]), self.db, ["autonomous"], START, END)
        self.assertEqual(rows, [])

    def test_empty_csv_and_formula_safety(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "out.csv"
            scan.write_csv(path, [])
            self.assertIn("company,cik,form", path.read_text(encoding="utf-8-sig"))
            rows = scan.screen(FakeClient([response([hit()])]), self.db, ["drone"], START, END)
            rows[0]["company"] = "=HYPERLINK(\"bad\")"
            scan.write_csv(path, rows)
            self.assertIn("'=HYPERLINK", path.read_text(encoding="utf-8-sig"))
            self.assertFalse(path.with_name("out.csv.tmp").exists())

    def test_plural_queries(self):
        self.assertEqual(scan.keyword_query("drone"), '"drone" OR "drones"')
        self.assertIn('"loitering munitions"', scan.keyword_query("loitering munition"))

    def test_retry_429_then_succeed(self):
        error = urllib.error.HTTPError("url", 429, "slow down", {"Retry-After": "3"}, None)
        success = io.BytesIO(json.dumps(response([])).encode())
        with patch("urllib.request.urlopen", side_effect=[error, success]), \
                patch("time.sleep") as sleep:
            result = scan.Client("Test test@example.com").search('"drone"', START, END)
        self.assertEqual(result["hits"]["total"]["value"], 0)
        self.assertTrue(any(call.args[0] >= 3 for call in sleep.call_args_list))

    def test_403_does_not_retry(self):
        error = urllib.error.HTTPError("url", 403, "blocked", {}, None)
        with patch("urllib.request.urlopen", side_effect=error) as request, \
                patch("time.sleep"), self.assertRaises(scan.SearchError):
            scan.Client("Test test@example.com").search('"drone"', START, END)
        self.assertEqual(request.call_count, 1)

    def test_network_failure_exhausts_retries(self):
        with patch("urllib.request.urlopen", side_effect=urllib.error.URLError("offline")) as request, \
                patch("time.sleep"), self.assertRaises(scan.SearchError):
            scan.Client("Test test@example.com", retries=1).search('"drone"', START, END)
        self.assertEqual(request.call_count, 2)

    def test_cli_rejects_missing_contact(self):
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as exc:
                scan.main([])
        self.assertEqual(exc.exception.code, 2)

    def test_cli_accepts_supplied_contact(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "scan_filings.screen", return_value=[]
        ):
            result = scan.main([
                "--user-agent", "DroneFilingScreen analyst@example.com",
                "--cache", str(Path(folder) / "cache.sqlite3"),
                "--output", str(Path(folder) / "results.csv"),
                "--excluded", str(Path(folder) / "excluded.csv"),
            ])
        self.assertEqual(result, 0)

    def test_broad_cli_uses_original_terms_without_latest_report_check(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "scan_filings.screen", return_value=[]
        ) as screen, patch("scan_filings.filter_latest") as latest:
            output = Path(folder) / "broad.csv"
            excluded = Path(folder) / "excluded.csv"
            result = scan.main([
                "--user-agent", "Test analyst@example.com", "--broad",
                "--cache", str(Path(folder) / "cache.sqlite3"),
                "--output", str(output), "--excluded", str(excluded),
            ])
            self.assertEqual(result, 0)
            self.assertEqual(screen.call_args.args[2], [
                "unmanned aerial", "autonomous", "loitering munition", "drone"
            ])
            latest.assert_not_called()
            self.assertTrue(output.exists())
            self.assertFalse(excluded.exists())

    def test_failed_cli_does_not_replace_existing_csv(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "scan_filings.screen", side_effect=scan.SearchError("SEC unavailable")
        ):
            output = Path(folder) / "results.csv"
            output.write_text("existing output", encoding="utf-8")
            result = scan.main([
                "--user-agent", "Test test@example.com",
                "--cache", str(Path(folder) / "cache.sqlite3"),
                "--output", str(output),
                "--excluded", str(Path(folder) / "excluded.csv"),
            ])
            self.assertEqual(output.read_text(encoding="utf-8"), "existing output")
        self.assertEqual(result, 1)

    def test_latest_annual_ignores_amendments_and_future_dates(self):
        data = submissions(submissions_table(
            ["10-K", "10-K/A", "20-F", "10-Q"],
            ["2024-02-01", "2024-03-01", "2025-02-01", "2024-05-01"],
            ["0000000001-24-000001", "0000000001-24-000002",
             "0000000001-25-000001", "0000000001-24-000003"],
        ))
        client = FakeClient([data])
        latest = scan.latest_annual(client, self.db, "0000000001", END)
        self.assertEqual(latest["accession"], "0000000001-24-000001")
        self.assertEqual(scan.latest_annual(FakeClient([]), self.db, "0000000001", END), latest)

    def test_latest_annual_uses_archives_for_inactive_company(self):
        data = submissions(submissions_table(["8-K"], ["2024-04-01"], ["anything"]), [{
            "name": "CIK0000000001-submissions-001.json",
            "filingFrom": "2001-01-01", "filingTo": "2024-03-01",
        }])
        archived = submissions_table(
            ["20-F"], ["2024-02-01"], ["0000000001-24-000001"]
        )
        client = FakeClient([data, archived])
        self.assertEqual(scan.latest_annual(client, self.db, "0000000001", END)["form"], "20-F")
        self.assertEqual(len(client.calls), 2)

    def test_malformed_submissions_fail(self):
        for data in ({}, submissions(submissions_table(["10-K"], [], []))):
            with self.subTest(data=data), self.assertRaises(scan.SearchError):
                scan.latest_annual(FakeClient([data]), self.db, "0000000001", END)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM latest_checks").fetchone()[0], 0)

    def test_latest_report_must_match(self):
        rows = scan.screen(FakeClient([response([hit()])]), self.db, ["drone"], START, END)
        newer = submissions(submissions_table(
            ["10-K"], ["2024-03-01"], ["0000000001-24-000002"]
        ))
        included, excluded = scan.filter_latest(
            FakeClient([newer]), self.db, rows, {"drone"}, END
        )
        self.assertEqual(included, [])
        self.assertEqual(excluded[0]["latest_annual_accession"], "0000000001-24-000002")

    def test_matching_latest_report_included(self):
        rows = scan.screen(FakeClient([response([hit()])]), self.db, ["drone"], START, END)
        data = submissions(submissions_table(
            ["10-K"], ["2024-02-01"], ["0000000001-24-000001"]
        ))
        included, excluded = scan.filter_latest(FakeClient([data]), self.db, rows, {"drone"}, END)
        self.assertEqual(len(included), 1)
        self.assertEqual(excluded, [])

    def test_autonomous_only_excluded_without_metadata_request(self):
        rows = scan.screen(FakeClient([response([hit()])]), self.db, ["autonomous"], START, END)
        included, excluded = scan.filter_latest(FakeClient([]), self.db, rows, {"drone"}, END)
        self.assertEqual(included, [])
        self.assertIn("Only autonomous", excluded[0]["exclusion_reason"])

    def test_missing_annual_is_error_not_silent_exclusion(self):
        rows = scan.screen(FakeClient([response([hit()])]), self.db, ["drone"], START, END)
        with self.assertRaises(scan.SearchError):
            scan.filter_latest(
                FakeClient([submissions(submissions_table([], [], []))]),
                self.db, rows, {"drone"}, END,
            )

    def test_archive_can_contain_newer_annual_than_recent_table(self):
        data = submissions(submissions_table(
            ["10-K"], ["2024-02-01"], ["0000000001-24-000001"]
        ), [{
            "name": "CIK0000000001-submissions-001.json",
            "filingFrom": "2024-01-01", "filingTo": "2024-04-01",
        }])
        archived = submissions_table(
            ["20-F"], ["2024-03-01"], ["0000000001-24-000002"]
        )
        self.assertEqual(
            scan.latest_annual(FakeClient([data, archived]), self.db, "0000000001", END)["form"],
            "20-F",
        )

    def test_cli_rejects_autonomous_as_qualifying_term(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as exc:
            scan.main(["--user-agent", "Test test@example.com", "--keyword", "autonomous"])
        self.assertEqual(exc.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
