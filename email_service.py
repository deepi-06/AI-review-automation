"""Gmail SMTP sending + HTML email templates. Every attempt is logged in the email_log table."""
import json
import re
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path

import config
import database as db
from utils import esc


def smtp_ready():
    return bool(config.SMTP_USER and config.SMTP_APP_PASSWORD)


def send_email(to, subject, html_body, attachments=None):
    """Returns (ok, message). Never raises: failures are logged and shown in the admin UI."""
    if not smtp_ready():
        err = "SMTP not configured (set SMTP_USER and SMTP_APP_PASSWORD in .env)"
        db.log_email(to, subject, "NOT SENT", err)
        return False, err
    try:
        msg = EmailMessage()
        msg["From"] = f"{config.MAIL_FROM_NAME} <{config.SMTP_USER}>"
        msg["To"] = to
        msg["Subject"] = subject
        msg.set_content(re.sub(r"<[^>]+>", "", html_body.replace("<br>", "\n").replace("</p>", "\n")))
        msg.add_alternative(html_body, subtype="html")
        for path in attachments or []:
            p = Path(path)
            if p.exists():
                msg.add_attachment(p.read_bytes(), maintype="application", subtype="pdf", filename=p.name)
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, context=ctx, timeout=30) as s:
            s.login(config.SMTP_USER, config.SMTP_APP_PASSWORD)
            s.send_message(msg)
        db.log_email(to, subject, "SENT")
        return True, "sent"
    except Exception as e:  # noqa: BLE001
        db.log_email(to, subject, "FAILED", str(e)[:300])
        return False, str(e)[:300]


def _wrap(title, inner):
    return (f"<div style='font-family:Arial,sans-serif;max-width:640px;margin:auto;border:1px solid #dde3ea;border-radius:8px'>"
            f"<div style='background:#1F4E79;color:#fff;padding:14px 20px;font-size:18px'>{esc(title)}</div>"
            f"<div style='padding:20px;color:#222;line-height:1.5'>{inner}</div>"
            f"<div style='padding:10px 20px;color:#888;font-size:12px'>{esc(config.APP_NAME)} - automated message</div></div>")


def _ul(items):
    return "<ul>" + "".join(f"<li>{esc(i)}</li>" for i in items) + "</ul>" if items else ""


def task_email(c, n, rv):
    reqs = json.loads(rv["requirements"] or "[]")
    task = json.loads(rv["task_json"] or "{}")
    link = rv["meeting_link"] or ""
    inner = (f"<p>Hi {esc(c['name'])},</p><p>Your <b>Review {n}</b> task has been assigned by our AI mentor.</p>"
             f"<h3>{esc(rv['task_title'])}</h3><p>{esc(rv['task_description'])}</p>"
             f"<p><b>Difficulty:</b> {esc(rv['difficulty'])}<br><b>Submission deadline:</b> {esc(config.pretty(rv['deadline']))}</p>"
             f"<p><b>Requirements</b></p>{_ul(reqs)}<p><b>Deliverables</b></p>{_ul(task.get('deliverables', []))}"
             f"<p><b>Instructions:</b> {esc(task.get('instructions',''))}</p>"
             f"<p><b>Review meeting:</b> {esc(config.pretty(rv['review_datetime']))}<br>"
             f"<b>Meeting link:</b> <a href='{esc(link)}'>{esc(link)}</a></p>"
             f"<p>Submit your work here: <a href='{esc(config.APP_URL)}'>{esc(config.APP_URL)}</a> "
             f"(login with your email and candidate ID <b>{esc(c['id'])}</b>).</p>")
    return f"Review {n} task assigned: {rv['task_title']}", _wrap(f"Review {n} - New Task", inner)


def result_email(c, n, ev, score):
    inner = (f"<p>Hi {esc(c['name'])},</p><p>Your Review {n} submission has been evaluated.</p>"
             f"<p style='font-size:20px'><b>Score: {score}/100</b></p><p>{esc(ev.get('summary',''))}</p>"
             f"<p><b>Strengths</b></p>{_ul(ev.get('strengths', []))}<p><b>Areas to improve</b></p>{_ul(ev.get('weaknesses', []))}"
             f"<p><b>Suggestions</b></p>{_ul(ev.get('suggestions', []))}<p>Log in to see the full feedback and your next task.</p>")
    return f"Review {n} result: {score}/100", _wrap(f"Review {n} - AI Feedback", inner)


def reminder_email(c, n, rv, kind):
    if kind == "deadline":
        subject = f"Reminder: Review {n} submission due {config.pretty(rv['deadline'])}"
        inner = (f"<p>Hi {esc(c['name'])},</p><p>Your task <b>{esc(rv['task_title'])}</b> is due on "
                 f"<b>{esc(config.pretty(rv['deadline']))}</b>. Please submit it in the portal.</p>")
    else:
        subject = f"Reminder: Review {n} meeting on {config.pretty(rv['review_datetime'])}"
        inner = (f"<p>Hi {esc(c['name'])},</p><p>Your Review {n} meeting is on <b>{esc(config.pretty(rv['review_datetime']))}</b>."
                 f"</p><p>Link: <a href='{esc(rv['meeting_link'])}'>{esc(rv['meeting_link'])}</a></p>")
    return subject, _wrap("Reminder", inner)


def reschedule_email(c, n, rv):
    inner = (f"<p>Hi {esc(c['name'])},</p><p>Your Review {n} meeting has been rescheduled to "
             f"<b>{esc(config.pretty(rv['review_datetime']))}</b>.</p>"
             f"<p>Link: <a href='{esc(rv['meeting_link'])}'>{esc(rv['meeting_link'])}</a></p>")
    return f"Review {n} rescheduled: {config.pretty(rv['review_datetime'])}", _wrap("Meeting Rescheduled", inner)


def final_email(c, status):
    inner = (f"<p>Hi {esc(c['name'])},</p><p>Your internship review cycle has been finalised and approved.</p>"
             f"<p style='font-size:18px'><b>Final status: {esc(status)}</b></p><p>Your detailed performance report is attached.</p>")
    return f"Final internship result: {status}", _wrap("Final Result", inner)
