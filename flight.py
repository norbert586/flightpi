#!/usr/bin/python3
# -*- coding: UTF-8 -*-

import os, time, json
from datetime import datetime, timedelta
from typing import Optional, Dict, Any
import requests
from PIL import Image, ImageDraw, ImageFont
from lib import LCD_2inch  # Waveshare driver in ./lib
from flight_logger import log_flight

# =======================
# CONFIG
# =======================
ME_LAT = 42.7077
ME_LON = -83.0315
RADIUS_NM = 50
TIMEOUT_S = 10
REFRESH_S = 12
ROTATE_DEG = 180

BASE = os.path.expanduser("~/flight-display")
CACHE_DIR = os.path.join(BASE, "cache")
CACHE_AIRCRAFT = os.path.join(CACHE_DIR, "aircraft")
CACHE_CALLSIGN = os.path.join(CACHE_DIR, "callsign")
CACHE_PHOTOS = os.path.join(CACHE_DIR, "photos")
for d in (CACHE_DIR, CACHE_AIRCRAFT, CACHE_CALLSIGN, CACHE_PHOTOS):
    os.makedirs(d, exist_ok=True)

TTL_AIRCRAFT_DAYS = 7
TTL_CALLSIGN_HOURS = 2
TTL_NO_PHOTO_HOURS = 12

# Layout
SIDE = 8
HEADER_H = 38
ROUTE_H  = 36
PHOTO_H  = 160
GAP = 6

# Colors tuned to roughly match the web UI
COL_BG     = (10, 12, 16)   # page background
COL_CARD   = (23, 26, 32)   # main card
COL_HDR    = (32, 36, 44)   # header bar
COL_TEXT   = (233, 238, 243)
COL_MUTED  = (148, 156, 168)
COL_ACCENT = (0, 214, 178)  # teal accent pill
COL_ARROW  = (245, 75, 75)  # route arrow
COL_TRACK  = (80, 85, 96)   # faint line
COL_FRAME  = (18, 20, 24)   # photo frame
COL_ERROR  = (120, 20, 20)

# Fonts (tuned for 240×320)
def load_fonts():
    try:
        f_xl   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)  # callsign
        f_iata = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)  # IATA codes
        f_md   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)  # medium
        f_sm   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)  # small
        f_ti   = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)  # tiny
    except Exception:
        f_xl = f_iata = f_md = f_sm = f_ti = ImageFont.load_default()
    return f_xl, f_iata, f_md, f_sm, f_ti

F_XL, F_IATA, F_MD, F_SM, F_TI = load_fonts()

# ===== helpers =====
def now_str():
    return datetime.now().strftime("%I:%M:%S %p").lstrip("0")

def tb(d: ImageDraw.ImageDraw, text: str, font):
    x0, y0, x1, y1 = d.textbbox((0, 0), text, font=font)
    return (x1-x0, y1-y0)

def textlen(d, text, font):
    return int(d.textlength(text, font=font))

def file_is_fresh(path: str, max_age: timedelta) -> bool:
    if not os.path.exists(path):
        return False
    mtime = datetime.fromtimestamp(os.path.getmtime(path))
    return (datetime.now() - mtime) <= max_age

def read_json(path: str):
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception:
        return None

def write_json(path: str, obj):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(obj, f)
    os.replace(tmp, path)

def download_image(url: str, path: str, timeout: int = 6) -> bool:
    """Download image from URL with robust error handling."""
    if not url or not isinstance(url, str):
        return False
    try:
        r = requests.get(url, timeout=timeout, stream=True)
        r.raise_for_status()
        tmp = path + ".part"
        # Clean up any existing partial download
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(16384):
                if chunk:
                    f.write(chunk)
        # Verify file has some content
        if os.path.getsize(tmp) < 100:  # Less than 100 bytes is likely corrupt
            os.remove(tmp)
            return False
        os.replace(tmp, path)
        return True
    except Exception:
        # Clean up partial file on error
        tmp = path + ".part"
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except Exception:
                pass
        return False

def truncate_text(d, text, font, max_w):
    """Truncate text to fit max_w with ellipsis if needed."""
    text = (text or "").strip()
    if not text:
        return ""
    if d.textlength(text, font=font) <= max_w:
        return text
    # Binary search for optimal length
    while text and d.textlength(text + "…", font=font) > max_w:
        text = text[:-1]
    return (text + "…") if text else "…"

