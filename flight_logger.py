#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import os
import sqlite3
from datetime import datetime, timedelta
from typing import Dict, Any

DB_PATH = os.path.join(os.path.dirname(__file__), "flight_log.db")

# Option A: same flight (same hex|reg|callsign) inside 20 minutes = SAME EVENT
EVENT_WINDOW_MINUTES = 20


def _connect():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """
    Create the flights table if it does not exist.
    Table MUST align with our INSERT — 23 columns (id is auto).
    """
    conn = _connect()
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS flights (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            seen_at TEXT,
            hex TEXT,
            reg TEXT,
            callsign TEXT,
            type_code TEXT,
            model TEXT,
            manufacturer TEXT,
            country TEXT,
            country_iso TEXT,
            owner TEXT,
            airline_name TEXT,
            origin_iata TEXT,
            origin_name TEXT,
            dest_iata TEXT,
            dest_name TEXT,
            altitude_ft REAL,
            ground_speed_kt REAL,
            distance_nm REAL,
            heading_deg REAL,
            event_key TEXT,
            first_seen TEXT,
            last_seen TEXT,
            times_seen INTEGER DEFAULT 1
        );
        """
    )

    conn.commit()
    conn.close()


def _build_event_key(row: Dict[str, Any]) -> str:
    """
    A unique key to identify the same aircraft + callsign.
    """
    hex_ = (row.get("hex") or "").strip()
    reg = (row.get("reg") or "").strip()
    cs = (row.get("callsign") or "").strip()
    return f"{hex_}|{reg}|{cs}"


def log_flight(row: Dict[str, Any]) -> None:
    """
    Logging logic:
    - Create event_key (hex|reg|callsign)
    - If within EVENT_WINDOW_MINUTES of last_seen → update existing event
    - Otherwise → insert new event
    """
    init_db()

    seen_at = row.get("seen_at")
    if not seen_at:
        seen_at = datetime.now().isoformat(timespec="seconds")
        row["seen_at"] = seen_at

    event_key = _build_event_key(row)

    # If nothing useful to identify the aircraft, store as a new event
    if not event_key.replace("|", ""):
        _insert_new_event(row, event_key)
        return

    # Calculate the cutoff timestamp
    seen_dt = datetime.fromisoformat(seen_at)
    cutoff_dt = seen_dt - timedelta(minutes=EVENT_WINDOW_MINUTES)
    cutoff_iso = cutoff_dt.isoformat(timespec="seconds")

    conn = _connect()
    cur = conn.cursor()

    # Find recent event with same key
    cur.execute(
        """
        SELECT id, times_seen
        FROM flights
        WHERE event_key = ?
          AND last_seen >= ?
        ORDER BY last_seen DESC
        LIMIT 1;
        """,
        (event_key, cutoff_iso),
    )

    match = cur.fetchone()

    if match:
        # ---- UPDATE EXISTING EVENT ----
        cur.execute(
            """
            UPDATE flights
            SET
                last_seen       = ?,
                seen_at         = ?,   -- frontend expects seen_at to reflect newest
                times_seen      = times_seen + 1,
                altitude_ft     = ?,
                ground_speed_kt = ?,
                distance_nm     = ?,
                heading_deg     = ?,
                model           = COALESCE(NULLIF(model, ''), ?),
                manufacturer    = COALESCE(NULLIF(manufacturer, ''), ?),
                country         = COALESCE(NULLIF(country, ''), ?),
                country_iso     = COALESCE(NULLIF(country_iso, ''), ?),
                owner           = COALESCE(NULLIF(owner, ''), ?),
                airline_name    = COALESCE(NULLIF(airline_name, ''), ?),
                origin_iata     = COALESCE(NULLIF(origin_iata, ''), ?),
                origin_name     = COALESCE(NULLIF(origin_name, ''), ?),
                dest_iata       = COALESCE(NULLIF(dest_iata, ''), ?),
                dest_name       = COALESCE(NULLIF(dest_name, ''), ?)
            WHERE id = ?
            """,
            (
                seen_at,
                seen_at,
                row.get("altitude_ft"),
                row.get("ground_speed_kt"),
                row.get("distance_nm"),
                row.get("heading_deg"),
                row.get("model"),
                row.get("manufacturer"),
                row.get("country"),
                row.get("country_iso"),
                row.get("owner"),
                row.get("airline_name"),
                row.get("origin_iata"),
                row.get("origin_name"),
                row.get("dest_iata"),
                row.get("dest_name"),
                match["id"],
            ),
        )
        conn.commit()
        conn.close()
        return

    # No match → insert a new event row
    conn.close()
    _insert_new_event(row, event_key)


def _insert_new_event(row: Dict[str, Any], event_key: str) -> None:
    """
    Insert brand new event.
    Table has 24 columns but ID is autoincrement → 23 VALUES needed.
    """
    conn = _connect()
    cur = conn.cursor()

    seen_at = row.get("seen_at") or datetime.now().isoformat(timespec="seconds")

    cur.execute(
        """
        INSERT INTO flights (
            seen_at,
            hex,
            reg,
            callsign,
            type_code,
            model,
            manufacturer,
            country,
            country_iso,
            owner,
            airline_name,
            origin_iata,
            origin_name,
            dest_iata,
            dest_name,
            altitude_ft,
            ground_speed_kt,
            distance_nm,
            heading_deg,
            event_key,
            first_seen,
            last_seen,
            times_seen
        )
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            seen_at,
            row.get("hex"),
            row.get("reg"),
            row.get("callsign"),
            row.get("type_code"),
            row.get("model"),
            row.get("manufacturer"),
            row.get("country"),
            row.get("country_iso"),
            row.get("owner"),
            row.get("airline_name"),
            row.get("origin_iata"),
            row.get("origin_name"),
            row.get("dest_iata"),
            row.get("dest_name"),
            row.get("altitude_ft"),
            row.get("ground_speed_kt"),
            row.get("distance_nm"),
            row.get("heading_deg"),
            event_key,
            seen_at,   # first_seen
            seen_at,   # last_seen
            1          # times_seen
        ),
    )

    conn.commit()
    conn.close()
