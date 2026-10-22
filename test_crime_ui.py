import importlib.util
import sqlite3
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("crime_ui", Path(__file__).with_name("crime_ui.py"))
crime_ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(crime_ui)


class SourceFreshnessTests(unittest.TestCase):
    def setUp(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE calls (response_at TEXT, source TEXT)")
        self.connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")

    def tearDown(self):
        self.connection.close()

    def test_shows_data_coverage_and_latest_published_record(self):
        self.connection.executemany(
            "INSERT INTO calls VALUES (?, 'city_cfs')",
            [("2026-07-01 00:00:00",), ("2026-07-31 23:59:25",)],
        )
        self.connection.execute(
            "INSERT INTO metadata VALUES ('source_latest_response_at', '2026-08-01 02:00:00')"
        )
        self.connection.execute(
            "INSERT INTO metadata VALUES ('collector_last_success', '2026-08-01T03:00:00+00:00')"
        )

        source_line, collector_line = crime_ui.source_freshness(self.connection)

        self.assertIn("SAPD DATA COVERAGE: 2026-07-01 00:00:00 through 2026-07-31 23:59:25", source_line)
        self.assertIn("LATEST PUBLISHED RECORD: 2026-08-01 02:00:00", source_line)
        self.assertNotIn("STALE", source_line)
        self.assertIn("CITY COLLECTOR LAST SUCCESS", collector_line)

    def test_shows_collector_failure_as_a_warning(self):
        self.connection.execute(
            "INSERT INTO metadata VALUES ('collector_last_failure', '2026-08-01T04:00:00+00:00: API unavailable')"
        )

        _source_line, collector_line = crime_ui.source_freshness(self.connection)

        self.assertEqual(
            collector_line,
            "COLLECTOR WARNING: 2026-08-01T04:00:00+00:00: API unavailable",
        )


if __name__ == "__main__":
    unittest.main()
