"""Kenya weather forecast: web server.

Serves the public dashboards, the staff portal, and a JSON API backed by a SQLite database.
Staff log in to add weather warnings, county advisories, station readings, forecast
corrections, and to upload new KMD forecast files. The Open-Meteo forecast is refreshed
automatically at 07:30 and 19:30 EAT.

Start it with:   python app.py          (see README.md)
"""
import csv
import io
import json
import os
import secrets
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

ROOT = Path(__file__).resolve().parent
PUBLIC = ROOT / "public"
STAFF = ROOT / "staff"
INSTANCE = ROOT / "instance"
DB_PATH = INSTANCE / "forecast.db"
KMD_FILE = ROOT / "data" / "kmd" / "7day.txt"
EAT = timezone(timedelta(hours=3))
UPDATE_TIMES = [(7, 30), (19, 30)]           # when the Open-Meteo forecast is refreshed (EAT)
MAX_UPLOAD_MB = 60

COUNTIES = [c["n"] for c in json.loads((ROOT / "data" / "shapes.json").read_text())["counties"]]
PARAMS = {"rain": "Rainfall (mm)", "tmax": "Maximum temperature (°C)", "tmin": "Minimum temperature (°C)"}
LEVELS = ["advisory", "watch", "warning"]

INSTANCE.mkdir(exist_ok=True)


def secret_key():
    f = INSTANCE / "secret.key"
    if not f.exists():
        f.write_text(secrets.token_hex(32))
        try:
            os.chmod(f, 0o600)
        except OSError:
            pass
    return f.read_text().strip()


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=secret_key(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("KF_HTTPS", "0") == "1",   # set KF_HTTPS=1 once served over https
    PERMANENT_SESSION_LIFETIME=timedelta(hours=12),
    MAX_CONTENT_LENGTH=MAX_UPLOAD_MB * 1024 * 1024,
)

