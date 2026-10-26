"""Review 0-3 workflow orchestration. All task allocation is done by AI - the admin never assigns tasks manually."""
import json
from datetime import timedelta
from statistics import mean

import ai_engine as ai
import calendar_service
import config
import database as db
import email_service as mail
import file_parser
import report

ADMIN = "ADMIN"


# ------------------------------------------------------------------ helpers
def _schedule(days):
    deadline = (config.now() + timedelta(days=days)).replace(hour=18, minute=0, second=0)
    review = (deadline + timedelta(days=1)).replace(hour=config.REVIEW_HOUR, minute=0, second=0)
    while review.weekday() >= 5:  # skip weekends
        review += timedelta(days=1)
    return deadline, review


def _history(cid):
    out = []
    for r in db.list_reviews(cid):
        if r["review_no"] >= 1 and r["status"] == "Evaluated":
            out.append({"review_no": r["review_no"], "score": r["score"], "task_title": r["task_title"],
                        "evaluation": json.loads(r["evaluation_json"] or "{}")})
    return out


def metrics(cid):
    scores = {r["review_no"]: r["score"] for r in db.list_reviews(cid)
              if r["review_no"] >= 1 and r["score"] is not None}
    vals = [scores[k] for k in sorted(scores)]
    if not vals:
        return {"scores": {}, "average": 0, "latest": 0, "improvement_pct": 0, "composite": 0}
    first, last = vals[0], vals[-1]
    if len(vals) < 2:
        imp = 0.0
    elif first > 0:
        imp = round((last - first) / first * 100, 1)
    else:
        imp = 100.0 if last > 0 else 0.0
    avg = round(mean(vals), 1)
    return {"scores": scores, "average": avg, "latest": last, "improvement_pct": imp,
            "composite": round(0.6 * avg + 0.4 * last, 1)}


def decide_status(m):
    scores = [m["scores"][k] for k in sorted(m["scores"])]
    first, last = scores[0], scores[-1]
    if last >= 60 and m["improvement_pct"] >= 15:
        return "Completed with Improvement"
    if m["average"] >= 70 and last >= 70:
        return "Completed"
    return "Needs Improvement"


# ------------------------------------------------------------------ Review 0
def run_review0(cid):
    c = db.get_candidate(cid)
    if not c:
        raise ValueError("Candidate not found")
    if db.get_review(cid, 0) and db.get_review(cid, 0)["status"] == "Completed":
        return db.get_review(cid, 1)  # idempotent
    result, provider = ai.review0(c)
    db.upsert_review(cid, 0, status="Completed", evaluation_json=json.dumps(result["analysis"]),
                     ai_provider=provider, evaluated_at=config.fmt(config.now()))
    db.add_notification(cid, "Review 0 complete: your profile was analysed by AI.")
    _assign(cid, 1, result["task"], provider)
    return db.get_review(cid, 1)


def _assign(cid, n, task, provider):
    c = db.get_candidate(cid)
    deadline, review_dt = _schedule(task["deadline_days"])
    meet = calendar_service.create_meeting(
        summary=f"{config.APP_NAME}: Review {n} - {c['name']}",
        description=f"Review {n} for {c['name']} ({c['role']}). Task: {task['title']}",
        start=review_dt, minutes=config.MEETING_MINUTES, attendee_email=c["email"])
    db.upsert_review(cid, n, task_title=task["title"], task_description=task["description"],
                     requirements=json.dumps(task["requirements"]), difficulty=task["difficulty"],
                     task_json=json.dumps(task), deadline=config.fmt(deadline),
                     review_datetime=config.fmt(review_dt), meeting_link=meet["link"], event_id=meet["event_id"],
                     status="Assigned", ai_provider=provider, review_reminder_sent=0, deadline_reminder_sent=0)
    db.update_candidate(cid, status=f"Review {n}: Task Assigned")
    db.add_notification(cid, f"New task for Review {n}: {task['title']} (due {config.pretty(config.fmt(deadline))}).")
    db.add_notification(ADMIN, f"AI assigned Review {n} task to {c['name']}: {task['title']}"
                        + (f" [{meet['note']}]" if meet["note"] else ""))
    subject, body = mail.task_email(c, n, db.get_review(cid, n))
    mail.send_email(c["email"], subject, body)


# ------------------------------------------------------------------ submission + evaluation
def submit(cid, n, files, notes=""):
    """files: [(filename, bytes)]. Saves, then runs the AI evaluation and advances the workflow."""
    rv = db.get_review(cid, n)
    if not rv or rv["status"] != "Assigned":
        raise ValueError(f"Review {n} is not open for submission.")
    if not files and not (notes or "").strip():
        raise ValueError("Upload at least one file or write some notes.")
    if sum(len(d) for _, d in files) > file_parser.MAX_UPLOAD_BYTES:
        raise ValueError("Total upload size must be under 10 MB.")
    saved = file_parser.save_uploads(cid, n, files)
    bundle = file_parser.build_bundle(files, notes)
    if not bundle["text"].strip():
        raise ValueError("No readable content found. Upload code, documents (pdf/docx), or a zip of your project.")
    db.upsert_review(cid, n, status="Submitted", submission_text=bundle["text"][:60000],
                     submission_files=json.dumps({"files": saved, "parsed": bundle["file_names"],
                                                  "findings": bundle["findings"]}),
                     submitted_at=config.fmt(config.now()))
    db.update_candidate(cid, status=f"Review {n}: Submitted")
    db.add_notification(ADMIN, f"{db.get_candidate(cid)['name']} submitted Review {n}.")
    return evaluate_review(cid, n)


