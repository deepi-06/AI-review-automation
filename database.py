"""SQLite storage layer."""
import sqlite3
from contextlib import contextmanager

import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT NOT NULL,
    role TEXT, department TEXT, skills TEXT, experience TEXT,
    status TEXT DEFAULT 'Registered',
    final_status TEXT,
    final_approved INTEGER DEFAULT 0,
    approved_at TEXT,
    report_path TEXT,
    final_json TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS reviews (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id TEXT NOT NULL,
    review_no INTEGER NOT NULL,
    task_title TEXT, task_description TEXT, requirements TEXT, difficulty TEXT,
    task_json TEXT,
    deadline TEXT, review_datetime TEXT, meeting_link TEXT, event_id TEXT,
    status TEXT DEFAULT 'Assigned',
    submission_text TEXT, submission_files TEXT, submitted_at TEXT,
    score REAL, evaluation_json TEXT, ai_provider TEXT, evaluated_at TEXT,
    review_reminder_sent INTEGER DEFAULT 0,
    deadline_reminder_sent INTEGER DEFAULT 0,
    UNIQUE(candidate_id, review_no),
    FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL,
    is_read INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS email_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient TEXT, subject TEXT, status TEXT, error TEXT, sent_at TEXT
);
"""


@contextmanager
def db():
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(config.DB_PATH), timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def init_db():
    with db() as con:
        try:
            con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        con.executescript(SCHEMA)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


# ---------------- candidates ----------------
def upsert_candidates(rows):
    new = upd = 0
    with db() as con:
        for r in rows:
            vals = (r["name"], r["email"], r["role"], r["department"], r["skills"], r["experience"])
            cid = str(r["id"]).strip().upper()
            existing = con.execute(
                "SELECT id FROM candidates WHERE upper(id)=upper(?) ORDER BY CASE WHEN id=? THEN 0 ELSE 1 END, id LIMIT 1",
                (cid, cid),
            ).fetchone()
            if existing:
                con.execute("UPDATE candidates SET name=?,email=?,role=?,department=?,skills=?,experience=? WHERE id=?",
                            (*vals, existing["id"]))
                upd += 1
            else:
                con.execute("INSERT INTO candidates(name,email,role,department,skills,experience,id) VALUES(?,?,?,?,?,?,?)",
                            (*vals, cid))
                new += 1
    return new, upd


def list_candidates():
    with db() as con:
        return _rows(con.execute("SELECT * FROM candidates ORDER BY id"))


def get_candidate(cid):
    with db() as con:
        r = con.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
        return dict(r) if r else None


def update_candidate(cid, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k}=?" for k in fields)  # keys are internal constants, never user input
    with db() as con:
        con.execute(f"UPDATE candidates SET {cols} WHERE id=?", (*fields.values(), cid))


def candidate_login(email, cid):
    with db() as con:
        r = con.execute("SELECT * FROM candidates WHERE lower(email)=lower(?) AND upper(id)=upper(?)",
                        (email.strip(), cid.strip())).fetchone()
        return dict(r) if r else None


# ---------------- reviews ----------------
def get_review(cid, n):
    with db() as con:
        r = con.execute("SELECT * FROM reviews WHERE candidate_id=? AND review_no=?", (cid, n)).fetchone()
        return dict(r) if r else None


def list_reviews(cid=None):
    with db() as con:
        if cid:
            return _rows(con.execute("SELECT * FROM reviews WHERE candidate_id=? ORDER BY review_no", (cid,)))
        return _rows(con.execute("SELECT * FROM reviews ORDER BY candidate_id, review_no"))


def upsert_review(cid, n, **fields):
    with db() as con:
        con.execute("INSERT OR IGNORE INTO reviews(candidate_id, review_no) VALUES(?,?)", (cid, n))
        if fields:
            cols = ", ".join(f"{k}=?" for k in fields)
            con.execute(f"UPDATE reviews SET {cols} WHERE candidate_id=? AND review_no=?",
                        (*fields.values(), cid, n))


def reviews_needing_review_reminder(start, end):
    with db() as con:
        return _rows(con.execute(
            "SELECT * FROM reviews WHERE review_no>=1 AND review_reminder_sent=0 AND review_datetime IS NOT NULL "
            "AND review_datetime>=? AND review_datetime<=?", (start, end)))


def reviews_needing_deadline_reminder(start, end):
    with db() as con:
        return _rows(con.execute(
            "SELECT * FROM reviews WHERE review_no>=1 AND status='Assigned' AND deadline_reminder_sent=0 "
            "AND deadline IS NOT NULL AND deadline>=? AND deadline<=?", (start, end)))


# ---------------- notifications / email log ----------------
def add_notification(recipient, message):
    with db() as con:
        con.execute("INSERT INTO notifications(recipient,message,created_at) VALUES(?,?,?)",
                    (recipient, message, config.fmt(config.now())))


def get_notifications(recipient, limit=100):
    with db() as con:
        return _rows(con.execute("SELECT * FROM notifications WHERE recipient=? ORDER BY id DESC LIMIT ?",
                                 (recipient, limit)))


def unread_count(recipient):
    with db() as con:
        return con.execute("SELECT COUNT(*) FROM notifications WHERE recipient=? AND is_read=0",
                           (recipient,)).fetchone()[0]


def mark_all_read(recipient):
    with db() as con:
        con.execute("UPDATE notifications SET is_read=1 WHERE recipient=?", (recipient,))


def log_email(recipient, subject, status, error=""):
    with db() as con:
        con.execute("INSERT INTO email_log(recipient,subject,status,error,sent_at) VALUES(?,?,?,?,?)",
                    (recipient, subject, status, error, config.fmt(config.now())))


def list_email_log(limit=200):
    with db() as con:
        return _rows(con.execute("SELECT * FROM email_log ORDER BY id DESC LIMIT ?", (limit,)))
