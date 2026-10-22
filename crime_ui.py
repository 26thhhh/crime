#!/usr/bin/env python3
import curses
import sqlite3
import sys
from pathlib import Path

from location import ensure_location, handle_location_command

DATABASE_PATH = Path.home() / ".local/share/kanslor-crime/history.sqlite3"


def draw_line(screen, row, text, selected=False, color=0):
    height, width = screen.getmaxyx()
    if row >= height:
        return
    try:
        screen.addnstr(row, 0, text.ljust(max(width - 1, 1)), max(width - 1, 1), curses.A_REVERSE if selected else color)
    except curses.error:
        pass


def calls(connection, sort_mode, call_filter):
    where = "WHERE problem LIKE ?" if call_filter else ""
    parameters = (f"%{call_filter}%",) if call_filter else ()
    if sort_mode == "PRIORITY":
        order = "COALESCE(priority, '') ASC, response_at DESC"
    elif sort_mode == "AREA":
        order = "COALESCE(service_area, '') ASC, response_at DESC"
    elif sort_mode == "ZIP PROXIMITY":
        order = "zip_distance_miles IS NULL, zip_distance_miles ASC, response_at DESC"
    else:
        order = "response_at DESC"
    return connection.execute(f"""
        SELECT incident_id, response_at, priority, problem, service_area, call_type,
               response_seconds, weekday, disposition_group, disposition_type, postal_code, zip_distance_miles, source
        FROM calls {where}
        ORDER BY {order}
    """, parameters).fetchall()


def prompt_filter(screen, current_filter):
    height, width = screen.getmaxyx()
    screen.move(height - 1, 0)
    screen.clrtoeol()
    screen.addnstr(height - 1, 0, "Call-type filter (blank clears): ", max(width - 1, 1))
    curses.echo()
    curses.curs_set(1)
    try:
        value = screen.getstr(height - 1, min(32, width - 1), max(width - 33, 1)).decode("utf-8").strip()
    except curses.error:
        value = current_filter
    finally:
        curses.noecho()
        curses.curs_set(0)
    return value


def source_freshness(connection):
    metadata = dict(connection.execute("SELECT key, value FROM metadata"))
    row = connection.execute("""
        SELECT MIN(response_at), MAX(response_at)
        FROM calls WHERE source = 'city_cfs'
    """).fetchone()
    coverage_start, coverage_end = row if row else (None, None)
    source_latest = metadata.get("source_latest_response_at", metadata.get("source_cursor", coverage_end))
    source_line = (
        f"SAPD DATA COVERAGE: {coverage_start or 'unavailable'} through {coverage_end or 'unavailable'} "
        f"| LATEST PUBLISHED RECORD: {source_latest or 'unavailable'}"
    )
    manual_import = metadata.get("community_crime_map_last_import", "unavailable")
    manual_count = metadata.get("community_crime_map_last_import_count")
    manual_status = manual_import if not manual_count else f"{manual_import} ({manual_count} incidents)"
    collector_failure = metadata.get("collector_last_failure")
    if collector_failure:
        collector_line = f"COLLECTOR WARNING: {collector_failure}"
    elif metadata.get("collector_last_success"):
        collector_line = f"CITY COLLECTOR LAST SUCCESS: {metadata['collector_last_success']} | MANUAL CCM SNAPSHOT: {manual_status}"
    else:
        collector_line = "COLLECTOR WARNING: no successful city collection recorded"
    return source_line, collector_line


def detail_view(screen, call):
    labels = (
        ("CALL", call[0]), ("RESPONSE DATE", call[1]), ("PRIORITY", call[2] or "--"),
        ("PROBLEM", call[3] or "--"), ("SERVICE AREA", call[4] or "--"),
        ("TYPE", call[5] or "--"), ("DISPOSITION", call[8] or "--"),
        ("POSTAL CODE", call[10] or "Not Listed"),
        ("ZIP PROXIMITY", f"{call[11]:.2f} miles from home (ZIP centroid approximation)" if call[11] is not None else "Unavailable"),
        ("SOURCE", "Manual Community Crime Map snapshot" if call[12] == "community_crime_map_manual" else "City of San Antonio Calls for Service"),
    )
    while True:
        screen.erase()
        draw_line(screen, 0, "S A P D  //  PUBLIC CALL DETAIL", color=curses.color_pair(1))
        draw_line(screen, 1, "ESC return to citywide calls list")
        row = 3
        for label, value in labels:
            draw_line(screen, row, f"{label:<20} {value}")
            row += 1
        row += 1
        draw_line(screen, row, "SOURCE: City of San Antonio SAPD Calls for Service (CC BY 4.0)", color=curses.color_pair(2))
        draw_line(screen, row + 1, "This public dataset does not publish coordinates, addresses, narratives, or dispatch notes.", color=curses.color_pair(2))
        screen.refresh()
        if screen.getch() in (27, ord("q"), ord("Q")):
            return