def wrap_lines(d, text, font, max_w, max_lines):
    words = (text or "").split()
    if not words:
        return []
    lines, cur = [], words[0]
    for w in words[1:]:
        t = cur + " " + w
        if d.textlength(t, font=font) <= max_w:
            cur = t
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    if len(lines) > max_lines:
        keep = lines[:max_lines]
        last = keep[-1]
        while d.textlength(last + "…", font=font) > max_w and last:
            last = last[:-1]
        keep[-1] = (last + "…") if last else "…"
        return keep
    return lines

def autofit_text(d, text, max_w, base_size, min_size=16):
    """Find largest font size that fits text within max_w."""
    text = (text or "").strip()
    if not text:
        return F_LG
    size = base_size
    while size >= min_size:
        try:
            f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", size)
        except Exception:
            f = F_LG
        if d.textlength(text, font=f) <= max_w:
            return f
        size -= 1
    return F_LG

# ===== network =====
def fetch_nearest(lat, lon, radius_nm):
    """Fetch nearest aircraft with robust error handling."""
    try:
        url = f"https://api.adsb.lol/v2/closest/{lat}/{lon}/{radius_nm}"
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict) and "ac" in data and data["ac"]:
            return data["ac"][0]
        if isinstance(data, dict) and data.get("lat") is not None:
            return data
        return None
    except Exception:
        return None

def fetch_adsbdb_aircraft(reg: str):
    reg = (reg or "").strip().upper()
    p = os.path.join(CACHE_AIRCRAFT, f"{reg}.json")
    if file_is_fresh(p, timedelta(days=TTL_AIRCRAFT_DAYS)):
        return read_json(p)
    url = f"https://api.adsbdb.com/v0/aircraft/{reg}"
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        obj = r.json()
        write_json(p, obj)
        return obj
    except Exception:
        return read_json(p) if os.path.exists(p) else None

def fetch_adsbdb_callsign(call: str):
    call = (call or "").strip().upper()
    p = os.path.join(CACHE_CALLSIGN, f"{call}.json")
    if file_is_fresh(p, timedelta(hours=TTL_CALLSIGN_HOURS)):
        return read_json(p)
    url = f"https://api.adsbdb.com/v0/callsign/{call}"
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        obj = r.json()
        write_json(p, obj)
        return obj
    except Exception:
        return read_json(p) if os.path.exists(p) else None

def fetch_planespotters_photo_by_reg(reg: str):
    """Fetch photo from planespotters.net API using registration."""
    reg = (reg or "").strip().upper()
    if not reg:
        return None
    img_path = os.path.join(CACHE_PHOTOS, f"ps_{reg}.jpg")
    none_flag = os.path.join(CACHE_PHOTOS, f"ps_{reg}.none")
    if os.path.exists(img_path):
        return img_path
    if file_is_fresh(none_flag, timedelta(hours=TTL_NO_PHOTO_HOURS)):
        return None
    url = f"https://api.planespotters.net/pub/photos/reg/{reg}"
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        obj = r.json()
        # API returns {"photos": [{"thumbnail": {...}, "thumbnail_large": {...}, ...}]}
        if obj and isinstance(obj, dict) and "photos" in obj and obj["photos"]:
            photo = obj["photos"][0]
            # Prefer thumbnail_large, fallback to thumbnail or other fields
            img_url = None
            for key in ("thumbnail_large", "thumbnail", "link"):
                if isinstance(photo.get(key), dict):
                    img_url = photo[key].get("src")
                    if img_url:
                        break
                elif isinstance(photo.get(key), str):
                    img_url = photo[key]
                    break
            if img_url and download_image(img_url, img_path, timeout=8):
                return img_path
        open(none_flag, "w").close()
        return None
    except Exception:
        open(none_flag, "w").close()
        return None

