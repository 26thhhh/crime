import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("collect", Path(__file__).with_name("collect.py"))
collect = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(collect)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.data_directory = Path(self.temporary_directory.name)
        self.original_data_directory = collect.DATA_DIR
        self.original_database_path = collect.DATABASE_PATH
        self.original_ensure_location = collect.ensure_location
        self.original_source_latest = collect.source_latest
        self.original_request_records = collect.request_records
        self.original_zip_centroid = collect.zip_centroid
        collect.DATA_DIR = self.data_directory
        collect.DATABASE_PATH = self.data_directory / "history.sqlite3"
        collect.ensure_location = lambda interactive: {"latitude": 29.442335, "longitude": -98.480912}
        collect.source_latest = lambda: "2026-07-31 23:59:25.000"
        collect.zip_centroid = lambda _connection, _postal_code: None

    def tearDown(self):
        collect.DATA_DIR = self.original_data_directory
        collect.DATABASE_PATH = self.original_database_path
        collect.ensure_location = self.original_ensure_location
        collect.source_latest = self.original_source_latest
        collect.request_records = self.original_request_records
        collect.zip_centroid = self.original_zip_centroid
        self.temporary_directory.cleanup()

    def test_normalizes_calls_for_service_and_records_freshness(self):
        collect.request_records = lambda _since, offset: [{
            "_id": 42,
            "Master_Incident_Number": "SAPD-2026-0000042",
            "Response_Date": "2026-07-31 23:59:25.000",
            "Priority": "1",
            "Problem": "Burglary",
            "Service_Area": "CENTRAL",
            "Type": "Emergency",
            "Seconds": "120",
            "Weekday": "Fri",
            "Disposition_Groups": "Report",
            "Disposition_Type": "RPT",
            "Postal_Code": "99999",
        }] if offset == 0 else []

        collect.main()

        with sqlite3.connect(collect.DATABASE_PATH) as connection:
            row = connection.execute(
                "SELECT incident_id, response_at, priority, problem, call_type, postal_code FROM calls"
            ).fetchone()
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        self.assertEqual(row, ("CFS:42", "2026-07-31 23:59:25.000", "1", "Burglary", "Emergency", "99999"))
        self.assertEqual(metadata["source_kind"], "sapd_calls_for_service")
        self.assertEqual(metadata["source_resource_id"], collect.RESOURCE_ID)
        self.assertEqual(metadata["source_latest_response_at"], "2026-07-31 23:59:25.000")
        self.assertEqual(metadata["source_cursor"], "2026-07-31 23:59:25.000")
        self.assertIn("collector_last_success", metadata)

    def test_unchanged_source_is_a_successful_poll(self):
        collect.request_records = lambda _since, offset: [{
            "_id": 99,
            "Response_Date": "2026-07-31 23:59:25.000",
            "Priority": "3",
            "Problem": "Welfare Check",
            "Service_Area": "WEST",
            "Type": "Non-Emergency",
            "Seconds": "60",
            "Weekday": "Fri",
            "Disposition_Groups": "Closed",
            "Disposition_Type": "CLS",
            "Postal_Code": "99999",
        }] if offset == 0 else []
        collect.main()
        first_success = (collect.DATA_DIR / "last_success").read_text(encoding="utf-8")

        collect.request_records = lambda _since, _offset: self.fail("unchanged source should not page records")
        collect.main()

        with sqlite3.connect(collect.DATABASE_PATH) as connection:
            metadata = dict(connection.execute("SELECT key, value FROM metadata"))
        self.assertEqual(metadata["source_latest_response_at"], "2026-07-31 23:59:25.000")
        self.assertIn("collector_last_success", metadata)
        self.assertTrue(first_success)


if __name__ == "__main__":
    unittest.main()
