#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import os
import sqlite3
import json
import time
import requests
from flask import (
    Flask,
    jsonify,
    render_template_string,
    request,
    send_from_directory,
    Response,
)
from flight_logger import DB_PATH

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MIL_CACHE_PATH = os.path.join(BASE_DIR, "mil_cache.json")

# ---------------------------------------------------
# Favicon
# ---------------------------------------------------
@app.route("/favicon.ico")
def favicon():
    # Put your icon as favicon.png in /home/pi/flight-display
    return send_from_directory(BASE_DIR, "favicon.png", mimetype="image/png")

# ---------------------------------------------------
# Plane image endpoint (for main log modal)
# ---------------------------------------------------
@app.route("/planeimg/<hex_code>")
def plane_image(hex_code):
    hex_code = hex_code.strip().upper()
    photo_dir = os.path.join(BASE_DIR, "cache", "photos")
    photo_path = os.path.join(photo_dir, f"{hex_code}.jpg")

    if os.path.exists(photo_path):
        return send_from_directory(photo_dir, f"{hex_code}.jpg")

    # Fallback: 1x1 transparent pixel or blank fallback
    return send_from_directory(BASE_DIR, "blank.png", mimetype="image/png")


# ---------------------------------------------------
# MIL cache helpers (for ADSBdb lookups)
# ---------------------------------------------------
def load_mil_cache():
    if not os.path.exists(MIL_CACHE_PATH):
        return {}
    try:
        with open(MIL_CACHE_PATH, "r") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except Exception:
        return {}


def save_mil_cache(cache):
    tmp = MIL_CACHE_PATH + ".tmp"
    with open(tmp, "w") as f:
        json.dump(cache, f)
    os.replace(tmp, MIL_CACHE_PATH)


def get_mil_aircraft_details(hex_code: str):
    """
    Lazy, cached ADSBdb lookup.
    Returns a small dict with enriched info about this hex.
    """
    hex_code = (hex_code or "").strip().upper()
    if not hex_code:
        return {"error": "no_hex"}

    cache = load_mil_cache()
    if hex_code in cache:
        return cache[hex_code]

    url = f"https://api.adsbdb.com/v0/aircraft/{hex_code}"
    try:
        r = requests.get(url, timeout=6)
        r.raise_for_status()
        j = r.json()
        ac = j.get("response", {}).get("aircraft")
        if not ac:
            data = {
                "hex": hex_code,
                "found": False,
            }
        else:
            data = {
                "hex": hex_code,
                "found": True,
                "type": ac.get("type"),
                "icao_type": ac.get("icao_type"),
                "manufacturer": ac.get("manufacturer"),
                "mode_s": ac.get("mode_s"),
                "country_iso": ac.get("registered_owner_country_iso_name"),
                "country_name": ac.get("registered_owner_country_name"),
                "owner": ac.get("registered_owner"),
            }
        cache[hex_code] = data
        save_mil_cache(cache)
        return data
    except Exception:
        # If lookup fails and we have nothing cached, return a basic error
        if hex_code in cache:
            return cache[hex_code]
        return {"hex": hex_code, "found": False, "error": "lookup_failed"}