def app(screen):
    curses.curs_set(0)
    curses.start_color()
    curses.use_default_colors()
    curses.init_pair(1, curses.COLOR_RED, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    selected = 0
    sort_mode = "TIME"
    call_filter = ""
    cached_key = None
    while True:
        screen.erase()
        if not DATABASE_PATH.exists():
            draw_line(screen, 0, "SAPD // CALL HISTORY", color=curses.color_pair(1))
            draw_line(screen, 2, "No local history yet. The official collector will create it on its next scheduled run.")
            draw_line(screen, 4, "ESC exit")
            screen.refresh()
            if screen.getch() == 27:
                return
            continue
        with sqlite3.connect(DATABASE_PATH) as connection:
            query_key = (sort_mode, call_filter)
            if cached_key != query_key:
                rows = calls(connection, sort_mode, call_filter)
                freshness, collector_status = source_freshness(connection)
                cached_key = query_key
                selected = min(selected, max(len(rows) - 1, 0))
            filter_label = call_filter or "ALL"
            draw_line(screen, 0, f"SAPD // CALL HISTORY   [SORT: {sort_mode} | FILTER: {filter_label}]", color=curses.color_pair(1))
            draw_line(screen, 1, "UP/DOWN select  ENTER detail  T time  P priority  S area  D ZIP distance  F filter  C clear  R refresh  ESC exit")
            draw_line(screen, 2, "ZIP proximity uses ZIP centroids, not incident coordinates. It is approximate.", color=curses.color_pair(2))
            draw_line(screen, 3, freshness, color=curses.color_pair(2))
            draw_line(screen, 4, collector_status, color=curses.color_pair(2))
            draw_line(screen, 6, "PROBLEM                   RESPONSE DATE    AREA     ZIP    PRI  SRC  ZIP APPROX", color=curses.color_pair(2))
            height, _ = screen.getmaxyx()
            visible = max(height - 8, 1)
            start = max(0, selected - visible + 1)
            for index, call in enumerate(rows[start:start + visible], start):
                distance = f"{call[11]:.1f}mi" if call[11] is not None else "--"
                source = "CCM" if call[12] == "community_crime_map_manual" else "CITY"
                text = f"{(call[3] or '--'):<25.25} {call[1][:16]:<16} {(call[4] or '--'):<8.8} {(call[10] or '--'):<6.6} {call[2] or '--':<4.4} {source:<4.4} {distance}"
                draw_line(screen, index - start + 7, text, index == selected)
            if not rows:
                draw_line(screen, 7, "No calls stored yet.")
            screen.refresh()
            key = screen.getch()
            if key in (ord("r"), ord("R")):
                cached_key = None
                continue
            if key in (27, ord("q"), ord("Q")):
                return
            if key in (curses.KEY_DOWN, ord("j")) and selected < len(rows) - 1:
                selected += 1
            elif key in (curses.KEY_UP, ord("k")) and selected > 0:
                selected -= 1
            elif key in (10, 13, curses.KEY_ENTER) and rows:
                detail_view(screen, rows[selected])
            elif key in (ord("t"), ord("T")):
                sort_mode, selected = "TIME", 0
            elif key in (ord("p"), ord("P")):
                sort_mode, selected = "PRIORITY", 0
            elif key in (ord("s"), ord("S")):
                sort_mode, selected = "AREA", 0
            elif key in (ord("d"), ord("D")):
                sort_mode, selected = "ZIP PROXIMITY", 0
            elif key in (ord("f"), ord("F")):
                call_filter = prompt_filter(screen, call_filter)
                selected = 0
            elif key in (ord("c"), ord("C")):
                call_filter, selected = "", 0


if __name__ == "__main__":
    if not handle_location_command(sys.argv[1:]):
        ensure_location(interactive=True)
        curses.wrapper(app)