# ---------------------------------------------------------------- database

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY, username TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK (role IN ('admin','staff')),
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, last_login TEXT);
CREATE TABLE IF NOT EXISTS warnings (
  id INTEGER PRIMARY KEY, title TEXT NOT NULL, level TEXT NOT NULL, counties TEXT NOT NULL,
  message TEXT NOT NULL, starts_at TEXT NOT NULL, ends_at TEXT NOT NULL,
  created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS advisories (
  id INTEGER PRIMARY KEY, county TEXT NOT NULL, valid_from TEXT NOT NULL, valid_to TEXT NOT NULL,
  summary TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stations (
  id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL, county TEXT NOT NULL,
  lat REAL NOT NULL, lon REAL NOT NULL, created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS readings (
  id INTEGER PRIMARY KEY, station_id INTEGER NOT NULL REFERENCES stations(id) ON DELETE CASCADE,
  date TEXT NOT NULL, rain_mm REAL, tmax REAL, tmin REAL, notes TEXT,
  created_by INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE (station_id, date));
CREATE TABLE IF NOT EXISTS corrections (
  id INTEGER PRIMARY KEY, county TEXT NOT NULL, date TEXT NOT NULL, parameter TEXT NOT NULL,
  value REAL NOT NULL, reason TEXT NOT NULL, created_by INTEGER, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, UNIQUE (county, date, parameter));
CREATE TABLE IF NOT EXISTS audit (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL, user_id INTEGER, username TEXT,
  action TEXT NOT NULL, entity TEXT NOT NULL, entity_id INTEGER, detail TEXT);
"""


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.commit()
    conn.close()


def now_iso():
    return datetime.now(EAT).isoformat(timespec="seconds")


def rows(sql, args=()):
    return [dict(r) for r in db().execute(sql, args).fetchall()]


def audit(action, entity, entity_id=None, detail=None):
    u = current_user()
    db().execute("INSERT INTO audit (at, user_id, username, action, entity, entity_id, detail) VALUES (?,?,?,?,?,?,?)",
                 (now_iso(), u["id"] if u else None, u["username"] if u else None, action, entity, entity_id,
                  json.dumps(detail, ensure_ascii=False) if detail is not None else None))

# ---------------------------------------------------------------- login and permissions

_failed = {}          # (ip, username) -> [count, first_failure_time]
LOCK_AFTER, LOCK_SECONDS = 5, 300


def current_user():
    uid = session.get("uid")
    if not uid:
        return None
    if "user" not in g:
        r = db().execute("SELECT id, username, name, role, active FROM users WHERE id = ?", (uid,)).fetchone()
        g.user = dict(r) if r and r["active"] else None
    return g.user


def login_required(role=None):
    def deco(fn):
        @wraps(fn)
        def wrapper(*a, **kw):
            u = current_user()
            if not u:
                return jsonify(error="Please log in."), 401
            if role == "admin" and u["role"] != "admin":
                return jsonify(error="Only administrators can do this."), 403
            if request.method not in ("GET", "HEAD"):
                # the staff portal sends this token with every change; other sites cannot read it
                if request.headers.get("X-CSRF-Token") != session.get("csrf"):
                    return jsonify(error="Your session has expired. Please refresh the page."), 403
            return fn(*a, **kw)
        return wrapper
    return deco


@app.post("/api/login")
def login():
    data = request.get_json(silent=True) or {}
    username = str(data.get("username", "")).strip().lower()
    password = str(data.get("password", ""))
    key = (request.remote_addr, username)
    count, first = _failed.get(key, (0, 0))
    if count >= LOCK_AFTER and time.time() - first < LOCK_SECONDS:
        return jsonify(error="Too many attempts. Please wait 5 minutes and try again."), 429
    r = db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
    if not r or not r["active"] or not check_password_hash(r["password_hash"], password):
        if time.time() - first >= LOCK_SECONDS:
            count, first = 0, time.time()
        _failed[key] = (count + 1, first)
        return jsonify(error="Wrong username or password."), 401
    _failed.pop(key, None)
    session.clear()
    session.permanent = True
    session["uid"] = r["id"]
    session["csrf"] = secrets.token_urlsafe(24)
    db().execute("UPDATE users SET last_login = ? WHERE id = ?", (now_iso(), r["id"]))
    db().commit()
    return jsonify(ok=True)


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    u = current_user()
    if not u:
        return jsonify(user=None)
    return jsonify(user=u, csrf=session.get("csrf"))


@app.post("/api/me/password")
@login_required()
def change_own_password():
    data = request.get_json(silent=True) or {}
    r = db().execute("SELECT password_hash FROM users WHERE id = ?", (current_user()["id"],)).fetchone()
    if not check_password_hash(r["password_hash"], str(data.get("current", ""))):
        return jsonify(error="Your current password is not correct."), 400
    new = str(data.get("new", ""))
    if len(new) < 8:
        return jsonify(error="The new password must be at least 8 characters."), 400
    db().execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(new), current_user()["id"]))
    audit("change password", "users", current_user()["id"])
    db().commit()
    return jsonify(ok=True)

# ---------------------------------------------------------------- validation helpers


class Invalid(Exception):
    pass


@app.errorhandler(Invalid)
def invalid(e):
    return jsonify(error=str(e)), 400


@app.errorhandler(413)
def too_big(_e):
    return jsonify(error=f"That file is too large. The limit is {MAX_UPLOAD_MB} MB."), 413


def text(data, key, label, required=True, max_len=2000):
    v = str(data.get(key) or "").strip()
    if required and not v:
        raise Invalid(f"Please fill in {label}.")
    if len(v) > max_len:
        raise Invalid(f"{label} is too long (maximum {max_len} characters).")
    return v


def number(data, key, label, required=False, lo=None, hi=None):
    v = data.get(key)
    if v in (None, ""):
        if required:
            raise Invalid(f"Please fill in {label}.")
        return None
    try:
        v = float(v)
    except (TypeError, ValueError):
        raise Invalid(f"{label} must be a number.")
    if (lo is not None and v < lo) or (hi is not None and v > hi):
        raise Invalid(f"{label} must be between {lo} and {hi}.")
    return v


def date(data, key, label):
    v = text(data, key, label, max_len=10)
    try:
        datetime.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise Invalid(f"{label} must be a date like 2026-10-24.")
    return v


def when(data, key, label):
    v = text(data, key, label, max_len=25)
    try:
        dt = datetime.fromisoformat(v)
    except ValueError:
        raise Invalid(f"{label} must be a date and time.")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=EAT)
    return dt.isoformat(timespec="minutes")


def county(data, key="county", allow_all=False):
    v = text(data, key, "the county", max_len=60)
    if allow_all and v == "ALL":
        return v
    if v not in COUNTIES:
        raise Invalid(f"“{v}” is not a Kenyan county.")
    return v

# ---------------------------------------------------------------- resources (create, list, edit, delete)


def clean_warning(d):
    lvl = text(d, "level", "the level")
    if lvl not in LEVELS:
        raise Invalid("Level must be advisory, watch or warning.")
    cs = d.get("counties") or []
    if isinstance(cs, str):
        cs = [c.strip() for c in cs.split(",") if c.strip()]
    if not cs:
        raise Invalid("Please choose at least one county, or All counties.")
    if "ALL" in cs:
        cs = ["ALL"]
    for c in cs:
        if c != "ALL" and c not in COUNTIES:
            raise Invalid(f"“{c}” is not a Kenyan county.")
    out = {"title": text(d, "title", "the title", max_len=120), "level": lvl, "counties": ",".join(cs),
           "message": text(d, "message", "the message", max_len=3000),
           "starts_at": when(d, "starts_at", "the start time"), "ends_at": when(d, "ends_at", "the end time")}
    if out["ends_at"] <= out["starts_at"]:
        raise Invalid("The end time must be after the start time.")
    return out


def clean_advisory(d):
    out = {"county": county(d, allow_all=True), "valid_from": date(d, "valid_from", "the first day"),
           "valid_to": date(d, "valid_to", "the last day"), "summary": text(d, "summary", "the advisory", max_len=3000)}
    if out["valid_to"] < out["valid_from"]:
        raise Invalid("The last day must be on or after the first day.")
    return out


def clean_station(d):
    return {"name": text(d, "name", "the station name", max_len=100), "county": county(d),
            "lat": number(d, "lat", "Latitude", True, -5.5, 5.5), "lon": number(d, "lon", "Longitude", True, 33.5, 42.5)}


def clean_reading(d):
    sid = number(d, "station_id", "the station", True)
    if not db().execute("SELECT 1 FROM stations WHERE id = ?", (int(sid),)).fetchone():
        raise Invalid("Please choose a station from the list.")
    out = {"station_id": int(sid), "date": date(d, "date", "the date"),
           "rain_mm": number(d, "rain_mm", "Rainfall", False, 0, 1000),
           "tmax": number(d, "tmax", "Maximum temperature", False, -20, 55),
           "tmin": number(d, "tmin", "Minimum temperature", False, -20, 55),
           "notes": text(d, "notes", "notes", required=False, max_len=500)}
    if out["rain_mm"] is None and out["tmax"] is None and out["tmin"] is None:
        raise Invalid("Please enter at least one reading: rainfall, maximum or minimum temperature.")
    if out["tmax"] is not None and out["tmin"] is not None and out["tmin"] > out["tmax"]:
        raise Invalid("The minimum temperature cannot be higher than the maximum.")
    return out


def clean_correction(d):
    p = text(d, "parameter", "what to correct")
    if p not in PARAMS:
        raise Invalid("Please choose rainfall, maximum or minimum temperature.")
    lim = (0, 1000) if p == "rain" else (-20, 55)
    return {"county": county(d), "date": date(d, "date", "the date"), "parameter": p,
            "value": number(d, "value", "The corrected value", True, *lim),
            "reason": text(d, "reason", "the reason", max_len=500)}


RESOURCES = {
    "warnings": (clean_warning, "SELECT * FROM warnings ORDER BY starts_at DESC"),
    "advisories": (clean_advisory, "SELECT * FROM advisories ORDER BY valid_from DESC, county"),
    "stations": (clean_station, "SELECT * FROM stations ORDER BY county, name"),
    "readings": (clean_reading, "SELECT r.*, s.name AS station, s.county FROM readings r JOIN stations s ON s.id = r.station_id ORDER BY r.date DESC, s.name LIMIT 1000"),
    "corrections": (clean_correction, "SELECT * FROM corrections ORDER BY date DESC, county"),
}


def resource_or_404(name):
    if name not in RESOURCES:
        abort(404)
    return RESOURCES[name]


@app.get("/api/<name>")
@login_required()
def list_items(name):
    _clean, sql = resource_or_404(name)
    return jsonify(items=rows(sql))


@app.post("/api/<name>")
@login_required()
def create_item(name):
    clean, _ = resource_or_404(name)
    data = clean(request.get_json(silent=True) or {})
    stamp = now_iso()
    data.update(created_by=current_user()["id"], created_at=stamp, updated_at=stamp)
    cols = ", ".join(data)
    try:
        cur = db().execute(f"INSERT INTO {name} ({cols}) VALUES ({', '.join('?' * len(data))})", tuple(data.values()))
    except sqlite3.IntegrityError:
        raise Invalid(duplicate_message(name))
    audit("add", name, cur.lastrowid, data)
    db().commit()
    return jsonify(ok=True, id=cur.lastrowid)


@app.put("/api/<name>/<int:item_id>")
@login_required()
def update_item(name, item_id):
    clean, _ = resource_or_404(name)
    if not db().execute(f"SELECT 1 FROM {name} WHERE id = ?", (item_id,)).fetchone():
        abort(404)
    data = clean(request.get_json(silent=True) or {})
    data["updated_at"] = now_iso()
    try:
        db().execute(f"UPDATE {name} SET {', '.join(k + ' = ?' for k in data)} WHERE id = ?", (*data.values(), item_id))
    except sqlite3.IntegrityError:
        raise Invalid(duplicate_message(name))
    audit("edit", name, item_id, data)
    db().commit()
    return jsonify(ok=True)


@app.delete("/api/<name>/<int:item_id>")
@login_required()
def delete_item(name, item_id):
    resource_or_404(name)
    old = db().execute(f"SELECT * FROM {name} WHERE id = ?", (item_id,)).fetchone()
    if not old:
        abort(404)
    db().execute(f"DELETE FROM {name} WHERE id = ?", (item_id,))
    audit("delete", name, item_id, dict(old))
    db().commit()
    return jsonify(ok=True)


def duplicate_message(name):
    return {"stations": "A station with that name already exists.",
            "readings": "That station already has a reading for that date. Edit the existing one instead.",
            "corrections": "There is already a correction for that county, date and measure. Edit it instead."}.get(name, "That entry already exists.")

# ---------------------------------------------------------------- uploads


@app.post("/api/upload/readings")
@login_required()
def import_readings():
    """CSV with columns: station, date, rain_mm, tmax, tmin, notes (station = station name)."""
    f = request.files.get("file")
    if not f:
        raise Invalid("Please choose a CSV file.")
    try:
        content = f.read().decode("utf-8-sig")
    except UnicodeDecodeError:
        raise Invalid("The file must be a CSV saved as UTF-8 text.")
    reader = csv.DictReader(io.StringIO(content))
    missing = {"station", "date"} - set(h.strip().lower() for h in (reader.fieldnames or []))
    if missing:
        raise Invalid("The CSV needs these columns: station, date, rain_mm, tmax, tmin (notes is optional).")
    stations = {r["name"].lower(): r["id"] for r in rows("SELECT id, name FROM stations")}
    added, updated, errors, stamp = 0, 0, [], now_iso()
    for line, raw in enumerate(reader, start=2):
        d = {k.strip().lower(): (v or "").strip() for k, v in raw.items() if k}
        sid = stations.get(d.get("station", "").lower())
        if not sid:
            errors.append(f"Row {line}: no station called “{d.get('station', '')}”. Add it under Stations first.")
            continue
        d["station_id"] = sid
        try:
            r = clean_reading(d)
        except Invalid as e:
            errors.append(f"Row {line}: {e}")
            continue
        exists = db().execute("SELECT id FROM readings WHERE station_id = ? AND date = ?", (sid, r["date"])).fetchone()
        if exists:
            db().execute("UPDATE readings SET rain_mm=?, tmax=?, tmin=?, notes=?, updated_at=? WHERE id=?",
                         (r["rain_mm"], r["tmax"], r["tmin"], r["notes"], stamp, exists["id"]))
            updated += 1
        else:
            db().execute("INSERT INTO readings (station_id, date, rain_mm, tmax, tmin, notes, created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                         (sid, r["date"], r["rain_mm"], r["tmax"], r["tmin"], r["notes"], current_user()["id"], stamp, stamp))
            added += 1
    audit("import CSV", "readings", None, {"added": added, "updated": updated, "errors": len(errors)})
    db().commit()
    return jsonify(ok=True, added=added, updated=updated, errors=errors[:50])


@app.post("/api/upload/kmd")
@login_required()
def upload_kmd():
    f = request.files.get("file")
    if not f:
        raise Invalid("Please choose the KMD 7day.txt file.")
    head = f.stream.readline().decode("utf-8", "replace").split()
    need = {"lon", "lat", "rain", "min_temp", "max_temp"}
    if not need.issubset(head):
        raise Invalid("This doesn’t look like a KMD 7-day file. Its first line should list lon, lat, rain, min_temp and max_temp.")
    f.stream.seek(0)
    tmp = KMD_FILE.with_suffix(".upload")
    f.save(tmp)
    os.replace(tmp, KMD_FILE)
    audit("upload", "kmd", None, {"filename": f.filename})
    db().commit()
    run_job("kmd")
    return jsonify(ok=True, message="File received. The KMD page will update in about a minute.")

# ---------------------------------------------------------------- background jobs

JOBS = {"forecast": [sys.executable, str(ROOT / "scripts" / "update_forecast.py")],
        "kmd": [sys.executable, str(ROOT / "scripts" / "build_kmd.py")]}
job_state = {k: {"running": False, "last_run": None, "ok": None, "message": ""} for k in JOBS}
job_lock = threading.Lock()


def run_job(name):
    with job_lock:
        if job_state[name]["running"]:
            return False
        job_state[name]["running"] = True

    def work():
        started = now_iso()
        try:
            p = subprocess.run(JOBS[name], cwd=ROOT, capture_output=True, text=True, timeout=1800)
            ok, msg = p.returncode == 0, (p.stdout or p.stderr).strip().splitlines()[-1:] or [""]
        except Exception as e:
            ok, msg = False, [str(e)]
        job_state[name].update(running=False, last_run=started, ok=ok, message=msg[0][:300])
    threading.Thread(target=work, daemon=True).start()
    return True


def scheduler():
    done = set()
    while True:
        t = datetime.now(EAT)
        for h, m in UPDATE_TIMES:
            slot = (t.date(), h, m)
            if slot not in done and (t.hour, t.minute) >= (h, m) and (t.hour, t.minute) < (h, m + 30):
                done.add(slot)
                run_job("forecast")
        time.sleep(30)


@app.get("/api/status")
@login_required()
def status():
    return jsonify(jobs=job_state, update_times=[f"{h:02d}:{m:02d}" for h, m in UPDATE_TIMES])


@app.post("/api/refresh/<name>")
@login_required()
def refresh(name):
    if name not in JOBS:
        abort(404)
    started = run_job(name)
    audit("run update", name)
    db().commit()
    return jsonify(ok=True, started=started)

# ---------------------------------------------------------------- administrators: staff accounts and activity


@app.get("/api/users")
@login_required("admin")
def list_users():
    return jsonify(items=rows("SELECT id, username, name, role, active, created_at, last_login FROM users ORDER BY name"))


@app.post("/api/users")
@login_required("admin")
def create_user():
    d = request.get_json(silent=True) or {}
    username = text(d, "username", "the username", max_len=40).lower()
    if not username.replace(".", "").replace("_", "").replace("-", "").isalnum():
        raise Invalid("Usernames can only use letters, numbers, dots, dashes and underscores.")
    role = d.get("role", "staff")
    if role not in ("admin", "staff"):
        raise Invalid("Role must be admin or staff.")
    pw = str(d.get("password", ""))
    if len(pw) < 8:
        raise Invalid("The password must be at least 8 characters.")
    try:
        cur = db().execute("INSERT INTO users (username, name, password_hash, role, active, created_at) VALUES (?,?,?,?,1,?)",
                           (username, text(d, "name", "the full name", max_len=100), generate_password_hash(pw), role, now_iso()))
    except sqlite3.IntegrityError:
        raise Invalid("That username is already taken.")
    audit("add", "users", cur.lastrowid, {"username": username, "role": role})
    db().commit()
    return jsonify(ok=True, id=cur.lastrowid)


@app.put("/api/users/<int:uid>")
@login_required("admin")
def update_user(uid):
    d = request.get_json(silent=True) or {}
    u = db().execute("SELECT * FROM users WHERE id = ?", (uid,)).fetchone()
    if not u:
        abort(404)
    role = d.get("role", u["role"])
    active = 1 if d.get("active", u["active"]) in (1, True, "1", "true") else 0
    if uid == current_user()["id"] and (role != "admin" or not active):
        raise Invalid("You can’t remove your own administrator access.")
    if role not in ("admin", "staff"):
        raise Invalid("Role must be admin or staff.")
    name = text(d, "name", "the full name", max_len=100) if "name" in d else u["name"]
    db().execute("UPDATE users SET name = ?, role = ?, active = ? WHERE id = ?", (name, role, active, uid))
    if d.get("password"):
        if len(str(d["password"])) < 8:
            raise Invalid("The password must be at least 8 characters.")
        db().execute("UPDATE users SET password_hash = ? WHERE id = ?", (generate_password_hash(str(d["password"])), uid))
    audit("edit", "users", uid, {"name": name, "role": role, "active": active, "password_reset": bool(d.get("password"))})
    db().commit()
    return jsonify(ok=True)


@app.get("/api/audit")
@login_required("admin")
def list_audit():
    return jsonify(items=rows("SELECT * FROM audit ORDER BY id DESC LIMIT 500"))

# ---------------------------------------------------------------- public data for the dashboards


@app.get("/api/meta")
def meta():
    return jsonify(counties=sorted(COUNTIES), params=PARAMS, levels=LEVELS)


@app.get("/api/public")
def public_data():
    now = datetime.now(EAT).isoformat(timespec="minutes")
    today = datetime.now(EAT).date()
    since = (today - timedelta(days=7)).isoformat()
    out = {
        "generated": now_iso(),
        "warnings": rows("SELECT id, title, level, counties, message, starts_at, ends_at FROM warnings WHERE ends_at >= ? ORDER BY starts_at", (now,)),
        "advisories": rows("SELECT id, county, valid_from, valid_to, summary, updated_at FROM advisories WHERE valid_to >= ? ORDER BY valid_from", ((today - timedelta(days=1)).isoformat(),)),
        "corrections": rows("SELECT county, date, parameter, value, reason FROM corrections WHERE date >= ?", ((today - timedelta(days=2)).isoformat(),)),
        "stations": rows("SELECT id, name, county, lat, lon FROM stations ORDER BY name"),
        "readings": rows("SELECT station_id, date, rain_mm, tmax, tmin FROM readings WHERE date >= ? ORDER BY date", (since,)),
    }
    for w in out["warnings"]:
        w["counties"] = w["counties"].split(",")
    resp = jsonify(out)
    resp.headers["Cache-Control"] = "no-store"
    return resp

# ---------------------------------------------------------------- pages


@app.get("/")
def home():
    return send_from_directory(PUBLIC, "index.html")


@app.get("/staff")
@app.get("/staff/")
def staff_portal():
    return send_from_directory(STAFF, "index.html")


@app.get("/staff/<path:name>")
def staff_files(name):
    return send_from_directory(STAFF, name)


@app.get("/<path:name>")
def public_files(name):
    resp = send_from_directory(PUBLIC, name)
    if name.endswith(".js"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


def main():
    init_db()
    with sqlite3.connect(DB_PATH) as c:
        if not c.execute("SELECT 1 FROM users WHERE role = 'admin' AND active = 1").fetchone():
            print("\nNo administrator account yet. Create one first with:\n    python manage.py create-admin\n")
    threading.Thread(target=scheduler, daemon=True).start()
    port = int(os.environ.get("KF_PORT", "8000"))
    host = os.environ.get("KF_HOST", "0.0.0.0")
    try:
        from waitress import serve
        print(f"Kenya forecast server running on http://{host}:{port}  (staff portal: /staff)")
        serve(app, host=host, port=port, threads=8)
    except ImportError:
        app.run(host=host, port=port)


if __name__ == "__main__":
    main()
