"""
Rishi Bharadwaj Quantum Academy (RBQA) - backend
Flask + SQLite. Serves the public site, a JSON API and an admin panel.

Run:  python app.py      ->  http://localhost:5000
Admin: http://localhost:5000/admin
"""
import csv
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime, timezone
from functools import wraps

from flask import (Flask, Response, g, jsonify, render_template, request,
                   session)
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("RBQA_DB", os.path.join(BASE_DIR, "rbqa.db"))
SECRET_FILE = os.path.join(BASE_DIR, ".secret_key")

app = Flask(__name__, static_folder="static", template_folder="templates")


def _load_secret():
    """Persist a random secret key so admin sessions survive restarts."""
    env = os.environ.get("RBQA_SECRET")
    if env:
        return env
    if os.path.exists(SECRET_FILE):
        with open(SECRET_FILE) as f:
            return f.read().strip()
    key = secrets.token_hex(32)
    with open(SECRET_FILE, "w") as f:
        f.write(key)
    return key


app.config.update(
    SECRET_KEY=_load_secret(),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    MAX_CONTENT_LENGTH=64 * 1024,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# --------------------------------------------------------------------------
# Database
# --------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS admin (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    username TEXT NOT NULL,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    audience TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    topics TEXT NOT NULL DEFAULT '',          -- newline separated
    icon TEXT NOT NULL DEFAULT 'fa-atom',
    sort_order INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS workshops (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'Upcoming',  -- Upcoming / Planning / Completed
    date_label TEXT NOT NULL DEFAULT 'TBA',
    mode TEXT NOT NULL DEFAULT 'Online',
    tags TEXT NOT NULL DEFAULT '',            -- comma separated
    sort_order INTEGER NOT NULL DEFAULT 0,
    published INTEGER NOT NULL DEFAULT 1
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    interest TEXT NOT NULL DEFAULT '',
    message TEXT NOT NULL,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS registrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    workshop_id INTEGER NOT NULL REFERENCES workshops(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    phone TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);
"""

SEED_COURSES = [
    ("Fundamental Physics to Quantum Mechanics", "Class 11-12, UG, Educators",
     "Transition from classical to quantum physics. Covers Blackbody Radiation, Photoelectric Effect, Wave-Particle Duality, and Python simulations.",
     "Double-Slit Interference\nPlanck's Quantum Hypothesis\nIntro to Atomic Spectra", "fa-atom"),
    ("Introduction to Quantum Computing", "Beginner Friendly",
     "Learn the basics of qubits, superposition, and entanglement. Includes hands-on Python simulations and introduction to Qiskit.",
     "Classical vs Quantum Bits\nBasic Quantum Gates\nQuantum Circuits", "fa-microchip"),
    ("Quantum Communication & QKD", "Intermediate",
     "Understand principles of secure quantum communication, no-cloning theorem, and cryptographic protocols.",
     "The No-Cloning Principle\nBB84 Protocol\nSimulation-Based Learning", "fa-network-wired"),
    ("Introduction to Quantum Sensing", "Advanced",
     "Explore precision measurement techniques using atomic states. Discover NV-centers and Rydberg-atom based sensors.",
     "Classical vs Quantum Sensors\nAtomic Magnetometers\nNoise & Sensitivity", "fa-satellite-dish"),
    ("Intro to RF and Microwave Technologies", "Engineering & Research",
     "An introductory pathway for understanding instrumentation, S-parameters, and measurement vital for quantum systems.",
     "Network Analyzers\nLow-Noise Amplifiers\nCryogenic Considerations", "fa-wave-square"),
]

SEED_WORKSHOPS = [
    ("Python for Physics & Quantum Science",
     "An introductory workshop focusing on utilizing Python libraries to simulate classical physics experiments and basic quantum systems. Ideal for students transitioning into computational science.",
     "Upcoming", "TBA", "Online", "Beginner,Python"),
    ("Classroom Physics to Quantum Tech",
     "A comprehensive seminar bridging the gap between high-school/UG physics curriculum and modern quantum technology applications.",
     "Planning", "--", "Hybrid", "Core Physics"),
]


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def init_db():
    db = sqlite3.connect(DB_PATH)
    db.executescript(SCHEMA)
    cur = db.cursor()

    if cur.execute("SELECT COUNT(*) FROM admin").fetchone()[0] == 0:
        username = os.environ.get("RBQA_ADMIN_USER", "admin")
        password = os.environ.get("RBQA_ADMIN_PASS")
        generated = False
        if not password:
            password = "admin123"
            generated = True
        cur.execute("INSERT INTO admin (id, username, password_hash) VALUES (1, ?, ?)",
                    (username, generate_password_hash(password)))
        print(f"\n  Admin account created -> username: {username}  password: {password}")
        if generated:
            print("  (default password - change it from the admin panel after logging in)\n")

    if cur.execute("SELECT COUNT(*) FROM courses").fetchone()[0] == 0:
        for i, c in enumerate(SEED_COURSES):
            cur.execute("INSERT INTO courses (title, audience, description, topics, icon, sort_order) VALUES (?,?,?,?,?,?)",
                        (*c, i))
    if cur.execute("SELECT COUNT(*) FROM workshops").fetchone()[0] == 0:
        for i, w in enumerate(SEED_WORKSHOPS):
            cur.execute("INSERT INTO workshops (title, description, status, date_label, mode, tags, sort_order) VALUES (?,?,?,?,?,?,?)",
                        (*w, i))
    db.commit()
    db.close()


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def clean(value, maxlen):
    return str(value or "").strip()[:maxlen]


def bad(msg, code=400):
    return jsonify({"ok": False, "error": msg}), code


def admin_required(fn):
    @wraps(fn)
    def wrapper(*a, **kw):
        if not session.get("admin"):
            return bad("Authentication required.", 401)
        return fn(*a, **kw)
    return wrapper


def course_row(r):
    return {
        "id": r["id"], "title": r["title"], "audience": r["audience"],
        "description": r["description"],
        "topics": [t for t in r["topics"].split("\n") if t.strip()],
        "topics_text": r["topics"], "icon": r["icon"],
        "sort_order": r["sort_order"], "published": bool(r["published"]),
    }


def workshop_row(r):
    return {
        "id": r["id"], "title": r["title"], "description": r["description"],
        "status": r["status"], "date_label": r["date_label"], "mode": r["mode"],
        "tags": [t.strip() for t in r["tags"].split(",") if t.strip()],
        "tags_text": r["tags"], "sort_order": r["sort_order"],
        "published": bool(r["published"]),
    }


# Very small in-memory rate limiter for public form posts (per IP).
_hits = {}


def rate_limited(limit=8, window=600):
    ip = request.remote_addr or "?"
    now = datetime.now().timestamp()
    bucket = [t for t in _hits.get(ip, []) if now - t < window]
    if len(bucket) >= limit:
        _hits[ip] = bucket
        return True
    bucket.append(now)
    _hits[ip] = bucket
    return False


# --------------------------------------------------------------------------
# Pages
# --------------------------------------------------------------------------
@app.get("/")
def index():
    return app.send_static_file("index.html")


@app.get("/admin")
def admin_page():
    return render_template("admin.html")


@app.get("/api/health")
def health():
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------
@app.get("/api/courses")
def public_courses():
    rows = get_db().execute(
        "SELECT * FROM courses WHERE published = 1 ORDER BY sort_order, id").fetchall()
    return jsonify([course_row(r) for r in rows])


@app.get("/api/workshops")
def public_workshops():
    rows = get_db().execute(
        "SELECT * FROM workshops WHERE published = 1 ORDER BY sort_order, id").fetchall()
    return jsonify([workshop_row(r) for r in rows])


@app.post("/api/contact")
def contact():
    if rate_limited():
        return bad("Too many submissions. Please try again later.", 429)
    d = request.get_json(silent=True) or {}
    name, email = clean(d.get("name"), 120), clean(d.get("email"), 200)
    phone, interest = clean(d.get("phone"), 30), clean(d.get("interest"), 40)
    message = clean(d.get("message"), 3000)
    if not name or not message:
        return bad("Name and message are required.")
    if not EMAIL_RE.match(email):
        return bad("Please enter a valid email address.")
    db = get_db()
    db.execute("INSERT INTO messages (name,email,phone,interest,message,created_at) VALUES (?,?,?,?,?,?)",
               (name, email, phone, interest, message, now_iso()))
    db.commit()
    return jsonify({"ok": True, "message": "Thank you! We have received your message and will get back to you soon."})


@app.post("/api/workshops/<int:wid>/register")
def register(wid):
    if rate_limited():
        return bad("Too many submissions. Please try again later.", 429)
    db = get_db()
    ws = db.execute("SELECT * FROM workshops WHERE id = ? AND published = 1", (wid,)).fetchone()
    if not ws:
        return bad("Workshop not found.", 404)
    if ws["status"] == "Completed":
        return bad("Registration for this workshop is closed.")
    d = request.get_json(silent=True) or {}
    name, email = clean(d.get("name"), 120), clean(d.get("email"), 200)
    phone, note = clean(d.get("phone"), 30), clean(d.get("note"), 1000)
    if not name:
        return bad("Name is required.")
    if not EMAIL_RE.match(email):
        return bad("Please enter a valid email address.")
    dup = db.execute("SELECT 1 FROM registrations WHERE workshop_id=? AND lower(email)=lower(?)",
                     (wid, email)).fetchone()
    if dup:
        return bad("This email is already registered for this workshop.", 409)
    db.execute("INSERT INTO registrations (workshop_id,name,email,phone,note,created_at) VALUES (?,?,?,?,?,?)",
               (wid, name, email, phone, note, now_iso()))
    db.commit()
    return jsonify({"ok": True, "message": f"You're on the list for \"{ws['title']}\". We'll email you the details."})


# --------------------------------------------------------------------------
# Admin API
# --------------------------------------------------------------------------
@app.post("/api/admin/login")
def admin_login():
    if rate_limited(limit=10, window=300):
        return bad("Too many attempts. Wait a few minutes.", 429)
    d = request.get_json(silent=True) or {}
    row = get_db().execute("SELECT * FROM admin WHERE id = 1").fetchone()
    if (row and d.get("username") == row["username"]
            and check_password_hash(row["password_hash"], d.get("password") or "")):
        session.clear()
        session["admin"] = True
        return jsonify({"ok": True, "username": row["username"]})
    return bad("Invalid username or password.", 401)


@app.post("/api/admin/logout")
def admin_logout():
    session.clear()
    return jsonify({"ok": True})


@app.get("/api/admin/me")
def admin_me():
    if not session.get("admin"):
        return jsonify({"ok": False}), 401
    row = get_db().execute("SELECT username FROM admin WHERE id = 1").fetchone()
    return jsonify({"ok": True, "username": row["username"]})


@app.post("/api/admin/password")
@admin_required
def admin_password():
    d = request.get_json(silent=True) or {}
    db = get_db()
    row = db.execute("SELECT * FROM admin WHERE id = 1").fetchone()
    if not check_password_hash(row["password_hash"], d.get("current") or ""):
        return bad("Current password is incorrect.", 403)
    new = d.get("new") or ""
    if len(new) < 8:
        return bad("New password must be at least 8 characters.")
    db.execute("UPDATE admin SET password_hash = ? WHERE id = 1", (generate_password_hash(new),))
    db.commit()
    return jsonify({"ok": True})


@app.get("/api/admin/stats")
@admin_required
def admin_stats():
    db = get_db()
    q = lambda sql: db.execute(sql).fetchone()[0]
    return jsonify({
        "messages": q("SELECT COUNT(*) FROM messages"),
        "unread": q("SELECT COUNT(*) FROM messages WHERE is_read = 0"),
        "registrations": q("SELECT COUNT(*) FROM registrations"),
        "courses": q("SELECT COUNT(*) FROM courses"),
        "workshops": q("SELECT COUNT(*) FROM workshops"),
    })


# ---- messages
@app.get("/api/admin/messages")
@admin_required
def admin_messages():
    rows = get_db().execute("SELECT * FROM messages ORDER BY id DESC").fetchall()
    return jsonify([dict(r) | {"is_read": bool(r["is_read"])} for r in rows])


@app.patch("/api/admin/messages/<int:mid>")
@admin_required
def admin_message_update(mid):
    d = request.get_json(silent=True) or {}
    db = get_db()
    db.execute("UPDATE messages SET is_read = ? WHERE id = ?", (1 if d.get("is_read") else 0, mid))
    db.commit()
    return jsonify({"ok": True})


@app.delete("/api/admin/messages/<int:mid>")
@admin_required
def admin_message_delete(mid):
    db = get_db()
    db.execute("DELETE FROM messages WHERE id = ?", (mid,))
    db.commit()
    return jsonify({"ok": True})


# ---- registrations
@app.get("/api/admin/registrations")
@admin_required
def admin_registrations():
    rows = get_db().execute("""
        SELECT r.*, w.title AS workshop_title FROM registrations r
        JOIN workshops w ON w.id = r.workshop_id ORDER BY r.id DESC""").fetchall()
    return jsonify([dict(r) for r in rows])


@app.delete("/api/admin/registrations/<int:rid>")
@admin_required
def admin_registration_delete(rid):
    db = get_db()
    db.execute("DELETE FROM registrations WHERE id = ?", (rid,))
    db.commit()
    return jsonify({"ok": True})


def csv_response(filename, header, rows):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        # neutralise spreadsheet formula injection
        w.writerow([("'" + v) if isinstance(v, str) and v and v[0] in "=+-@" else v for v in r])
    return Response("﻿" + buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


@app.get("/api/admin/export/<kind>")
@admin_required
def admin_export(kind):
    db = get_db()
    if kind == "messages":
        rows = db.execute("SELECT created_at,name,email,phone,interest,message FROM messages ORDER BY id DESC").fetchall()
        return csv_response("messages.csv", ["Received (UTC)", "Name", "Email", "Phone", "Interest", "Message"], rows)
    if kind == "registrations":
        rows = db.execute("""SELECT r.created_at, w.title, r.name, r.email, r.phone, r.note
                             FROM registrations r JOIN workshops w ON w.id = r.workshop_id
                             ORDER BY r.id DESC""").fetchall()
        return csv_response("registrations.csv", ["Registered (UTC)", "Workshop", "Name", "Email", "Phone", "Note"], rows)
    return bad("Unknown export.", 404)


# ---- courses
def course_payload(d):
    title = clean(d.get("title"), 200)
    if not title:
        return None, "Title is required."
    try:
        order = int(d.get("sort_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return (title, clean(d.get("audience"), 200), clean(d.get("description"), 2000),
            clean(d.get("topics_text"), 2000), clean(d.get("icon"), 40) or "fa-atom",
            order, 1 if d.get("published", True) else 0), None


@app.get("/api/admin/courses")
@admin_required
def admin_courses():
    rows = get_db().execute("SELECT * FROM courses ORDER BY sort_order, id").fetchall()
    return jsonify([course_row(r) for r in rows])


@app.post("/api/admin/courses")
@admin_required
def admin_course_create():
    p, err = course_payload(request.get_json(silent=True) or {})
    if err:
        return bad(err)
    db = get_db()
    cur = db.execute("INSERT INTO courses (title,audience,description,topics,icon,sort_order,published) VALUES (?,?,?,?,?,?,?)", p)
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid}), 201


@app.put("/api/admin/courses/<int:cid>")
@admin_required
def admin_course_update(cid):
    p, err = course_payload(request.get_json(silent=True) or {})
    if err:
        return bad(err)
    db = get_db()
    cur = db.execute("UPDATE courses SET title=?,audience=?,description=?,topics=?,icon=?,sort_order=?,published=? WHERE id=?", (*p, cid))
    db.commit()
    return jsonify({"ok": True}) if cur.rowcount else bad("Not found.", 404)


@app.delete("/api/admin/courses/<int:cid>")
@admin_required
def admin_course_delete(cid):
    db = get_db()
    db.execute("DELETE FROM courses WHERE id = ?", (cid,))
    db.commit()
    return jsonify({"ok": True})


# ---- workshops
def workshop_payload(d):
    title = clean(d.get("title"), 200)
    if not title:
        return None, "Title is required."
    status = clean(d.get("status"), 20)
    if status not in ("Upcoming", "Planning", "Completed"):
        return None, "Status must be Upcoming, Planning or Completed."
    try:
        order = int(d.get("sort_order") or 0)
    except (TypeError, ValueError):
        order = 0
    return (title, clean(d.get("description"), 2000), status,
            clean(d.get("date_label"), 40) or "TBA", clean(d.get("mode"), 20) or "Online",
            clean(d.get("tags_text"), 200), order, 1 if d.get("published", True) else 0), None


@app.get("/api/admin/workshops")
@admin_required
def admin_workshops():
    rows = get_db().execute("SELECT * FROM workshops ORDER BY sort_order, id").fetchall()
    return jsonify([workshop_row(r) for r in rows])


@app.post("/api/admin/workshops")
@admin_required
def admin_workshop_create():
    p, err = workshop_payload(request.get_json(silent=True) or {})
    if err:
        return bad(err)
    db = get_db()
    cur = db.execute("INSERT INTO workshops (title,description,status,date_label,mode,tags,sort_order,published) VALUES (?,?,?,?,?,?,?,?)", p)
    db.commit()
    return jsonify({"ok": True, "id": cur.lastrowid}), 201


@app.put("/api/admin/workshops/<int:wid>")
@admin_required
def admin_workshop_update(wid):
    p, err = workshop_payload(request.get_json(silent=True) or {})
    if err:
        return bad(err)
    db = get_db()
    cur = db.execute("UPDATE workshops SET title=?,description=?,status=?,date_label=?,mode=?,tags=?,sort_order=?,published=? WHERE id=?", (*p, wid))
    db.commit()
    return jsonify({"ok": True}) if cur.rowcount else bad("Not found.", 404)


@app.delete("/api/admin/workshops/<int:wid>")
@admin_required
def admin_workshop_delete(wid):
    db = get_db()
    db.execute("DELETE FROM workshops WHERE id = ?", (wid,))
    db.commit()
    return jsonify({"ok": True})


# --------------------------------------------------------------------------
init_db()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print(f"  Site:  http://localhost:{port}")
    print(f"  Admin: http://localhost:{port}/admin")
    app.run(host="127.0.0.1", port=port, debug=False)
