"""Reminder logic (called by APScheduler and by the admin 'Run reminder check now' button)."""
from datetime import timedelta

import config
import database as db
import email_service as mail


def check_reminders():
    now = config.now()
    sent = 0
    start = config.fmt(now)
    # review-meeting reminders
    for rv in db.reviews_needing_review_reminder(start, config.fmt(now + timedelta(hours=config.REMINDER_HOURS))):
        c = db.get_candidate(rv["candidate_id"])
        if not c:
            continue
        subject, body = mail.reminder_email(c, rv["review_no"], rv, "review")
        mail.send_email(c["email"], subject, body)
        db.add_notification(c["id"], f"Reminder: Review {rv['review_no']} meeting on {config.pretty(rv['review_datetime'])}")
        db.upsert_review(c["id"], rv["review_no"], review_reminder_sent=1)
        sent += 1
    # submission-deadline reminders (48h window)
    for rv in db.reviews_needing_deadline_reminder(start, config.fmt(now + timedelta(hours=48))):
        c = db.get_candidate(rv["candidate_id"])
        if not c:
            continue
        subject, body = mail.reminder_email(c, rv["review_no"], rv, "deadline")
        mail.send_email(c["email"], subject, body)
        db.add_notification(c["id"], f"Reminder: Review {rv['review_no']} task due {config.pretty(rv['deadline'])}")
        db.upsert_review(c["id"], rv["review_no"], deadline_reminder_sent=1)
        sent += 1
    return sent
