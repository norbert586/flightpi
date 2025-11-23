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
HEADER_H = 42
FOOTER_H = 20
GAP = 6

# Left/right panel bounds
PHOTO_MAX_W = 138
PHOTO_MAX_H = 108
FACTS_GAP_Y = 2

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
        f_xl = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 22)  # callsign
        f_lg = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)  # IATA
        f_md = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14)  # airport names
        f_sm = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 12)  # facts
        f_ti = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 11)  # tiny / labels
    except Exception:
        f_xl = f_lg = f_md = f_sm = f_ti = ImageFont.load_default()
    return f_xl, f_lg, f_md, f_sm, f_ti

F_XL, F_LG, F_MD, F_SM, F_TI = load_fonts()

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
    try:
        r = requests.get(url, timeout=timeout, stream=True)
        r.raise_for_status()
        tmp = path + ".part"
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(16384):
                if chunk:
                    f.write(chunk)
        os.replace(tmp, path)
        return True
    except Exception:
        return False

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
    url = f"https://api.adsb.lol/v2/closest/{lat}/{lon}/{radius_nm}"
    r = requests.get(url, timeout=TIMEOUT_S)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "ac" in data and data["ac"]:
        return data["ac"][0]
    if isinstance(data, dict) and data.get("lat") is not None:
        return data
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

# ===== drawing =====
def draw_header(d, card, callsign, type_code):
    hdr = [card[0], card[1], card[2], card[1] + HEADER_H]
    d.rectangle(hdr, fill=COL_HDR)
    # big callsign
    d.text((card[0] + 8, card[1] + 9), callsign, font=F_XL, fill=COL_TEXT)
    # type pill
    pill_txt = (type_code or "—").upper()
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
    return hdr

def draw_route(d, card, y, o_iata, d_iata, o_name, d_name):
    left_x = card[0] + 10
    right_x = card[2] - 10
    mid_x = (left_x + right_x) // 2

    max_w_side = (right_x - left_x - 44) // 2
    f_left = autofit_text(d, o_iata, max_w_side, base_size=30, min_size=18)
    f_right = autofit_text(d, d_iata, max_w_side, base_size=30, min_size=18)

    lh = tb(d, o_iata, f_left)[1]
    rh = tb(d, d_iata, f_right)[1]
    top = y

    # IATA codes
    d.text((left_x, top), o_iata, font=f_left, fill=(245, 220, 225))
    rx = right_x - textlen(d, d_iata, f_right)
    d.text((rx, top), d_iata, font=f_right, fill=(245, 220, 225))

    # Flight path
    h = max(lh, rh)
    cy = top + h // 2 + 1
    track_l = left_x + textlen(d, o_iata, f_left) + 8
    track_r = right_x - textlen(d, d_iata, f_right) - 8
    track_l = min(track_l, mid_x - 20)
    track_r = max(track_r, mid_x + 20)

    d.line([(track_l, cy), (track_r, cy)], fill=COL_TRACK, width=3)

    ax = track_r
    aw = 9
    d.polygon([(ax, cy), (ax - aw, cy - 4), (ax - aw, cy + 4)], fill=COL_ARROW)
    d.line([(mid_x - 16, cy), (ax - aw - 2, cy)], fill=COL_ARROW, width=3)

    plane = "✈"
    pf = F_MD
    px = mid_x - textlen(d, plane, pf) // 2
    py = cy - tb(d, plane, pf)[1] // 2 - 1
    d.text((px, py), plane, font=pf, fill=COL_TEXT)

    y = top + h + 2

    # Wrapped airport names
    half_w = max_w_side
    left_lines = wrap_lines(d, o_name, F_MD, half_w, max_lines=2)
    right_lines = wrap_lines(d, d_name, F_MD, half_w, max_lines=2)
    ly = y
    ry = y
    for line in left_lines:
        d.text((left_x, ly), line, font=F_MD, fill=COL_MUTED)
        ly += 14
    for line in right_lines:
        d.text((right_x - textlen(d, line, F_MD), ry), line, font=F_MD, fill=COL_MUTED)
        ry += 14
    return max(ly, ry) + 4