# ---------------------------------------------------
# DB Helpers (main flight log)
# ---------------------------------------------------
def get_recent_flights(limit=150):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()
        c.execute(
            """
            SELECT *
            FROM flights
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = c.fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_flight_detail(reg: str, hex_code: str):
    reg = (reg or "").strip().upper()
    hex_code = (hex_code or "").strip().upper()

    if not reg and not hex_code:
        return None

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()

        where_clauses = []
        params = []
        if reg:
            where_clauses.append("reg = ?")
            params.append(reg)
        if hex_code:
            where_clauses.append("hex = ?")
            params.append(hex_code)
        where = " AND ".join(where_clauses) or "1=0"

        # aggregate stats
        c.execute(
            f"""
            SELECT
                COUNT(*) AS total_seen,
                MIN(seen_at) AS first_seen,
                MAX(seen_at) AS last_seen
            FROM flights
            WHERE {where}
            """,
            params,
        )
        agg = c.fetchone()

        # most recent row for extra info
        c.execute(
            f"""
            SELECT
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
                heading_deg
            FROM flights
            WHERE {where}
            ORDER BY id DESC
            LIMIT 1
            """,
            params,
        )
        last_row = c.fetchone()

        if not agg or not last_row:
            return None

        detail = dict(last_row)
        detail["total_seen"] = agg["total_seen"]
        detail["first_seen"] = agg["first_seen"]
        detail["last_seen"] = agg["last_seen"]
        return detail
    finally:
        conn.close()


def get_stats():
    """
    Return overall + today's stats for mini-bar and /stats page.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        c = conn.cursor()

        # Total flights overall
        c.execute("SELECT COUNT(*) AS cnt FROM flights")
        total_flights = c.fetchone()["cnt"]

        # Flights today (local time)
        c.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM flights
            WHERE date(seen_at) = date('now', 'localtime')
            """
        )
        flights_today = c.fetchone()["cnt"]

        # Unique aircraft today (by reg or hex)
        c.execute(
            """
            SELECT COUNT(DISTINCT COALESCE(NULLIF(reg,''), hex)) AS cnt
            FROM flights
            WHERE date(seen_at) = date('now', 'localtime')
            """
        )
        unique_today = c.fetchone()["cnt"]

        # Most common airline today
        c.execute(
            """
            SELECT airline_name, COUNT(*) AS cnt
            FROM flights
            WHERE date(seen_at) = date('now', 'localtime')
              AND airline_name IS NOT NULL
              AND airline_name != ''
            GROUP BY airline_name
            ORDER BY cnt DESC
            LIMIT 1
            """
        )
        row = c.fetchone()
        top_airline_today = row["airline_name"] if row else None
        top_airline_today_count = row["cnt"] if row else 0

        # Top airlines overall
        c.execute(
            """
            SELECT airline_name, COUNT(*) AS cnt
            FROM flights
            WHERE airline_name IS NOT NULL
              AND airline_name != ''
            GROUP BY airline_name
            ORDER BY cnt DESC
            LIMIT 10
            """
        )
        top_airlines = [dict(r) for r in c.fetchall()]

        # Top aircraft types (model or type_code)
        c.execute(
            """
            SELECT
                COALESCE(NULLIF(model,''), type_code) AS aircraft_type,
                COUNT(*) AS cnt
            FROM flights
            WHERE COALESCE(NULLIF(model,''), type_code) IS NOT NULL
              AND COALESCE(NULLIF(model,''), type_code) != ''
            GROUP BY aircraft_type
            ORDER BY cnt DESC
            LIMIT 10
            """
        )
        top_types = [dict(r) for r in c.fetchall()]

        # Top countries (registration country)
        c.execute(
            """
            SELECT
                COALESCE(NULLIF(country,''), country_iso) AS country,
                country_iso,
                COUNT(*) AS cnt
            FROM flights
            GROUP BY COALESCE(NULLIF(country,''), country_iso), country_iso
            ORDER BY cnt DESC
            LIMIT 10
            """
        )
        top_countries = [dict(r) for r in c.fetchall()]

        # Most-seen aircraft (by reg or hex)
        c.execute(
            """
            SELECT
                COALESCE(NULLIF(reg,''), hex) AS key_id,
                reg,
                hex,
                airline_name,
                country,
                country_iso,
                COUNT(*) AS cnt,
                MIN(seen_at) AS first_seen,
                MAX(seen_at) AS last_seen
            FROM flights
            GROUP BY key_id
            HAVING key_id IS NOT NULL
            ORDER BY cnt DESC
            LIMIT 10
            """
        )
        top_aircraft = [dict(r) for r in c.fetchall()]

        return {
            "total_flights": total_flights,
            "flights_today": flights_today,
            "unique_today": unique_today,
            "top_airline_today": top_airline_today,
            "top_airline_today_count": top_airline_today_count,
            "top_airlines": top_airlines,
            "top_types": top_types,
            "top_countries": top_countries,
            "top_aircraft": top_aircraft,
        }
    finally:
        conn.close()


# ---------------------------------------------------
# API Endpoints (main)
# ---------------------------------------------------
@app.route("/api/flights")
def api_flights():
    limit = int(request.args.get("limit", 50))
    return jsonify(get_recent_flights(limit))


@app.route("/api/flight_detail")
def api_flight_detail():
    reg = request.args.get("reg", "")
    hex_code = request.args.get("hex", "")
    detail = get_flight_detail(reg, hex_code)
    if not detail:
        return jsonify({"error": "not_found"}), 404
    return jsonify(detail)


@app.route("/api/stats")
def api_stats():
    return jsonify(get_stats())


# ---------------------------------------------------
# API: MIL live feed (no DB, just live endpoint)
# ---------------------------------------------------
@app.route("/api/mil")
def api_mil():
    """
    Calls https://api.adsb.lol/v2/mil and returns a simplified
    list of military aircraft. No DB writes, just pass-through.
    """
    try:
        r = requests.get("https://api.adsb.lol/v2/mil", timeout=6)
        r.raise_for_status()
        data = r.json()
        ac_list = data.get("ac", []) or []

        # Keep it light: only send fields we actually need
        simplified = []
        for ac in ac_list:
            simplified.append(
                {
                    "hex": ac.get("hex"),
                    "flight": (ac.get("flight") or "").strip(),
                    "alt_baro": ac.get("alt_baro"),
                    "squawk": ac.get("squawk"),
                    "category": ac.get("category"),
                    "seen": ac.get("seen"),
                    "rssi": ac.get("rssi"),
                    "t": ac.get("t"),
                    "type": ac.get("type"),
                    "lat": ac.get("lat") or ac.get("rr_lat"),
                    "lon": ac.get("lon") or ac.get("rr_lon"),
                }
            )

        # Sort by altitude desc if available
        simplified.sort(key=lambda a: a.get("alt_baro") or 0, reverse=True)
        return jsonify({"count": len(simplified), "aircraft": simplified})
    except Exception as e:
        return jsonify({"error": "mil_fetch_failed", "details": str(e)}), 502


@app.route("/api/mil_detail")
def api_mil_detail():
    """
    Lazy ADSBdb enrichment for a single hex.
    Uses file-based cache so repeated clicks are instant.
    """
    hex_code = request.args.get("hex", "")
    data = get_mil_aircraft_details(hex_code)
    return jsonify(data)


# ---------------------------------------------------
# Server-Sent Events: push the most-recent flight when it changes
# ---------------------------------------------------
@app.route("/events")
def sse_events():
    def gen():
        last_id = None
        while True:
            try:
                conn = sqlite3.connect(DB_PATH)
                conn.row_factory = sqlite3.Row
                c = conn.cursor()
                c.execute("SELECT * FROM flights ORDER BY id DESC LIMIT 1")
                row = c.fetchone()
                conn.close()
                if row:
                    rowd = dict(row)
                    rid = rowd.get("id")
                    if rid != last_id:
                        last_id = rid
                        yield f"data: {json.dumps(rowd, default=str)}\n\n"
            except Exception:
                pass
            time.sleep(2)

    return Response(gen(), mimetype="text/event-stream")

# ---------------------------------------------------
# MAIN UI (Dashboard)
# ---------------------------------------------------
HTML_MAIN = """
<!DOCTYPE html>
<html>
<head>
    <title>Flight-Pi Intelligence Log</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <link rel="icon" href="/favicon.ico" type="image/png">

    <style>
        body {
            background: #0b0e11;
            color: #d0d3d6;
            font-family: "Segoe UI", Roboto, Arial, sans-serif;
            margin: 0;
            padding: 0;
        }
        a {
            color: #42f5d7;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        header {
            background: #101417;
            padding: 12px 16px 10px 16px;
            border-bottom: 1px solid #1c2329;
        }
        .header-top {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .title {
            font-size: 20px;
            font-weight: 600;
            letter-spacing: 1px;
            text-transform: uppercase;
        }
        .status {
            font-size: 14px;
            display: flex;
            align-items: center;
            gap: 6px;
        }
        .dot {
            width: 10px;
            height: 10px;
            border-radius: 50%;
            background: #19ff86;
        }
        .subheader {
            margin-top: 4px;
            font-size: 12px;
            color: #8a959f;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .subheader-right {
            font-size: 12px;
        }

        .mini-stats {
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin-top: 4px;
            font-size: 12px;
            color: #a8b2ba;
        }
        .mini-stat-item {
            background: #14181d;
            border: 1px solid #1f242a;
            border-radius: 4px;
            padding: 4px 8px;
        }

        .controls {
            padding: 8px 16px 0 16px;
            display: flex;
            flex-wrap: wrap;
            gap: 8px;
            font-size: 13px;
        }
        .select {
            background: #14181d;
            border: 1px solid #1f242a;
            color: #d0d3d6;
            border-radius: 4px;
            padding: 4px 8px;
        }
        .controls-label {
            color: #8a959f;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
        }

        .container {
            padding: 8px 12px 12px 12px;
        }

        .card {
            background: #14181d;
            border: 1px solid #1f242a;
            border-radius: 6px;
            padding: 12px 14px;
            margin-bottom: 12px;
            cursor: pointer;
            position: relative;
        }
        .card:hover {
            border-color: #2a3540;
        }

        .latest-record {
            border-left: 4px solid #ff6f00;
            background: linear-gradient(180deg, rgba(255,111,0,0.04), transparent);
            box-shadow: 0 6px 18px rgba(0,0,0,0.08);
            transform: translateY(-2px);
            transition: transform 200ms ease, box-shadow 200ms ease;
            position: relative;
            padding-left: 12px;
        }
        .latest-record .tag {
            margin-right: 92px;
        }
        .latest-record:hover {
            transform: translateY(-4px);
            box-shadow: 0 10px 24px rgba(0,0,0,0.12);
        }

        .latest-badge {
            position: absolute;
            right: 12px;
            top: 12px;
            background: #ff6f00;
            color: #fff;
            font-weight: 700;
            padding: 4px 8px;
            border-radius: 12px;
            font-size: 12px;
            box-shadow: 0 2px 6px rgba(0,0,0,0.12);
            animation: pulse 2000ms infinite;
        }

        @keyframes pulse {
            0% { box-shadow: 0 0 0 0 rgba(255,111,0,0.4); }
            70% { box-shadow: 0 0 0 10px rgba(255,111,0,0); }
            100% { box-shadow: 0 0 0 0 rgba(255,111,0,0); }
        }

        .latest-animate {
            animation: topGlow 900ms ease-out;
        }
        @keyframes topGlow {
            0% { box-shadow: 0 0 0 0 rgba(255,111,0,0.0); transform: translateY(0); }
            40% { box-shadow: 0 12px 32px rgba(255,111,0,0.18); transform: translateY(-8px); }
            100% { box-shadow: 0 0 0 0 rgba(255,111,0,0.0); transform: translateY(0); }
        }

        .badge-pulse {
            animation: badgePop 700ms ease-out;
        }
        @keyframes badgePop {
            0% { transform: scale(0.92); opacity: 0.95; }
            50% { transform: scale(1.14); opacity: 1; }
            100% { transform: scale(1); opacity: 1; }
        }

        .card-header-line {
            display: flex;
            align-items: baseline;
            gap: 6px;
            margin-bottom: 4px;
        }
        .callsign {
            font-size: 18px;
            color: #42f5d7;
            font-weight: 600;
        }
        .reg {
            font-size: 14px;
            color: #a0b1bb;
        }
        .tag {
            font-size: 11px;
            padding: 1px 6px;
            border-radius: 10px;
            border: 1px solid #28323a;
            color: #a6b5bf;
            margin-left: auto;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            display: flex;
            align-items: center;
            gap: 4px;
        }
        .tag img {
            height: 12px;
            border-radius: 2px;
        }

        .row {
            margin: 2px 0;
            font-size: 13px;
        }
        .label {
            color: #7c8c96;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            margin-right: 4px;
        }

        .route {
            margin-top: 4px;
            margin-bottom: 4px;
            font-size: 14px;
            color: #e8edee;
        }

        .divider {
            margin: 6px 0;
            height: 1px;
            background: #1c2228;
        }

        .telemetry {
            font-size: 13px;
        }

        .plane-img-box {
            width: 260px;
            height: 160px;
            border-radius: 6px;
            border: 1px solid #2a3138;
            background: linear-gradient(135deg, #2e363d, #1e252b);
            object-fit: cover;
            display: flex;
            align-items: center;
            justify-content: center;
            color: #b8c2ca;
            font-size: 13px;
            font-weight: 500;
            text-align: center;
        }

        .modal-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.5);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal-card {
            background: #14181d;
            border: 1px solid #1f242a;
            border-radius: 8px;
            max-width: 420px;
            width: 90%;
            padding: 16px 18px 14px 18px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.7);
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-title {
            font-size: 17px;
            font-weight: 600;
        }
        .modal-close {
            cursor: pointer;
            font-size: 18px;
            color: #a0b1bb;
        }
        .modal-row {
            margin: 4px 0;
            font-size: 13px;
        }

        @media (min-width: 900px) {
            .card {
                max-width: 720px;
                margin: 14px auto;
            }
            .container {
                padding-top: 10px;
            }
            .controls {
                max-width: 720px;
                margin: 0 auto;
            }
            .mini-stats {
                max-width: 720px;
            }
        }
    </style>

    <script>
        let flightsData = [];
        let currentSort = "latest";
        let currentFilter = "all";

        function parseSeen(seenStr) {
            if (!seenStr) return null;
            const d = new Date(seenStr);
            if (isNaN(d.getTime())) return null;
            return d;
        }

        function formatClock(d) {
            return d.toLocaleTimeString([], {hour: "2-digit", minute: "2-digit", second: "2-digit"});
        }

        function formatDateShort(d) {
            return d.toLocaleDateString([], {month: "short", day: "numeric"});
        }

        function timeAgoText(d) {
            const diffSec = Math.floor((Date.now() - d.getTime()) / 1000);
            if (diffSec < 60) return diffSec + "s ago";
            const mins = Math.floor(diffSec / 60);
            if (mins < 60) return mins + "m ago";
            const hrs = Math.floor(mins / 60);
            if (hrs < 24) return hrs + "h ago";
            const days = Math.floor(hrs / 24);
            return days + "d ago";
        }

        function classifyTag(f) {
            if (f.airline_name && f.airline_name.trim() !== "") return "COMMERCIAL";
            if (!f.airline_name && f.owner && f.owner.trim() !== "") return "PRIVATE";
            return "UNKNOWN";
        }

        function countryFlagUrl(iso) {
            if (!iso) return null;
            const code = iso.toLowerCase();
            return "https://flagcdn.com/w20/" + code + ".png";
        }

        function buildRouteText(f) {
            const o = (f.origin_iata || "").trim();
            const d = (f.dest_iata || "").trim();
            if (!o && !d) return "Route: Unknown";
            if (o && !d) return "Route: " + o + " → ?";
            if (!o && d) return "Route: ? → " + d;
            return "Route: " + o + " → " + d;
        }

        function applyFilterAndSort() {
            let list = flightsData.slice();

            if (currentFilter !== "all") {
                list = list.filter(f => {
                    const tag = classifyTag(f);
                    return tag.toLowerCase() === currentFilter;
                });
            }

            if (currentSort === "altitude") {
                list.sort((a, b) => (b.altitude_ft || 0) - (a.altitude_ft || 0));
            } else if (currentSort === "distance") {
                list.sort((a, b) => {
                    const da = a.distance_nm != null ? a.distance_nm : 9999;
                    const db = b.distance_nm != null ? b.distance_nm : 9999;
                    return da - db;
                });
            } else {
                list.sort((a, b) => {
                    const da = parseSeen(a.seen_at);
                    const db = parseSeen(b.seen_at);
                    if (!da && !db) return 0;
                    if (!da) return 1;
                    if (!db) return -1;
                    return db - da;
                });
            }

            return list;
        }

        function renderFlights() {
            const container = document.getElementById("cards");
            container.innerHTML = "";

            const flights = applyFilterAndSort();

            flights.forEach((f, idx) => {
                const card = document.createElement("div");
                const isTop = idx === 0;
                card.className = "card" + (isTop ? " latest-record" : "");
                card.dataset.reg = f.reg || "";
                card.dataset.hex = f.hex || "";
                card.dataset.callsign = f.callsign || "";

                const seenDate = parseSeen(f.seen_at);
                const seenText = seenDate
                    ? formatClock(seenDate) + " · " + formatDateShort(seenDate)
                    : (f.seen_at || "—");

                const typeText = (f.model && f.model.trim()) || (f.type_code && f.type_code.trim()) || "";
                const airlineText = (f.airline_name || "").trim();
                const ownerText = (f.owner || "").trim();
                const countryIso = (f.country_iso || "").trim();
                const flagUrl = countryFlagUrl(countryIso);

                let infoRows = "";

                if (typeText) {
                    infoRows += `
                        <div class="row"><span class="label">Type</span>${typeText}</div>
                    `;
                } else {
                    infoRows += `
                        <div class="row"><span class="label">Type</span>Not available</div>
                    `;
                }

                if (airlineText) {
                    infoRows += `
                        <div class="row"><span class="label">Airline</span>${airlineText}</div>
                    `;
                }

                if (ownerText) {
                    infoRows += `
                        <div class="row"><span class="label">Owner</span>${ownerText}</div>
                    `;
                }

                const alt = f.altitude_ft != null ? f.altitude_ft : "—";
                const dist = f.distance_nm != null ? Number(f.distance_nm).toFixed(1) : "—";

                const tagLabel = classifyTag(f);

                card.innerHTML = `
                    ${isTop ? `<span class="latest-badge">LATEST</span>` : ""}
                    <div class="card-header-line">
                        <div class="callsign">${f.callsign || "UNKNOWN"}</div>
                        <div class="reg">${f.reg || "—"}</div>
                        <div class="tag">
                            ${flagUrl ? `<img src="${flagUrl}" alt="">` : ""}
                            ${tagLabel}
                        </div>
                    </div>

                    ${infoRows}

                    <div class="route">${buildRouteText(f)}</div>

                    <div class="divider"></div>

                    <div class="telemetry">
                        <div class="row"><span class="label">Altitude</span>${alt} ft</div>
                        <div class="row"><span class="label">Distance</span>${dist} nm</div>
                        <div class="row"><span class="label">Seen</span>${seenText}</div>
                    </div>
                `;

                card.addEventListener("click", () => openDetail(card.dataset));
                container.appendChild(card);
            });
        }

        async function loadFlights() {
            const res = await fetch('/api/flights?limit=50');
            const flights = await res.json();
            flightsData = flights;

            const lastUpdateEl = document.getElementById("lastUpdate");
            const dot = document.getElementById("statusDot");

            const lastSeenStr = flights.length > 0 ? flights[0].seen_at : null;

            if (lastSeenStr) {
                const d = parseSeen(lastSeenStr);
                if (d) {
                    const diffSec = (Date.now() - d.getTime()) / 1000;
                    if (diffSec < 90) {
                        dot.style.background = "#19ff86";
                    } else if (diffSec < 300) {
                        dot.style.background = "#ffc842";
                    } else {
                        dot.style.background = "#ff4b5c";
                    }
                    lastUpdateEl.textContent =
                        "Last update: " +
                        formatClock(d) + " · " +
                        formatDateShort(d) +
                        " (" + timeAgoText(d) + ")";
                } else {
                    dot.style.background = "#ffc842";
                    lastUpdateEl.textContent = "Last update: —";
                }
            } else {
                dot.style.background = "#ffc842";
                lastUpdateEl.textContent = "Last update: no flights logged yet";
            }

            renderFlights();
        }

        async function loadStats() {
            try {
                const res = await fetch('/api/stats');
                const s = await res.json();
                const el = document.getElementById("miniStats");
                el.innerHTML = `
                    <div class="mini-stat-item">Flights today: <strong>${s.flights_today}</strong></div>
                    <div class="mini-stat-item">Unique aircraft today: <strong>${s.unique_today}</strong></div>
                    <div class="mini-stat-item">Top airline today: <strong>${s.top_airline_today || "—"}</strong></div>
                    <div class="mini-stat-item">Total logged: <strong>${s.total_flights}</strong></div>
                `;
            } catch (e) {}
        }

        function onSortChange(sel) {
            currentSort = sel.value;
            renderFlights();
        }

        function onFilterChange(sel) {
            currentFilter = sel.value;
            renderFlights();
        }

        function headingToCardinal(deg) {
            if (deg == null) return "Unknown";
            const dirs = ["N","NE","E","SE","S","SW","W","NW"];
            const ix = Math.round(((deg % 360) / 45)) % 8;
            return dirs[ix];
        }

        async function openDetail(data) {
            const reg = encodeURIComponent(data.reg || "");
            const hex = encodeURIComponent(data.hex || "");
            if (!reg && !hex) return;

            const res = await fetch(`/api/flight_detail?reg=${reg}&hex=${hex}`);
            if (!res.ok) return;
            const f = await res.json();

            const modal = document.getElementById("modal");
            const body = document.getElementById("modalBody");
            modal.style.display = "flex";

            const seenFirst = parseSeen(f.first_seen);
            const seenLast = parseSeen(f.last_seen);

            const firstText = seenFirst
                ? formatClock(seenFirst) + " · " + formatDateShort(seenFirst)
                : (f.first_seen || "—");
            const lastText = seenLast
                ? formatClock(seenLast) + " · " + formatDateShort(seenLast)
                : (f.last_seen || "—");

            const headingDeg = f.heading_deg != null ? f.heading_deg : null;
            const headingLabel = headingDeg != null ? headingDeg.toFixed(0) + "° " + headingToCardinal(headingDeg) : "Unknown";

            const country = (f.country || "").trim() || "Unknown";
            const countryIso = (f.country_iso || "").trim();
            const flagUrl = countryFlagUrl(countryIso);

            let imgTag = `<img src="/planeimg/${f.hex}" class="plane-img-box">`;
            if (!f.hex) {
                imgTag = `<div class="plane-img-box">NO IMAGE AVAILABLE</div>`;
            }

            body.innerHTML = `
                <div class="modal-row"><span class="label">Callsign</span>${f.callsign || "UNKNOWN"}</div>
                <div class="modal-row"><span class="label">Registration</span>${f.reg || "—"}</div>
                <div class="modal-row"><span class="label">Hex</span>${f.hex || "—"}</div>
                <div class="modal-row"><span class="label">Country</span>
                    ${flagUrl ? `<img src="${flagUrl}" style="height:14px;vertical-align:middle;margin-right:4px;">` : ""}
                    ${country}
                </div>
                <div class="modal-row"><span class="label">Heading</span>${headingLabel}</div>
                <div class="modal-row"><span class="label">Times seen</span>${f.total_seen}</div>
                <div class="modal-row"><span class="label">First seen</span>${firstText}</div>
                <div class="modal-row"><span class="label">Last seen</span>${lastText}</div>

                <div style="margin-top:12px; text-align:center;">
                    ${imgTag}
                </div>
            `;
        }

        function closeModal() {
            const modal = document.getElementById("modal");
            modal.style.display = "none";
        }

        setInterval(() => {
            loadFlights();
            loadStats();
        }, 10000);

        window.onload = () => {
            loadFlights();
            loadStats();
        };

        if (window.EventSource) {
            try {
                const es = new EventSource('/events');
                es.onmessage = e => {
                    try {
                        const row = JSON.parse(e.data);
                        flightsData = flightsData.filter(r => r.id !== row.id);
                        flightsData.unshift(row);
                        flightsData = flightsData.slice(0, 150);
                        renderFlights();

                        const container = document.getElementById('cards');
                        const top = container.querySelector('.card');
                        if (top) {
                            top.classList.remove('latest-animate');
                            void top.offsetWidth;
                            top.classList.add('latest-animate');

                            const badge = top.querySelector('.latest-badge');
                            if (badge) {
                                badge.classList.remove('badge-pulse');
                                void badge.offsetWidth;
                                badge.classList.add('badge-pulse');
                                setTimeout(() => badge.classList.remove('badge-pulse'), 900);
                            }
                            setTimeout(() => top.classList.remove('latest-animate'), 1000);
                        }
                    } catch (err) {}
                };
                es.onerror = () => {};
            } catch (err) {}
        }
    </script>

</head>
<body>

<header>
    <div class="header-top">
        <div class="title">Flight-Pi Intelligence Log</div>
        <div class="status"><div class="dot" id="statusDot"></div>Online</div>
    </div>
    <div class="subheader">
        <div id="lastUpdate">Last update: —</div>
        <div class="subheader-right">
            <a href="/stats">View stats</a> ·
            <a href="/mil">MIL board</a>
        </div>
    </div>
    <div class="mini-stats" id="miniStats">
        <!-- Filled by JS -->
    </div>
</header>

<div class="controls">
    <div>
        <span class="controls-label">Sort</span>
        <select class="select" onchange="onSortChange(this)">
            <option value="latest">Latest</option>
            <option value="altitude">Altitude</option>
            <option value="distance">Distance</option>
        </select>
    </div>
    <div>
        <span class="controls-label">Filter</span>
        <select class="select" onchange="onFilterChange(this)">
            <option value="all">All</option>
            <option value="commercial">Commercial</option>
            <option value="private">Private</option>
            <option value="unknown">Unknown</option>
        </select>
    </div>
</div>

<div class="container" id="cards"></div>

<div class="modal-backdrop" id="modal" onclick="closeModal()">
    <div class="modal-card" onclick="event.stopPropagation()">
        <div class="modal-header">
            <div class="modal-title">Flight detail</div>
            <div class="modal-close" onclick="closeModal()">×</div>
        </div>
        <div id="modalBody" style="margin-top: 8px;"></div>
    </div>
</div>

</body>
</html>
"""

# ---------------------------------------------------
# /stats page
# ---------------------------------------------------
HTML_STATS = """
<!DOCTYPE html>
<html>
<head>
    <title>Flight-Pi Stats</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <link rel="icon" href="/favicon.ico" type="image/png">

    <style>
        body {
            background: #0b0e11;
            color: #d0d3d6;
            font-family: "Segoe UI", Roboto, Arial, sans-serif;
            margin: 0;
            padding: 0;
        }
        a {
            color: #42f5d7;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        header {
            background: #101417;
            padding: 12px 16px 10px 16px;
            border-bottom: 1px solid #1c2329;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .title {
            font-size: 20px;
            font-weight: 600;
            letter-spacing: 1px;
            text-transform: uppercase;
        }
        .back-link {
            font-size: 13px;
        }

        .container {
            padding: 14px 14px 20px 14px;
        }

        .section {
            background: #14181d;
            border: 1px solid #1f242a;
            border-radius: 6px;
            padding: 12px 14px;
            margin-bottom: 12px;
        }
        .section-title {
            font-size: 14px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            color: #9da8b2;
            margin-bottom: 6px;
        }

        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
        }
        th, td {
            padding: 4px 4px;
            border-bottom: 1px solid #222831;
        }
        th {
            text-align: left;
            color: #9da8b2;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
        }
        tr:last-child td {
            border-bottom: none;
        }

        .flag {
            height: 12px;
            margin-right: 4px;
            vertical-align: middle;
            border-radius: 2px;
        }

        @media (min-width: 900px) {
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
        }
    </style>

    <script>
        function countryFlagUrl(iso) {
            if (!iso) return null;
            return "https://flagcdn.com/w20/" + iso.toLowerCase() + ".png";
        }

        async function loadStatsPage() {
            const res = await fetch('/api/stats');
            const s = await res.json();

            document.getElementById("overallCounts").innerText =
                "Total flights logged: " + s.total_flights +
                " · Flights today: " + s.flights_today +
                " · Unique aircraft today: " + s.unique_today;

            const tAirlines = document.getElementById("topAirlinesBody");
            tAirlines.innerHTML = "";
            s.top_airlines.forEach((r, idx) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${idx + 1}</td>
                    <td>${r.airline_name}</td>
                    <td>${r.cnt}</td>
                `;
                tAirlines.appendChild(tr);
            });

            const tTypes = document.getElementById("topTypesBody");
            tTypes.innerHTML = "";
            s.top_types.forEach((r, idx) => {
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${idx + 1}</td>
                    <td>${r.aircraft_type}</td>
                    <td>${r.cnt}</td>
                `;
                tTypes.appendChild(tr);
            });

            const tCountries = document.getElementById("topCountriesBody");
            tCountries.innerHTML = "";
            s.top_countries.forEach((r, idx) => {
                const iso = r.country_iso || "";
                const flag = countryFlagUrl(iso);
                const name = r.country || iso || "Unknown";
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${idx + 1}</td>
                    <td>
                        ${flag ? `<img src="${flag}" class="flag">` : ""}
                        ${name}
                    </td>
                    <td>${r.cnt}</td>
                `;
                tCountries.appendChild(tr);
            });

            const tAircraft = document.getElementById("topAircraftBody");
            tAircraft.innerHTML = "";
            s.top_aircraft.forEach((r, idx) => {
                const iso = r.country_iso || "";
                const flag = countryFlagUrl(iso);
                const country = r.country || iso || "";
                const id = r.reg || r.hex || r.key_id;
                const airline = r.airline_name || "";
                const tr = document.createElement("tr");
                tr.innerHTML = `
                    <td>${idx + 1}</td>
                    <td>${id}</td>
                    <td>${airline}</td>
                    <td>
                        ${flag ? `<img src="${flag}" class="flag">` : ""}
                        ${country}
                    </td>
                    <td>${r.cnt}</td>
                    <td>${r.first_seen}</td>
                    <td>${r.last_seen}</td>
                `;
                tAircraft.appendChild(tr);
            });
        }

        window.onload = loadStatsPage;
    </script>
</head>
<body>

<header>
    <div class="title">Flight-Pi Stats</div>
    <div class="back-link"><a href="/">← Back to live log</a></div>
</header>

<div class="container">
    <div class="section">
        <div class="section-title">Overview</div>
        <div id="overallCounts">Loading…</div>
    </div>

    <div class="section">
        <div class="section-title">Top Airlines (all time)</div>
        <table>
            <thead>
                <tr><th>#</th><th>Airline</th><th>Count</th></tr>
            </thead>
            <tbody id="topAirlinesBody"></tbody>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Top Aircraft Types</div>
        <table>
            <thead>
                <tr><th>#</th><th>Type</th><th>Count</th></tr>
            </thead>
            <tbody id="topTypesBody"></tbody>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Top Countries (by registration)</div>
        <table>
            <thead>
                <tr><th>#</th><th>Country</th><th>Count</th></tr>
            </thead>
            <tbody id="topCountriesBody"></tbody>
        </table>
    </div>

    <div class="section">
        <div class="section-title">Most-seen Aircraft</div>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Reg/Hex</th>
                    <th>Airline</th>
                    <th>Country</th>
                    <th>Seen</th>
                    <th>First seen</th>
                    <th>Last seen</th>
                </tr>
            </thead>
            <tbody id="topAircraftBody"></tbody>
        </table>
    </div>
</div>

</body>
</html>
"""

# ---------------------------------------------------
# MIL page
# ---------------------------------------------------
HTML_MIL = """
<!DOCTYPE html>
<html>
<head>
    <title>Flight-Pi MIL Board</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">

    <link rel="icon" href="/favicon.ico" type="image/png">

    <style>
        body {
            background: #050507;
            color: #e3e3e3;
            font-family: "Segoe UI", Roboto, Arial, sans-serif;
            margin: 0;
            padding: 0;
        }
        a {
            color: #ff4b6e;
            text-decoration: none;
        }
        a:hover {
            text-decoration: underline;
        }
        header {
            background: linear-gradient(90deg, #140208, #241018);
            padding: 12px 16px 10px 16px;
            border-bottom: 1px solid #3a121b;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .title {
            font-size: 20px;
            font-weight: 700;
            letter-spacing: 1.4px;
            text-transform: uppercase;
            color: #ff4b4b;
        }
        .subtitle {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.7px;
            color: #ffb3b3;
        }
        .links {
            font-size: 13px;
            text-align: right;
        }

        .toolbar {
            padding: 8px 14px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 1px solid #2a1015;
            background: #0c0508;
            font-size: 13px;
        }
        .toolbar-left {
            display: flex;
            gap: 10px;
            align-items: center;
        }
        .badge-count {
            background: #2a1015;
            border: 1px solid #ff4b4b;
            border-radius: 10px;
            padding: 3px 8px;
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
        }
        .pill {
            background: #151018;
            border-radius: 999px;
            padding: 3px 8px;
            border: 1px solid #362030;
            font-size: 11px;
        }

        .container {
            padding: 10px 12px 20px 12px;
        }

        .mil-card {
            background: #0f0b0f;
            border: 1px solid #39151f;
            border-radius: 6px;
            margin-bottom: 10px;
            padding: 10px 12px;
            cursor: pointer;
            box-shadow: 0 6px 20px rgba(0,0,0,0.35);
        }
        .mil-card:hover {
            border-color: #ff4b4b;
        }

        .mil-card-header {
            display: flex;
            justify-content: space-between;
            align-items: baseline;
            margin-bottom: 4px;
        }
        .mil-callsign {
            font-size: 17px;
            font-weight: 600;
            color: #ffe6e6;
        }
        .mil-hex {
            font-size: 12px;
            color: #ff9b9b;
        }
        .mil-tag {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.8px;
            background: rgba(255,75,75,0.08);
            border-radius: 999px;
            padding: 2px 8px;
            border: 1px solid rgba(255,75,75,0.4);
            color: #ffb3b3;
        }

        .mil-row {
            font-size: 13px;
            margin: 2px 0;
        }
        .mil-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: #ff9b9b;
            margin-right: 6px;
        }

        .status-dot {
            display: inline-block;
            width: 7px;
            height: 7px;
            border-radius: 50%;
            margin-right: 4px;
        }
        .status-dot-hot { background: #ff4b4b; }
        .status-dot-cold { background: #555; }

        .mil-footer {
            margin-top: 4px;
            font-size: 11px;
            color: #b07b7b;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .modal-backdrop {
            position: fixed;
            inset: 0;
            background: rgba(0, 0, 0, 0.7);
            display: none;
            align-items: center;
            justify-content: center;
            z-index: 1000;
        }
        .modal-card {
            background: #11080d;
            border: 1px solid #ff4b4b;
            border-radius: 8px;
            max-width: 420px;
            width: 90%;
            padding: 16px 18px 14px 18px;
            box-shadow: 0 16px 40px rgba(0,0,0,0.9);
        }
        .modal-header {
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .modal-title {
            font-size: 16px;
            font-weight: 600;
            color: #ffe6e6;
        }
        .modal-close {
            cursor: pointer;
            font-size: 18px;
            color: #ff9b9b;
        }
        .modal-row {
            margin: 4px 0;
            font-size: 13px;
        }
        .modal-label {
            font-size: 11px;
            text-transform: uppercase;
            letter-spacing: 0.6px;
            color: #ff9b9b;
            margin-right: 6px;
        }
        .intel-pending {
            font-size: 12px;
            color: #ffb3b3;
            margin-top: 6px;
        }

        .flag {
            height: 14px;
            margin-right: 4px;
            vertical-align: middle;
            border-radius: 2px;
        }

        .skeleton {
            background: linear-gradient(90deg, #1a1114 0px, #24151b 40px, #1a1114 80px);
            background-size: 200% 100%;
            animation: shimmer 1.4s infinite;
            border-radius: 4px;
            height: 10px;
            width: 100px;
            display: inline-block;
        }

        @keyframes shimmer {
            0% { background-position: -40px 0; }
            100% { background-position: 40px 0; }
        }

        @media (min-width: 900px) {
            .container {
                max-width: 800px;
                margin: 0 auto;
            }
        }
    </style>

    <script>
        let milData = [];
        let lastLoadedAt = null;

        function countryFlagUrl(iso) {
            if (!iso) return null;
            return "https://flagcdn.com/w20/" + iso.toLowerCase() + ".png";
        }

        function formatTimeAgoSeen(secs) {
            if (secs == null) return "Unknown";
            if (secs < 60) return secs.toFixed(1) + "s ago";
            const m = secs / 60;
            if (m < 60) return m.toFixed(1) + " min ago";
            const h = m / 60;
            return h.toFixed(1) + " hr ago";
        }

        function renderMil() {
            const container = document.getElementById("milCards");
            container.innerHTML = "";

            milData.forEach(ac => {
                const card = document.createElement("div");
                card.className = "mil-card";
                card.dataset.hex = ac.hex || "";

                const cs = (ac.flight || "").trim() || "UNKNOWN";
                const hex = ac.hex || "—";
                const alt = ac.alt_baro != null ? ac.alt_baro + " ft" : "Unknown";
                const squawk = ac.squawk || "—";
                const cat = ac.category || "—";
                const rssi = ac.rssi != null ? ac.rssi.toFixed(1) + " dB" : "Unknown";
                const seenAgo = ac.seen != null ? formatTimeAgoSeen(ac.seen) : "Unknown";
                const hot = ac.seen != null && ac.seen < 60;

                card.innerHTML = `
                    <div class="mil-card-header">
                        <div>
                            <div class="mil-callsign">${cs}</div>
                            <div class="mil-hex">HEX ${hex}</div>
                        </div>
                        <div class="mil-tag">
                            ${hot ? '<span class="status-dot status-dot-hot"></span>HOT' : '<span class="status-dot status-dot-cold"></span>STALE'}
                        </div>
                    </div>
                    <div class="mil-row">
                        <span class="mil-label">Altitude</span>${alt}
                    </div>
                    <div class="mil-row">
                        <span class="mil-label">Squawk</span>${squawk}
                    </div>
                    <div class="mil-row">
                        <span class="mil-label">Category</span>${cat}
                    </div>
                    <div class="mil-row">
                        <span class="mil-label">Signal</span>${rssi}
                    </div>
                    <div class="mil-footer">
                        <span><span class="mil-label">Seen</span>${seenAgo}</span>
                        <span class="pill">Tap for ADSBdb intel</span>
                    </div>
                `;

                card.addEventListener("click", () => openMilDetail(ac));
                container.appendChild(card);
            });

            document.getElementById("countLabel").textContent =
                milData.length + " aircraft in current /v2/mil frame";
        }

        async function loadMil() {
            const statusEl = document.getElementById("loadStatus");
            statusEl.textContent = "Loading current /v2/mil snapshot…";

            try {
                const res = await fetch("/api/mil");
                const data = await res.json();

                if (data.error) {
                    statusEl.textContent = "Error fetching /v2/mil: " + data.error;
                    milData = [];
                    renderMil();
                    return;
                }

                milData = data.aircraft || [];
                lastLoadedAt = new Date();
                statusEl.textContent = "Snapshot loaded " + lastLoadedAt.toLocaleTimeString();
                renderMil();
            } catch (e) {
                statusEl.textContent = "Error fetching /v2/mil.";
            }
        }

        function openModal() {
            document.getElementById("milModal").style.display = "flex";
        }

        function closeModal() {
            document.getElementById("milModal").style.display = "none";
        }

        async function openMilDetail(ac) {
            const hex = ac.hex || "";
            const modal = document.getElementById("milModal");
            const body = document.getElementById("milModalBody");

            modal.style.display = "flex";

            const cs = (ac.flight || "").trim() || "UNKNOWN";
            const alt = ac.alt_baro != null ? ac.alt_baro + " ft" : "Unknown";
            const squawk = ac.squawk || "—";
            const cat = ac.category || "—";
            const rssi = ac.rssi != null ? ac.rssi.toFixed(1) + " dB" : "Unknown";
            const seenAgo = ac.seen != null ? formatTimeAgoSeen(ac.seen) : "Unknown";

            body.innerHTML = `
                <div class="modal-row"><span class="modal-label">Callsign</span>${cs}</div>
                <div class="modal-row"><span class="modal-label">Hex</span>${hex || "—"}</div>
                <div class="modal-row"><span class="modal-label">Altitude</span>${alt}</div>
                <div class="modal-row"><span class="modal-label">Squawk</span>${squawk}</div>
                <div class="modal-row"><span class="modal-label">Category</span>${cat}</div>
                <div class="modal-row"><span class="modal-label">Signal</span>${rssi}</div>
                <div class="modal-row"><span class="modal-label">Seen</span>${seenAgo}</div>
                <div class="intel-pending">
                    ADSBdb intel: <span class="skeleton"></span>
                </div>
            `;

            if (!hex) {
                const intelDiv = document.createElement("div");
                intelDiv.className = "intel-pending";
                intelDiv.textContent = "No hex provided – ADSBdb lookup skipped.";
                body.appendChild(intelDiv);
                return;
            }

            try {
                const res = await fetch("/api/mil_detail?hex=" + encodeURIComponent(hex));
                const info = await res.json();

                const intelDivs = document.querySelectorAll(".intel-pending");
                intelDivs.forEach(d => d.remove());

                const countryIso = info.country_iso || null;
                const flag = countryFlagUrl(countryIso);
                const countryName = info.country_name || countryIso || "Unknown";

                const intelHtml = `
                    <div class="modal-row"><span class="modal-label">Type</span>${info.type || "Unknown"}</div>
                    <div class="modal-row"><span class="modal-label">ICAO Type</span>${info.icao_type || "Unknown"}</div>
                    <div class="modal-row"><span class="modal-label">Manufacturer</span>${info.manufacturer || "Unknown"}</div>
                    <div class="modal-row"><span class="modal-label">Owner</span>${info.owner || "Unknown"}</div>
                    <div class="modal-row"><span class="modal-label">Mode S</span>${info.mode_s || "Unknown"}</div>
                    <div class="modal-row">
                        <span class="modal-label">Country</span>
                        ${flag ? `<img src="${flag}" class="flag">` : ""}
                        ${countryName}
                    </div>
                `;
                body.insertAdjacentHTML("beforeend", intelHtml);
            } catch (e) {
                const intelDivs = document.querySelectorAll(".intel-pending");
                intelDivs.forEach(d => d.textContent = "ADSBdb lookup failed.");
            }
        }

        window.onload = () => {
            loadMil();
            setInterval(loadMil, 30000);
        };
    </script>
</head>
<body>

<header>
    <div>
        <div class="title">MIL INTEL BOARD</div>
        <div class="subtitle">Live /v2/mil snapshot · flightpi</div>
    </div>
    <div class="links">
        <div><a href="/">← Live log</a> · <a href="/stats">Stats</a></div>
    </div>
</header>

<div class="toolbar">
    <div class="toolbar-left">
        <div class="badge-count" id="countLabel">0 aircraft</div>
        <div class="pill">Source: adsb.lol /v2/mil</div>
    </div>
    <div id="loadStatus">Loading…</div>
</div>

<div class="container" id="milCards">
    <!-- Filled by JS -->
</div>

<div class="modal-backdrop" id="milModal" onclick="closeModal()">
    <div class="modal-card" onclick="event.stopPropagation()">
        <div class="modal-header">
            <div class="modal-title">MIL Aircraft Detail</div>
            <div class="modal-close" onclick="closeModal()">×</div>
        </div>
        <div id="milModalBody" style="margin-top: 8px;"></div>
    </div>
</div>

</body>
</html>
"""


@app.route("/")
def index():
    return render_template_string(HTML_MAIN)


@app.route("/stats")
def stats_page():
    return render_template_string(HTML_STATS)


@app.route("/mil")
def mil_page():
    return render_template_string(HTML_MIL)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080)