def fetch_airportdata_thumb_by_hex(hex_str: str):
    hex_str = (hex_str or "").strip().upper()
    if not hex_str:
        return None
    img_path = os.path.join(CACHE_PHOTOS, f"{hex_str}.jpg")
    none_flag = os.path.join(CACHE_PHOTOS, f"{hex_str}.none")
    if os.path.exists(img_path):
        return img_path
    if file_is_fresh(none_flag, timedelta(hours=TTL_NO_PHOTO_HOURS)):
        return None
    url = f"https://airport-data.com/api/ac_thumb.json?m={hex_str}&n=1"
    try:
        r = requests.get(url, timeout=TIMEOUT_S)
        r.raise_for_status()
        obj = r.json()
        if obj.get("status") == 200 and obj.get("count", 0) > 0:
            img_url = obj["data"][0]["image"]
            if download_image(img_url, img_path, timeout=8):
                return img_path
        open(none_flag, "w").close()
        return None
    except Exception:
        open(none_flag, "w").close()
        return None

def fetch_aircraft_photo(hex_str: str, reg: str):
    """Try multiple sources to fetch aircraft photo. Returns path or None."""
    # Try airport-data.com first (by hex)
    if hex_str:
        photo = fetch_airportdata_thumb_by_hex(hex_str)
        if photo:
            return photo
    # Try planespotters.net second (by registration)
    if reg:
        photo = fetch_planespotters_photo_by_reg(reg)
        if photo:
            return photo
    return None

# ===== drawing =====
def draw_header(d, card, callsign, type_code):
    hdr = [card[0], card[1], card[2], card[1] + HEADER_H]
    d.rectangle(hdr, fill=COL_HDR)

    # Type pill (right side)
    pill_txt = truncate_text(d, (type_code or "—").upper(), F_SM, 50)
    tw = textlen(d, pill_txt, F_SM)
    th = tb(d, pill_txt, F_SM)[1]
    pad = 6
    pill = [
        card[2] - tw - 2 * pad - 10,
        hdr[1] + (HEADER_H - th) // 2 - 3,
        card[2] - 10,
        hdr[1] + (HEADER_H - th) // 2 + th + 3,
    ]
    d.rounded_rectangle(pill, radius=9, fill=COL_ACCENT)
    d.text((pill[0] + pad, pill[1] + 3), pill_txt, font=F_SM, fill=(0, 0, 0))

    # Callsign (left side) - truncate to avoid overlapping pill
    max_callsign_w = pill[0] - card[0] - 16
    callsign_txt = truncate_text(d, callsign, F_XL, max_callsign_w)
    cs_y = card[1] + (HEADER_H - tb(d, callsign_txt, F_XL)[1]) // 2
    d.text((card[0] + 8, cs_y), callsign_txt, font=F_XL, fill=COL_TEXT)

    return hdr