def draw_facts_block(d, x, y, w, facts):
    """Right-hand fact block under the photo."""
    label_w = int(w * 0.44)
    value_w = w - label_w
    for label, value in facts:
        val = value.strip() if value else "—"
        l_lines = wrap_lines(d, label + ":", F_TI, label_w, max_lines=2)
        v_lines = wrap_lines(d, val, F_SM, value_w, max_lines=2)
        lines = max(len(l_lines), len(v_lines))
        for i in range(lines):
            l_txt = l_lines[i] if i < len(l_lines) else ""
            v_txt = v_lines[i] if i < len(v_lines) else ""
            d.text((x, y), l_txt, font=F_TI, fill=COL_MUTED)
            d.text((x + label_w + 6, y - 1), v_txt, font=F_SM, fill=COL_TEXT)
            y += 12
        y += FACTS_GAP_Y
    return y

def draw_card(disp, callsign, type_code, model, manufacturer, country, owner, route, photo_path, reg):
    W, H = disp.height, disp.width  # 240×320 portrait
    img = Image.new("RGB", (W, H), COL_BG)
    d = ImageDraw.Draw(img)
    card = [SIDE, SIDE, W - SIDE, H - SIDE]
    d.rectangle(card, fill=COL_CARD)

    # Header (flight + pill)
    hdr = draw_header(d, card, callsign, type_code)
    y = hdr[3] + 5

    # Route block
    o_iata = d_iata = o_name = d_name = ""
    if route and route.get("response", {}).get("flightroute"):
        fr   = route["response"]["flightroute"]
        orig = fr.get("origin") or {}
        dest = fr.get("destination") or {}
        o_iata = (orig.get("iata_code") or "").upper()
        d_iata = (dest.get("iata_code") or "").upper()
        o_name = orig.get("name") or orig.get("municipality") or ""
        d_name = dest.get("name") or dest.get("municipality") or ""
    if o_iata and d_iata:
        y = draw_route(d, card, y, o_iata, d_iata, o_name, d_name)
    else:
        msg = "Route unavailable"
        cx = card[0] + (card[2]-card[0]-textlen(d, msg, F_MD))//2
        d.text((cx, y), msg, font=F_MD, fill=COL_MUTED)
        y += 18

    # Two columns: photo left, facts right
    left_x  = card[0] + 8
    right_x = card[2] - 8
    mid_gap = 6
    photo_w = PHOTO_MAX_W
    facts_x = left_x + photo_w + mid_gap
    facts_w = right_x - facts_x

    # Photo
    d.rectangle([left_x, y, left_x + photo_w, y + PHOTO_MAX_H], fill=COL_FRAME)
    if photo_path and os.path.exists(photo_path):
        try:
            ph = Image.open(photo_path).convert("RGB")
            scale = min(photo_w / ph.width, PHOTO_MAX_H / ph.height)
            tw, th = int(ph.width * scale), int(ph.height * scale)
            ph = ph.resize((tw, th), Image.LANCZOS)
            px = left_x + (photo_w - tw)//2
            py = y + (PHOTO_MAX_H - th)//2
            img.paste(ph, (px, py))
        except Exception:
            msg = "No photo"
            tx = left_x + (photo_w - textlen(d, msg, F_SM))//2
            ty = y + (PHOTO_MAX_H - tb(d, msg, F_SM)[1])//2
            d.text((tx, ty), msg, font=F_SM, fill=COL_MUTED)
    else:
        msg = "No photo"
        tx = left_x + (photo_w - textlen(d, msg, F_SM))//2
        ty = y + (PHOTO_MAX_H - tb(d, msg, F_SM)[1])//2
        d.text((tx, ty), msg, font=F_SM, fill=COL_MUTED)

    # Facts on the right
    facts = [
        ("Type",           (model or "").strip() or type_code or ""),
        ("Manufacturer",   (manufacturer or "").strip()),
        ("Country Registered", (country or "").strip()),
        ("Registered owner",   (owner or "").strip()),
    ]
    _ = draw_facts_block(d, facts_x, y, facts_w, facts)

    # Footer
    foot = f"Reg {reg or '—'}    Updated {now_str()}"
    fx = card[0] + (card[2]-card[0]-textlen(d, foot, F_SM))//2
    fy = card[3] - FOOTER_H
    d.text((fx, fy), foot, font=F_SM, fill=COL_MUTED)

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
                cached_photo_path = fetch_airportdata_thumb_by_hex(hex_str) if hex_str else None
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
                manufacturer=manuf,
                country=country,
                owner=owner,
                route=cached_route,
                photo_path=cached_photo_path,
                reg=reg or "—",
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
