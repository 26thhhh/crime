#!/usr/bin/env python3
import json
import math
import sqlite3
import time
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
import sys

from location import ensure_location, handle_location_command

HOME = Path.home()
DATA_DIR = HOME / ".local/share/kanslor-crime"
DATABASE_PATH = DATA_DIR / "history.sqlite3"
RESOURCE_ID = "9cb17985-ac16-49a6-ad69-6fe5ad8f2bf5"
API_URL = "https://data.sanantonio.gov/api/3/action/datastore_search_sql"
SOURCE_KIND = "sapd_calls_for_service"
CITY_SOURCE = "city_cfs"
FIELDS = '"_id", "Master_Incident_Number", "Response_Date", "Priority", "Problem", "Service_Area", "Type", "Seconds", "Weekday", "Disposition_Groups", "Disposition_Type", "Postal_Code"'


def initialize_database(connection):
    connection.execute("""
        CREATE TABLE IF NOT EXISTS calls (
            incident_id TEXT PRIMARY KEY,
            response_at TEXT NOT NULL,
            priority TEXT,
            problem TEXT,
            service_area TEXT,
            call_type TEXT,
            response_seconds TEXT,
            weekday TEXT,
            disposition_group TEXT,
            disposition_type TEXT,
            postal_code TEXT,
            zip_distance_miles REAL,
            source TEXT NOT NULL DEFAULT 'city_cfs',
            first_seen INTEGER NOT NULL,
            last_seen INTEGER NOT NULL
        )
    """)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(calls)")}
    if "zip_distance_miles" not in columns:
        connection.execute("ALTER TABLE calls ADD COLUMN zip_distance_miles REAL")
    if "source" not in columns:
        connection.execute("ALTER TABLE calls ADD COLUMN source TEXT NOT NULL DEFAULT 'city_cfs'")
    connection.execute("CREATE INDEX IF NOT EXISTS calls_response_at ON calls(response_at DESC)")
    connection.execute("CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    connection.execute("""
        CREATE TABLE IF NOT EXISTS zip_centroids (
            postal_code TEXT PRIMARY KEY,
            latitude REAL NOT NULL,
            longitude REAL NOT NULL,
            fetched_at INTEGER NOT NULL
        )
    """)


def metadata_value(connection, key):
    row = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_metadata(connection, key, value):
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def source_latest():
    sql = f'SELECT MAX("Response_Date") AS latest FROM "{RESOURCE_ID}"'
    url = f"{API_URL}?{urllib.parse.urlencode({'sql': sql})}"
    request = urllib.request.Request(url, headers={"User-Agent": "kanslor-crime-watch/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["result"]["records"][0]["latest"]


def request_records(since, offset):
    sql = f'SELECT {FIELDS} FROM "{RESOURCE_ID}" WHERE "Response_Date" >= \'{since}\' ORDER BY "_id" ASC LIMIT 10000 OFFSET {offset}'
    url = f"{API_URL}?{urllib.parse.urlencode({'sql': sql})}"
    request = urllib.request.Request(url, headers={"User-Agent": "kanslor-crime-watch/1.0"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)["result"]["records"]


def distance_miles(latitude, longitude, location):
    earth_radius_miles = 3958.7613
    lat1, lon1, lat2, lon2 = map(math.radians, (location["latitude"], location["longitude"], latitude, longitude))
    a = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return round(earth_radius_miles * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


def zip_centroid(connection, postal_code):
    cached = connection.execute(
        "SELECT latitude, longitude FROM zip_centroids WHERE postal_code = ?", (postal_code,)
    ).fetchone()
    if cached:
        return cached
    request = urllib.request.Request(
        f"https://api.zippopotam.us/us/{postal_code}",
        headers={"User-Agent": "kanslor-crime-watch/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            place = json.load(response)["places"][0]
        centroid = float(place["latitude"]), float(place["longitude"])
    except (OSError, KeyError, IndexError, ValueError, json.JSONDecodeError):
        return None
    connection.execute(
        "INSERT INTO zip_centroids(postal_code, latitude, longitude, fetched_at) VALUES (?, ?, ?, ?)",
        (postal_code, centroid[0], centroid[1], int(time.time())),
    )
    return centroid


def collect():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    location = ensure_location(interactive=sys.stdin.isatty())
    with sqlite3.connect(DATABASE_PATH) as connection:
        initialize_database(connection)
        if metadata_value(connection, "source_kind") != SOURCE_KIND:
            connection.execute("CREATE TABLE IF NOT EXISTS calls_offenses_archive AS SELECT * FROM calls")
            connection.execute("DELETE FROM calls")
            connection.execute("DELETE FROM metadata WHERE key = 'source_cursor'")
            set_metadata(connection, "source_kind", SOURCE_KIND)
        cursor = metadata_value(connection, "source_cursor")
        latest_source = source_latest()
        checked_at = datetime.now(UTC).isoformat(timespec="seconds")
        set_metadata(connection, "source_resource_id", RESOURCE_ID)
        set_metadata(connection, "source_last_checked", checked_at)
        if latest_source:
            set_metadata(connection, "source_latest_response_at", latest_source)
        if cursor and latest_source and cursor >= latest_source:
            set_metadata(connection, "collector_last_success", checked_at)
            connection.execute("DELETE FROM metadata WHERE key = 'collector_last_failure'")
            (DATA_DIR / "last_success").write_text(str(int(time.time())), encoding="utf-8")
            return
        if cursor:
            since = (datetime.fromisoformat(cursor) - timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
        else:
            since = (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d 00:00:00")

        offset = 0
        latest = cursor
        now = int(time.time())
        while True:
            records = request_records(since, offset)
            if not records:
                break
            rows = []
            for row in records:
                source_id = row.get("_id")
                response_at = (row.get("Response_Date") or "").strip()
                incident_id = f"CFS:{source_id}" if source_id is not None else ""
                if not incident_id or not response_at:
                    continue
                rows.append((
                    incident_id, response_at, row.get("Priority"), row.get("Problem"), row.get("Service_Area"),
                    row.get("Type"), row.get("Seconds"), row.get("Weekday"), row.get("Disposition_Groups"),
                    row.get("Disposition_Type"), row.get("Postal_Code"), CITY_SOURCE, now, now,
                ))
                latest = max(latest or response_at, response_at)
            connection.executemany("""
                INSERT INTO calls (
                    incident_id, response_at, priority, problem, service_area, call_type,
                    response_seconds, weekday, disposition_group, disposition_type, postal_code,
                    source, first_seen, last_seen
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    response_at = excluded.response_at,
                    priority = excluded.priority,
                    problem = excluded.problem,
                    service_area = excluded.service_area,
                    call_type = excluded.call_type,
                    response_seconds = excluded.response_seconds,
                    weekday = excluded.weekday,
                    disposition_group = excluded.disposition_group,
                    disposition_type = excluded.disposition_type,
                    postal_code = excluded.postal_code,
                    source = excluded.source,
                    last_seen = excluded.last_seen
            """, rows)
            if len(records) < 10000:
                break
            offset += len(records)

        if latest:
            set_metadata(connection, "source_cursor", latest)
        location_key = f"{location['latitude']:.6f},{location['longitude']:.6f}"
        if metadata_value(connection, "location_key") != location_key:
            connection.execute("UPDATE calls SET zip_distance_miles = NULL")
            connection.execute("INSERT INTO metadata(key, value) VALUES('location_key', ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", (location_key,))
        postal_codes = connection.execute("SELECT DISTINCT postal_code FROM calls WHERE postal_code GLOB '[0-9][0-9][0-9][0-9][0-9]' AND zip_distance_miles IS NULL").fetchall()
        for (postal_code,) in postal_codes:
            centroid = zip_centroid(connection, postal_code)
            if centroid:
                connection.execute(
                    "UPDATE calls SET zip_distance_miles = ? WHERE postal_code = ?",
                    (distance_miles(*centroid, location), postal_code),
                )
        set_metadata(connection, "collector_last_success", checked_at)
        connection.execute("DELETE FROM metadata WHERE key = 'collector_last_failure'")
    (DATA_DIR / "last_success").write_text(str(int(time.time())), encoding="utf-8")


def record_collector_failure(error):
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        initialize_database(connection)
        set_metadata(
            connection,
            "collector_last_failure",
            f"{datetime.now(UTC).isoformat(timespec='seconds')}: {error}",
        )


def main():
    try:
        if handle_location_command(sys.argv[1:]):
            return
        collect()
    except Exception as error:
        record_collector_failure(error)
        raise


if __name__ == "__main__":
    main()