def draw_card(disp, callsign, type_code, model, route, photo_path, reg, airline_name):
    W, H = disp.height, disp.width  # 240×320 portrait
    img = Image.new("RGB", (W, H), COL_BG)
    d = ImageDraw.Draw(img)
    card = [SIDE, SIDE, W - SIDE, H - SIDE]
    d.rectangle(card, fill=COL_CARD)

    callsign     = (callsign or "Unknown").strip() or "Unknown"
    type_code    = (type_code or "—").strip() or "—"
    model        = (model or type_code).strip()
    reg          = (reg or "—").strip() or "—"
    airline_name = (airline_name or "").strip()

    inner_l = card[0] + 10
    inner_r = card[2] - 10
    inner_w = inner_r - inner_l

    # ── Header ──────────────────────────────────────────────
    draw_header(d, card, callsign, type_code)
    y = card[1] + HEADER_H + GAP

    # ── Route ───────────────────────────────────────────────
    o_iata = d_iata = ""
    if route and route.get("response", {}).get("flightroute"):
        fr   = route["response"]["flightroute"]
        orig = fr.get("origin") or {}
        dest = fr.get("destination") or {}
        o_iata = (orig.get("iata_code") or "").strip().upper()
        d_iata = (dest.get("iata_code") or "").strip().upper()

    cy = y + ROUTE_H // 2  # vertical centre of route strip
    if o_iata and d_iata:
        ow = textlen(d, o_iata, F_IATA)
        dw = textlen(d, d_iata, F_IATA)
        ih = tb(d, o_iata, F_IATA)[1]
        ty_iata = cy - ih // 2

        d.text((inner_l, ty_iata), o_iata, font=F_IATA, fill=(245, 220, 225))
        d.text((inner_r - dw, ty_iata), d_iata, font=F_IATA, fill=(245, 220, 225))

        # Plane icon centred on the strip
        plane = "✈"
        mid_x = (inner_l + inner_r) // 2
        pw = textlen(d, plane, F_MD)
        px = mid_x - pw // 2
        py = cy - tb(d, plane, F_MD)[1] // 2
        d.text((px, py), plane, font=F_MD, fill=COL_TEXT)

        # Track lines either side of plane
        line_l = inner_l + ow + 5
        line_r = inner_r - dw - 5
        d.line([(line_l, cy), (px - 3, cy)], fill=COL_TRACK, width=2)
        arr_start = px + pw + 3
        d.line([(arr_start, cy), (line_r - 7, cy)], fill=COL_ARROW, width=2)
        d.polygon([(line_r, cy), (line_r - 7, cy - 3), (line_r - 7, cy + 3)], fill=COL_ARROW)
    else:
        msg = "Route unavailable"
        mx = inner_l + (inner_w - textlen(d, msg, F_SM)) // 2
        d.text((mx, cy - tb(d, msg, F_SM)[1] // 2), msg, font=F_SM, fill=COL_MUTED)

    y += ROUTE_H + 5

    # ── Photo (full card width, dominant) ───────────────────
    px1, py1 = inner_l, y
    px2, py2 = inner_r, y + PHOTO_H
    d.rectangle([px1, py1, px2, py2], fill=COL_FRAME)
    if photo_path and os.path.exists(photo_path):
        try:
            ph = Image.open(photo_path).convert("RGB")
            p_w = px2 - px1
            scale = min(p_w / ph.width, PHOTO_H / ph.height)
            tw, th = int(ph.width * scale), int(ph.height * scale)
            ph = ph.resize((tw, th), Image.LANCZOS)
            img.paste(ph, (px1 + (p_w - tw) // 2, py1 + (PHOTO_H - th) // 2))
        except Exception:
            pass  # leave dark frame on error
    else:
        msg = "No photo"
        d.text(
            (px1 + ((px2 - px1) - textlen(d, msg, F_SM)) // 2,
             py1 + (PHOTO_H - tb(d, msg, F_SM)[1]) // 2),
            msg, font=F_SM, fill=COL_MUTED,
        )

    y = py2 + 7

    # ── Info lines ───────────────────────────────────────────
    d.text((inner_l, y), truncate_text(d, model, F_SM, inner_w), font=F_SM, fill=COL_TEXT)
    y += 16

    if airline_name:
        d.text((inner_l, y), truncate_text(d, airline_name, F_TI, inner_w), font=F_TI, fill=COL_MUTED)

    # ── Footer: reg left, time right ─────────────────────────
    fy = card[3] - 18
    d.text((inner_l, fy), f"Reg {reg}", font=F_TI, fill=COL_MUTED)
    time_txt = now_str()
    d.text((inner_r - textlen(d, time_txt, F_TI), fy), time_txt, font=F_TI, fill=COL_MUTED)

    img = img.rotate(ROTATE_DEG)
    disp.ShowImage(img)

def draw_loading(disp, msg="Loading Flight-Pi…", tick=0):
    W, H = disp.height, disp.width
    img = Image.new("RGB", (W, H), COL_BG)
    d = ImageDraw.Draw(img)
    cx = (W - textlen(d, msg, F_XL)) // 2
    d.text((cx, 48), msg, font=F_XL, fill=COL_TEXT)
    # spinner
    r = 18
    cx0, cy0 = W//2, 110
    start = (tick * 18) % 360
    d.arc([cx0-r, cy0-r, cx0+r, cy0+r], start=start, end=start+270, fill=COL_ACCENT, width=3)
    img = img.rotate(ROTATE_DEG)
    disp.ShowImage(img)

def draw_error(disp, err):
    W, H = disp.height, disp.width
    img = Image.new("RGB", (W, H), COL_ERROR)
    d = ImageDraw.Draw(img)
    title = "Error"
    cx = (W - textlen(d, title, F_XL)) // 2
    d.text((cx, 44), title, font=F_XL, fill=COL_TEXT)
    msg = (err or "")[:64]
    cx = (W - textlen(d, msg, F_SM)) // 2
    d.text((cx, 80), msg, font=F_SM, fill=COL_TEXT)
    t = now_str()
    cx = (W - textlen(d, t, F_SM)) // 2
    d.text((cx, 100), t, font=F_SM, fill=COL_TEXT)
    img = img.rotate(ROTATE_DEG)
    disp.ShowImage(img)

# ===== main =====
def main():
    disp = LCD_2inch.LCD_2inch()
    disp.Init()
    disp.clear()
    try:
        disp.bl_DutyCycle(80)
    except Exception:
        pass

    # startup spinner
    for i in range(6):
        draw_loading(disp, tick=i)
        time.sleep(0.25)

    last_key = None
    cached_aircraft = cached_route = None
    cached_photo_path = None

    while True:
        try:
            ac = fetch_nearest(ME_LAT, ME_LON, RADIUS_NM)
            if not ac:
                draw_loading(disp, msg="No aircraft nearby")
                time.sleep(REFRESH_S)
                continue

            reg      = (ac.get("r")      or "").strip().upper()
            callsign = (ac.get("flight") or "").strip().upper()
            type_cd  = (ac.get("t")      or "").strip().upper()
            hex_str  = (ac.get("hex")    or "").strip().upper()
            key = (reg, callsign, hex_str)

            if key != last_key:
                cached_aircraft   = fetch_adsbdb_aircraft(reg) if reg else None
                cached_route      = fetch_adsbdb_callsign(callsign) if callsign else None
                # Try both photo APIs (airport-data.com by hex, planespotters.net by reg)
                cached_photo_path = fetch_aircraft_photo(hex_str, reg)
                last_key = key

            model = manuf = country = owner = country_iso = ""
            if cached_aircraft and cached_aircraft.get("response", {}).get("aircraft"):
                a = cached_aircraft["response"]["aircraft"]
                model       = (a.get("type") or a.get("icao_type") or "").strip()
                manuf       = (a.get("manufacturer") or "").strip()
                country     = (a.get("registered_owner_country_name") or "").strip()
                country_iso = (a.get("registered_owner_country_iso_name") or "").strip()
                owner       = (a.get("registered_owner") or "").strip()

            airline_name = None
            origin_iata = origin_name = None
            dest_iata = dest_name = None
            if cached_route and cached_route.get("response", {}).get("flightroute"):
                fr = cached_route["response"]["flightroute"]
                airline = fr.get("airline") or {}
                airline_name = airline.get("name")
                orig = fr.get("origin") or {}
                dest = fr.get("destination") or {}
                origin_iata = orig.get("iata_code")
                origin_name = orig.get("name") or orig.get("municipality")
                dest_iata = dest.get("iata_code")
                dest_name = dest.get("name") or dest.get("municipality")

            # --- log into SQLite for the web app ---
            now_iso = datetime.now().isoformat(timespec="seconds")
            row = {
                "seen_at": now_iso,
                "hex": hex_str or None,
                "reg": reg or None,
                "callsign": callsign or None,
                "type_code": type_cd or None,
                "model": model or None,
                "manufacturer": manuf or None,
                "country": country or None,
                "country_iso": country_iso or None,
                "owner": owner or None,
                "airline_name": airline_name,
                "origin_iata": origin_iata,
                "origin_name": origin_name,
                "dest_iata": dest_iata,
                "dest_name": dest_name,
                "altitude_ft": ac.get("alt_baro"),
                "ground_speed_kt": ac.get("gs"),
                "distance_nm": ac.get("dst"),
                "heading_deg": ac.get("track"),
            }
            try:
                log_flight(row)
            except Exception:
                # DB issues should never kill the display loop
                pass

            # --- draw main card on the LCD ---
            draw_card(
                disp=disp,
                callsign=callsign or "Unknown",
                type_code=type_cd or "—",
                model=model,
                route=cached_route,
                photo_path=cached_photo_path,
                reg=reg or "—",
                airline_name=airline_name,
            )

        except KeyboardInterrupt:
            break
        except Exception as e:
            # show error but keep running
            draw_error(disp, str(e))

        time.sleep(REFRESH_S)

    try:
        disp.module_exit()
    except Exception:
        pass

if __name__ == "__main__":
    main()