def evaluate_review(cid, n):
    """Runs (or re-runs) the AI evaluation for a submitted review, then advances the workflow."""
    c, rv = db.get_candidate(cid), db.get_review(cid, n)
    if not rv or rv["status"] not in ("Submitted", "Evaluated") or not rv["submission_text"]:
        raise ValueError("Nothing to evaluate.")
    info = json.loads(rv["submission_files"] or "{}")
    bundle = {"text": rv["submission_text"], "file_names": info.get("parsed", []),
              "findings": info.get("findings", []), "truncated": len(rv["submission_text"]) >= 45000}
    late = bool(rv["submitted_at"] and rv["deadline"] and rv["submitted_at"] > rv["deadline"])
    ev, provider = ai.evaluate(c, rv, bundle, late)
    db.upsert_review(cid, n, status="Evaluated", score=ev["score"], evaluation_json=json.dumps(ev),
                     ai_provider=provider, evaluated_at=config.fmt(config.now()))
    db.update_candidate(cid, status=f"Review {n}: Evaluated")
    db.add_notification(cid, f"Review {n} evaluated: score {ev['score']}/100. Open 'AI Feedback' for details.")
    db.add_notification(ADMIN, f"Review {n} evaluated for {c['name']}: {ev['score']}/100 ({provider}).")
    subject, body = mail.result_email(c, n, ev, ev["score"])
    mail.send_email(c["email"], subject, body)
    try:
        advance_candidate(cid)
    except Exception as e:  # noqa: BLE001
        db.add_notification(ADMIN, f"Next step for {c['name']} failed ({str(e)[:120]}). Use 'Resume stalled workflows'.")
    return db.get_review(cid, n)


def advance_candidate(cid):
    """Idempotent: after an evaluated review, generate the next AI task, or finalise after Review 3."""
    c = db.get_candidate(cid)
    revs = {r["review_no"]: r for r in db.list_reviews(cid)}
    last = max((n for n in (1, 2, 3) if revs.get(n) and revs[n]["status"] == "Evaluated"), default=None)
    if last is None:
        return None
    if last < 3 and (last + 1) not in revs:
        task, provider = ai.next_task(c, last + 1, _history(cid))
        _assign(cid, last + 1, task, provider)
        return f"Review {last + 1} task assigned"
    if last == 3 and not c["final_json"]:
        finalize(cid)
        return "Final report generated"
    return None


def resume_all(candidate_ids=None):
    done = []
    candidates = db.list_candidates()
    if candidate_ids is not None:
        selected_ids = {str(cid).strip().upper() for cid in candidate_ids}
        candidates = [c for c in candidates if c["id"].strip().upper() in selected_ids]
    for c in candidates:
        for n in (1, 2, 3):
            rv = db.get_review(c["id"], n)
            if rv and rv["status"] == "Submitted":
                evaluate_review(c["id"], n)
                done.append(f"{c['name']}: Review {n} evaluated")
        msg = advance_candidate(c["id"])
        if msg:
            done.append(f"{c['name']}: {msg}")
    return done


# ------------------------------------------------------------------ final report
def finalize(cid):
    c = db.get_candidate(cid)
    m = metrics(cid)
    if len(m["scores"]) < 3:
        raise ValueError("All three reviews must be evaluated first.")
    assess, provider = ai.final_assessment(c, _history(cid), m)
    status = decide_status(m)
    final = {"metrics": m, "assessment": assess, "status": status, "ai_provider": provider,
             "generated_at": config.fmt(config.now())}
    db.update_candidate(cid, final_json=json.dumps(final), final_status=status, status="Pending Admin Approval")
    path = report.build_report(cid)
    db.update_candidate(cid, report_path=str(path))
    db.add_notification(ADMIN, f"Final report ready for {c['name']} ({status}). Approval required.")
    return final


def approve(cid):
    c = db.get_candidate(cid)
    if not c or c["status"] != "Pending Admin Approval":
        raise ValueError("Candidate is not awaiting approval.")
    db.update_candidate(cid, final_approved=1, approved_at=config.fmt(config.now()), status=c["final_status"])
    path = report.build_report(cid)  # rebuild so the PDF shows 'Approved'
    db.update_candidate(cid, report_path=str(path))
    c = db.get_candidate(cid)
    subject, body = mail.final_email(c, c["final_status"])
    mail.send_email(c["email"], subject, body, attachments=[path])
    db.add_notification(cid, f"Your internship was finalised: {c['final_status']}. Download your report.")
    return c


def reschedule(cid, n, new_dt):
    rv = db.get_review(cid, n)
    if not rv:
        raise ValueError("Review not found")
    ok = calendar_service.update_meeting(rv["event_id"], new_dt, config.MEETING_MINUTES)
    link, event_id = rv["meeting_link"], rv["event_id"]
    if not ok and rv["event_id"] is None:
        pass  # Jitsi room links are time-independent, keep the same link
    db.upsert_review(cid, n, review_datetime=config.fmt(new_dt), meeting_link=link, event_id=event_id,
                     review_reminder_sent=0)
    c = db.get_candidate(cid)
    subject, body = mail.reschedule_email(c, n, db.get_review(cid, n))
    mail.send_email(c["email"], subject, body)
    db.add_notification(cid, f"Review {n} rescheduled to {config.pretty(config.fmt(new_dt))}.")
