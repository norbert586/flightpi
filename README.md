# FlightPi — Real-Time Aircraft Overhead Tracker  
FlightPi is a Raspberry-Pi powered aircraft proximity detection and logging system that visually displays the nearest aircraft overhead on a 2-inch Waveshare LCD and simultaneously logs flight events to a local SQLite database and exposes them over a web-based viewer on your LAN.

---

## ✈️ Features

### Real-Time Flight Display (on Pi LCD)
- Shows nearest aircraft within 50nm
- Displays:
  - Callsign
  - Aircraft model/type
  - Route (origin → destination)
  - Airline name (if available)
  - Registration
  - Aircraft photo (if available)
  - Owner & country of registration
- Auto-rotated for portrait LCD orientation
- Smooth UI with wrapping & fitting text

---

### Web Application (browser UI)
Hosted at:  

http://<pi-ip>:5000


Displays:
- Full historical log of flights (event grouped)
- Filters & sorting
- Flight statistics dashboard
- “Current flight” indicator
- Flags for country of registration

---

### Event-Smart Flight Logging  
Uses an **event model** so the same plane circling overhead doesn’t create dozens of duplicate entries.

- First detection → new event
- Re-detection within 20 minutes:
  - `times_seen` increments
  - `last_seen` updates
- Stores altitude, speed, distance, direction etc.

---

## 📡 APIs Used

| Source | Purpose | URL |
|--------|---------|-----|
| ADSB.lol | Nearest aircraft | https://api.adsb.lol |
| ADSBdb | Aircraft registry lookup | https://api.adsbdb.com |
| ADSBdb | Route & airline lookup | https://api.adsbdb.com |
| Airport-Data.com | Aircraft image thumbnails | https://airport-data.com |
| FlagCDN | Country flags | https://flagcdn.com |


---

## 🧠 Architecture

flight.py → main display loop
flight_logger.py → event-aware database insertion
flight_log.db → SQLite datastore
web_server.py → Flask-based REST API + frontend
cache/ → cached API responses + images
lib/LCD_2inch.py → LCD hardware driver


---

## 🗄 Database Schema

flights(
id INTEGER,
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
times_seen INTEGER
)


---


## 🔁 Boot Services

### Display Service

sudo systemctl enable flight-display
sudo systemctl restart flight-display


### Web UI Service

sudo systemctl enable flight-web
sudo systemctl restart flight-web


---

## 🧩 Version Control Strategy

### Branch Structure

| Branch | Purpose |
|--------|---------|
| main | stable production |
| dev | experimental features |

Workflow example:

git checkout dev
work happens here

git commit
git checkout main
git merge dev
git push


---

## 🧪 Future Enhancements

- Current-flight badge on web UI  
- More country & aircraft metadata  
- Export to CSV  
- Export to JSON  
- Flight persistence analytics  
- UI polish for LCD + Web  

---

## License  
MIT  
