#!/usr/bin/env python3
"""Import a human-obtained Community Crime Map snapshot.

Open Community Crime Map in a browser, complete its required interaction, and
save only the incidents you choose to import in this JSON shape:

{"incidents":[{"id":"optional-provider-id","occurred_at":"2026-08-20 12:34:00",
"type":"Theft","agency":"SAPD","zip":"99999","priority":"optional",
"disposition":"optional"}]}

This utility does not request Community Crime Map, acquire tokens, or bypass
its browser, reCAPTCHA, or terms flow.
"""

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from datetime import UTC, datetime

from collect import (
    DATABASE_PATH,
    distance_miles,
    ensure_location,
    handle_location_command,
    initialize_database,
    set_metadata,
    zip_centroid,
)

SOURCE = "community_crime_map_manual"


def read_snapshot(path):
    with sys.stdin if path == "-" else open(path, encoding="utf-8") as source_file:
        payload = json.load(source_file)
    if not isinstance(payload, dict) or not isinstance(payload.get("incidents"), list):
        raise ValueError("snapshot must be an object with an incidents array")
    return payload["incidents"]


def read_interactive_snapshot():
    print("Enter incidents visible in Community Crime Map. Leave occurrence time blank when finished.")
    incidents = []
    while True:
        occurred_at = input("Occurrence time: ").strip()
        if not occurred_at:
            break
        call_type = input("Type: ").strip()
        if not call_type:
            print("Type is required; incident was not added.")
            continue
        incident = {"occurred_at": occurred_at, "type": call_type}
        for field, prompt in (
            ("id", "Provider incident ID (optional): "),
            ("agency", "Agency (optional): "),
            ("zip", "ZIP (optional): "),
            ("priority", "Priority (optional): "),
            ("disposition", "Disposition (optional): "),
        ):
            value = input(prompt).strip()
            if value:
                incident[field] = value
        incidents.append(incident)
        print(f"Added incident {len(incidents)}.")
    if not incidents:
        raise ValueError("no incidents entered")
    return incidents


def text(record, field, required=False):
    value = record.get(field)
    if value is None and not required:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"incident {field} must be a non-empty string")
    return value.strip()


def normalized_incident(record, now):
    if not isinstance(record, dict):
        raise ValueError("every incident must be an object")
    occurred_at = text(record, "occurred_at", required=True)
    call_type = text(record, "type", required=True)
    source_id = text(record, "id")
    postal_code = text(record, "zip")
    if postal_code and (len(postal_code) != 5 or not postal_code.isdigit()):
        raise ValueError("incident zip must be a five-digit ZIP code")
    identity = source_id or hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return (
        f"CCM:{identity}", occurred_at, text(record, "priority"), call_type,
        text(record, "agency"), "Community Crime Map", None, None,
        text(record, "disposition"), None, postal_code, SOURCE, now, now,
    )


def main():
    if handle_location_command(sys.argv[1:]):
        return
    parser = argparse.ArgumentParser(
        description="Import a manually captured Community Crime Map snapshot.",
    )
    parser.add_argument("--input", help="JSON snapshot path, or - for standard input; omit for interactive entry")
    args = parser.parse_args()
    try:
        incidents = read_snapshot(args.input) if args.input else read_interactive_snapshot()
        now = int(time.time())
        rows = [normalized_incident(record, now) for record in incidents]
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(f"invalid Community Crime Map snapshot: {error}") from error

    location = ensure_location(interactive=True)
    captured_at = datetime.now(UTC).isoformat(timespec="seconds")
    with sqlite3.connect(DATABASE_PATH) as connection:
        initialize_database(connection)
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
                disposition_group = excluded.disposition_group,
                postal_code = excluded.postal_code,
                source = excluded.source,
                last_seen = excluded.last_seen
        """, rows)
        location_key = f"{location['latitude']:.6f},{location['longitude']:.6f}"
        if connection.execute("SELECT value FROM metadata WHERE key = 'location_key'").fetchone() != (location_key,):
            connection.execute("UPDATE calls SET zip_distance_miles = NULL")
            set_metadata(connection, "location_key", location_key)
        postal_codes = connection.execute(
            "SELECT DISTINCT postal_code FROM calls WHERE source = ? AND postal_code GLOB '[0-9][0-9][0-9][0-9][0-9]' AND zip_distance_miles IS NULL",
            (SOURCE,),
        ).fetchall()
        for (postal_code,) in postal_codes:
            centroid = zip_centroid(connection, postal_code)
            if centroid:
                connection.execute(
                    "UPDATE calls SET zip_distance_miles = ? WHERE postal_code = ?",
                    (distance_miles(*centroid, location), postal_code),
                )
        set_metadata(connection, "community_crime_map_last_import", captured_at)
        set_metadata(connection, "community_crime_map_last_import_count", str(len(rows)))
    print(f"Imported {len(rows)} manually captured Community Crime Map incidents at {captured_at}.")


if __name__ == "__main__":
    main()
