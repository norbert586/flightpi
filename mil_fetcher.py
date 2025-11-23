#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import requests
import sqlite3
import os
from datetime import datetime

MIL_DB = os.path.join(os.path.dirname(__file__), "flight_mil.db")


API_URL = "https://api.adsb.lol/v2/mil"
TIMEOUT_S = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (FlightPi)",
    "Accept": "application/json",
}

def connect():
    conn = sqlite3.connect(MIL_DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = connect()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mil (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            logged_at TEXT,
            hex TEXT,
            flight TEXT,
            type TEXT,
            alt_baro REAL,
            lat REAL,
            lon REAL,
            seen REAL,
            rssi REAL
        );
    """)
    conn.commit()
    conn.close()

def fetch_mil():
    try:
        r = requests.get(API_URL, headers=HEADERS, timeout=TIMEOUT_S)
        print("HTTP RESPONSE:", r.status_code)
        if r.status_code != 200:
            print("ERROR: non-200 response")
            return []

        data = r.json()
        ac_list = data.get("ac", [])
        print(f"Returned {len(ac_list)} aircraft")
        return ac_list

    except Exception as e:
        print("ERROR fetching:", e)
        return []

def log_mil(ac_list):
    if not ac_list:
        print("No aircraft to log.")
        return
