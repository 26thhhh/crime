import importlib.util
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SPEC = importlib.util.spec_from_file_location(
    "import_community_crime_map", Path(__file__).with_name("import_community_crime_map.py")
)
importer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(importer)


class CommunityCrimeMapImportTests(unittest.TestCase):
    def test_import_marks_rows_as_manual_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            directory = Path(temporary_directory)
            snapshot_path = directory / "snapshot.json"
            snapshot_path.write_text(json.dumps({"incidents": [{
                "id": "example-1",
                "occurred_at": "2026-08-20 12:34:00",
                "type": "Theft",
                "agency": "SAPD",
                "zip": "99999",
            }]}), encoding="utf-8")
            original_database_path = importer.DATABASE_PATH
            original_ensure_location = importer.ensure_location
            original_zip_centroid = importer.zip_centroid
            importer.DATABASE_PATH = directory / "history.sqlite3"
            importer.ensure_location = lambda interactive: {"latitude": 29.442335, "longitude": -98.480912}
            importer.zip_centroid = lambda _connection, _postal_code: None
            try:
                with patch.object(sys, "argv", ["import_community_crime_map.py", "--input", str(snapshot_path)]):
                    importer.main()
            finally:
                importer.DATABASE_PATH = original_database_path
                importer.ensure_location = original_ensure_location
                importer.zip_centroid = original_zip_centroid
            with sqlite3.connect(directory / "history.sqlite3") as connection:
                row = connection.execute("SELECT incident_id, problem, source FROM calls").fetchone()
                metadata = dict(connection.execute("SELECT key, value FROM metadata"))
            self.assertEqual(row, ("CCM:example-1", "Theft", "community_crime_map_manual"))
            self.assertEqual(metadata["community_crime_map_last_import_count"], "1")


if __name__ == "__main__":
    unittest.main()
