"""End-to-end test of the Review 0-3 workflow (runs offline: no API keys / SMTP needed).
Run:  python tests/test_workflow.py
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TMP = Path(tempfile.mkdtemp())
os.environ["DB_PATH"] = str(TMP / "test.db")

import config  # noqa: E402

config.UPLOAD_DIR, config.REPORT_DIR = TMP / "uploads", TMP / "reports"
config.GEMINI_API_KEY = config.OPENAI_API_KEY = config.SMTP_USER = config.SMTP_APP_PASSWORD = ""
config.GOOGLE_TOKEN_FILE = str(TMP / "no_token.json")
config.ALLOW_OFFLINE_FALLBACK = True

import ai_engine as ai  # noqa: E402
import database as db  # noqa: E402
import excel_utils  # noqa: E402
import file_parser  # noqa: E402
import reminders  # noqa: E402
import workflow as wf  # noqa: E402

BAD = b"def create_student(:\n    pass\n"  # syntax error
OK = b'''"""Student REST API with Flask"""
import sqlite3
from flask import Flask, request, jsonify
app = Flask(__name__)
def db():
    return sqlite3.connect("students.db")
@app.route("/students", methods=["POST"])
def create_student():
    """CRUD endpoint: create a student with input validation"""
    data = request.get_json() or {}
    if not data.get("name"):
        return jsonify({"error": "name required"}), 400
    con = db(); con.execute("INSERT INTO students(name) VALUES (?)", (data["name"],)); con.commit()
    return jsonify({"ok": True}), 201
''' * 3
README = b"README: setup, endpoints, unit tests, error codes, SQLite parameterised queries, validation, CRUD students"


def check(cond, msg):
    assert cond, msg
    print("PASS:", msg)


def main():
    db.init_db()
    rows, errs = excel_utils.parse_candidates(excel_utils.sample_bytes())
    check(len(rows) == 10 and not errs, "sample Excel has 10 valid candidates")
    check(excel_utils.parse_candidates(b"not excel")[1], "invalid Excel reported as error")
    new, upd = db.upsert_candidates(rows)
    check(new == 10, "10 candidates stored in SQLite")
    new, upd = db.upsert_candidates([dict(rows[0], id=rows[0]["id"].lower())])
    check(new == 0 and upd == 1 and len(db.list_candidates()) == 10,
          "candidate IDs are matched case-insensitively on re-import")
    check(db.candidate_login("AARAV.SHARMA@example.com", "c001") is not None, "candidate login works")

    # Review 0 -> task 1
    rv1 = wf.run_review0("C001")
    check(rv1["status"] == "Assigned" and rv1["task_title"], "Review 0 analysed candidate and AI-assigned task 1")
    check(rv1["meeting_link"].startswith("https://"), "meeting link generated")
    check(db.get_review("C001", 0)["status"] == "Completed", "Review 0 stored")
    new, upd = db.upsert_candidates([dict(rows[0], id="c001")])
    check(new == 0 and upd == 1 and db.get_candidate("C001")["status"] == "Review 1: Task Assigned"
          and db.get_review("C001", 1)["status"] == "Assigned",
          "re-importing a candidate preserves the existing workflow and reviews")
    resume = wf.resume_all(["c003"])
    check(not resume and db.get_review("C003", 0) is None,
          "resuming selected candidates does not start unselected workflows")

    original_review0 = ai.review0
    original_create_meeting = wf.calendar_service.create_meeting
    original_send_email = wf.mail.send_email
    selected_emails = []

    def mock_review0(candidate):
        return {
            "analysis": {"summary": "Test profile"},
            "task": {"title": "Test task", "description": "Test task description",
                     "requirements": [], "difficulty": "Easy", "deadline_days": 3},
        }, "offline-test"

    try:
        ai.review0 = mock_review0
        wf.calendar_service.create_meeting = lambda **kwargs: {
            "link": "https://meet.jit.si/test", "event_id": None, "note": "",
        }
        wf.mail.send_email = lambda recipient, *args, **kwargs: selected_emails.append(recipient)
        wf.run_review0("C004")
        wf.run_review0("C005")
        check(db.get_review("C004", 1) is not None and db.get_review("C005", 1) is not None
              and db.get_review("C006", 0) is None
              and selected_emails == ["sneha.reddy@example.com", "karthik.s@example.com"],
              "task assignment and task emails are limited to selected candidates")
    finally:
        ai.review0 = original_review0
        wf.calendar_service.create_meeting = original_create_meeting
        wf.mail.send_email = original_send_email

    # Review 1 (weak, with syntax error)
    r = wf.submit("C001", 1, [("api.py", BAD)], "first attempt")
    check(r["status"] == "Evaluated" and r["score"] is not None, f"Review 1 evaluated (score {r['score']})")
    ev1 = json.loads(r["evaluation_json"])
    check(any("SYNTAX" in e for e in ev1["errors"]), "syntax error detected")
    rv2 = db.get_review("C001", 2)
    check(rv2 and rv2["status"] == "Assigned", "Review 2 improvement task auto-generated")
    check("Improvement" in rv2["task_title"], "Review 2 task is an improvement task")

    # Review 2 (zip project)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("app.py", OK)
        z.writestr("README.md", README)
    r = wf.submit("C001", 2, [("project.zip", buf.getvalue())])
    check(r["score"] > ev1["score"], f"Review 2 score improved ({ev1['score']} -> {r['score']})")
    check(db.get_review("C001", 3)["status"] == "Assigned", "Review 3 final task generated")

    # Review 3
    r = wf.submit("C001", 3, [("app.py", OK), ("README.md", README)], "final")
    c = db.get_candidate("C001")
    check(c["status"] == "Pending Admin Approval", "candidate awaits admin approval after Review 3")
    final = json.loads(c["final_json"])
    check(final["status"] in ("Completed", "Completed with Improvement", "Needs Improvement"), f"final status: {final['status']}")
    check(final["metrics"]["improvement_pct"] > 0, f"improvement {final['metrics']['improvement_pct']}%")
    pdf = Path(c["report_path"])
    check(pdf.exists() and pdf.read_bytes()[:4] == b"%PDF", "PDF report generated")

    wf.approve("C001")
    c = db.get_candidate("C001")
    check(c["final_approved"] == 1 and c["status"] == c["final_status"], "admin approval recorded")
    check(len(db.list_email_log()) >= 7, "all emails attempted and logged (SMTP not configured -> NOT SENT)")
    check(db.unread_count("C001") > 0 and db.unread_count("ADMIN") > 0, "in-app notifications created")

    # cannot submit twice
    try:
        wf.submit("C001", 1, [("x.py", OK)])
        raise SystemExit("double submission should fail")
    except ValueError:
        check(True, "duplicate submission blocked")

    # reminders
    wf.run_review0("C002")
    rv = db.get_review("C002", 1)
    db.upsert_review("C002", 1, review_datetime=config.fmt(config.now() + timedelta(hours=5)),
                     deadline=config.fmt(config.now() + timedelta(hours=10)))
    check(reminders.check_reminders() == 2, "review + deadline reminders sent")
    check(reminders.check_reminders() == 0, "reminders are not repeated")

    # provider selection + fallback with mocked HTTP
    config.GEMINI_API_KEY = config.OPENAI_API_KEY = "test-key"
    calls = []
    original_post = ai._post

    class Resp:
        def __init__(self, code, data):
            self.status_code, self._d, self.text = code, data, json.dumps(data)

        def json(self):
            return self._d

    config.GEMINI_MODEL = "gemini-3.8-flash"
    gemini_requests = []

    def fake_gemini_post(url, headers, payload):
        gemini_requests.append(url)
        return {"candidates": [{"content": {"parts": [{"text": '{"ok": true}'}]}}]}

    try:
        ai._post = fake_gemini_post
        check(ai.call_gemini("system", "prompt") == '{"ok": true}'
              and "/models/gemini-3.8-flash:generateContent" in gemini_requests[0],
              "Gemini requests use the configured supported model")
    finally:
        ai._post = original_post

    def fake_post(url, headers=None, json=None, timeout=None):  # noqa: A002
        calls.append(url)
        if "googleapis" in url:
            return Resp(400, {"error": "bad key"})
        out = {"score": 82, "requirement_coverage": 80, "quality_score": 75, "summary": "ok", "strengths": ["a"],
               "errors": [], "missing_requirements": [], "weaknesses": [], "corrections": [], "suggestions": [],
               "next_task_recommendation": "x"}
        return Resp(200, {"choices": [{"message": {"content": __import__("json").dumps(out)}}]})

    ai.requests.post = fake_post
    ai.set_preferred("gemini")
    ev, provider = ai.evaluate(db.get_candidate("C002"), db.get_review("C002", 1),
                               {"text": "x" * 200, "file_names": [], "findings": [], "truncated": False}, False)
    check(provider == "openai" and ev["score"] == 82, "Gemini failed -> automatic fallback to OpenAI")
    check("googleapis" in calls[0], "primary provider was tried first")
    config.OPENAI_API_KEY = ""
    config.ALLOW_OFFLINE_FALLBACK = False
    try:
        ai.evaluate(db.get_candidate("C002"), db.get_review("C002", 1),
                    {"text": "x" * 200, "file_names": [], "findings": [], "truncated": False}, False)
        raise SystemExit("should raise when all providers fail and offline fallback disabled")
    except ai.AIError:
        check(True, "AIError raised when offline fallback disabled")

    print("\nALL TESTS PASSED")


if __name__ == "__main__":
    main()
