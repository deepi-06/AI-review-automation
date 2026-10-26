"""AI Review Automation System - Streamlit dashboard (Admin + Candidate).  Run: streamlit run app.py"""
import hmac
import json
from datetime import datetime
from html import escape
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

import ai_engine as ai
import calendar_service
import config
import database as db
import email_service as mail
import excel_utils
import file_parser
import reminders
import workflow as wf

st.set_page_config(page_title=config.APP_NAME, page_icon=":material/rate_review:", layout="wide")
st.markdown(
    f"<style>{Path(__file__).with_name('styles.css').read_text(encoding='utf-8')}</style>",
    unsafe_allow_html=True,
)
px.defaults.template = "plotly_white"
px.defaults.color_discrete_sequence = ["#183B63", "#49698B", "#7189A4", "#9AAABD", "#C0CAD5"]
px.defaults.color_continuous_scale = ["#E8EEF5", "#183B63"]


@st.cache_resource
def boot():
    db.init_db()
    for d in (config.UPLOAD_DIR, config.REPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
    try:
        import scheduler
        return scheduler.start()
    except Exception as e:  # noqa: BLE001
        return f"Scheduler not started: {e}"


SCHED = boot()
st.session_state.setdefault("role", None)
st.session_state.setdefault("cid", None)


def hero(title, sub=""):
    st.markdown(
        f"<div class='hero'><h2>{escape(str(title))}</h2><p>{escape(str(sub))}</p></div>",
        unsafe_allow_html=True,
    )


def pdf_button(path, label, key):
    if path and Path(path).exists():
        st.download_button(label, Path(path).read_bytes(), file_name=Path(path).name, mime="application/pdf", key=key)


def render_eval(r):
    ev = json.loads(r["evaluation_json"] or "{}")
    a, b, c, d = st.columns(4)
    a.metric("Score", f"{r['score']}/100")
    b.metric("Requirement coverage", f"{ev.get('requirement_coverage', 0)}%")
    c.metric("Quality", f"{ev.get('quality_score', 0)}/100")
    d.metric("Evaluated by", r["ai_provider"] or "-")
    if ev.get("late"):
        st.warning("This submission was received after the deadline.")
    st.write(ev.get("summary", ""))
    left, right = st.columns(2)
    with left:
        st.markdown("**Strengths**")
        for s in ev.get("strengths", []) or ["-"]:
            st.markdown(f"- {s}")
    with right:
        st.markdown("**Weaknesses**")
        for s in ev.get("weaknesses", []) or ["-"]:
            st.markdown(f"- {s}")
    for title, key in (("Errors detected", "errors"), ("Missing requirements", "missing_requirements"),
                       ("Corrections", "corrections"), ("Suggestions", "suggestions"),
                       ("Static analysis findings", "static_findings")):
        if ev.get(key):
            with st.expander(title, expanded=key in ("errors", "missing_requirements")):
                for s in ev[key]:
                    st.markdown(f"- {s}")
    if ev.get("next_task_recommendation"):
        st.info(f"**Recommended next task:** {ev['next_task_recommendation']}")


def render_task(rv):
    task = json.loads(rv["task_json"] or "{}")
    st.subheader(rv["task_title"])
    st.write(rv["task_description"])
    st.markdown("**Requirements**")
    for q in json.loads(rv["requirements"] or "[]"):
        st.markdown(f"- {q}")
    if task.get("deliverables"):
        st.markdown("**Deliverables:** " + ", ".join(task["deliverables"]))
    if task.get("instructions"):
        st.markdown(f"**Instructions:** {task['instructions']}")
    c1, c2, c3 = st.columns(3)
    c1.metric("Difficulty", rv["difficulty"])
    c2.metric("Deadline", config.pretty(rv["deadline"]))
    c3.metric("Review meeting", config.pretty(rv["review_datetime"]))
    if rv["meeting_link"]:
        st.markdown(f"**Meeting link:** [{rv['meeting_link']}]({rv['meeting_link']})")


# =====================================================================  LOGIN
def login_view():
    hero("AI Review Automation System", "AI-driven internship task allocation, review scheduling and evaluation")
    t_admin, t_cand = st.tabs(["Admin login", "Candidate login"])
    with t_admin:
        with st.form("admin_login"):
            pw = st.text_input("Admin password", type="password")
            go = st.form_submit_button("Login as Admin", type="primary")
        if go:
            if not config.ADMIN_PASSWORD:
                st.error("ADMIN_PASSWORD is not set. Copy .env.example to .env and set it.")
            elif hmac.compare_digest(pw.encode(), config.ADMIN_PASSWORD.encode()):
                st.session_state.update(role="admin", cid=None)
                st.rerun()
            else:
                st.error("Wrong password.")
    with t_cand:
        with st.form("cand_login"):
            email = st.text_input("Email")
            cid = st.text_input("Candidate ID (e.g. C001)")
            go = st.form_submit_button("Login as Candidate", type="primary")
        if go:
            c = db.candidate_login(email, cid) if email and cid else None
            if c:
                st.session_state.update(role="candidate", cid=c["id"])
                st.rerun()
            else:
                st.error("No candidate matches that email + ID.")


# =====================================================================  ADMIN
def admin_dashboard():
    hero("Admin Dashboard", "Live overview of the internship review pipeline")
    cands, revs = db.list_candidates(), db.list_reviews()
    m = st.columns(6)
    m[0].metric("Candidates", len(cands))
    m[1].metric("Review 0 done", sum(1 for r in revs if r["review_no"] == 0 and r["status"] == "Completed"))
    m[2].metric("Awaiting submission", sum(1 for r in revs if r["review_no"] >= 1 and r["status"] == "Assigned"))
    m[3].metric("Evaluated reviews", sum(1 for r in revs if r["review_no"] >= 1 and r["status"] == "Evaluated"))
    m[4].metric("Pending approval", sum(1 for c in cands if c["status"] == "Pending Admin Approval"))
    m[5].metric("Finalised", sum(1 for c in cands if c["final_approved"]))
    if not cands:
        st.info("No candidates yet. Go to **Candidates** and upload the sample Excel file.")
        return
    c1, c2 = st.columns(2)
    sdf = pd.DataFrame(cands)["status"].value_counts().reset_index()
    sdf.columns = ["Status", "Count"]
    c1.plotly_chart(px.pie(sdf, names="Status", values="Count", hole=0.45, title="Candidates by stage"),
                    width="stretch")
    scored = pd.DataFrame([r for r in revs if r["review_no"] >= 1 and r["score"] is not None])
    if not scored.empty:
        avg = scored.groupby("review_no")["score"].mean().round(1).reset_index()
        avg["Review"] = "Review " + avg["review_no"].astype(str)
        c2.plotly_chart(px.bar(avg, x="Review", y="score", text="score", range_y=[0, 100],
                               title="Average score per review"), width="stretch")
    else:
        c2.info("Charts appear after the first evaluation.")


def admin_candidates():
    hero("Candidate Management", "Upload an Excel sheet - candidates are stored in SQLite")
    st.download_button("Download sample Excel (10 candidates)", excel_utils.sample_bytes(),
                       file_name="sample_candidates.xlsx",
                       mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    up = st.file_uploader("Upload candidates Excel (.xlsx)", type=["xlsx"])
    if up:
        rows, errs = excel_utils.parse_candidates(up.getvalue())
        for e in errs:
            st.error(e)
        if rows:
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
            if st.button(f"Import {len(rows)} candidate(s)", type="primary"):
                new, upd = db.upsert_candidates(rows)
                st.success(f"Imported: {new} new, {upd} updated.")
    st.subheader("All candidates")
    cands = db.list_candidates()
    if cands:
        df = pd.DataFrame(cands)[["id", "name", "email", "role", "department", "skills", "experience", "status"]]
        df.columns = ["ID", "Name", "Email", "Role", "Department", "Skills", "Experience", "Status"]
        st.dataframe(df, width="stretch", hide_index=True)
    else:
        st.info("No candidates imported yet.")


def admin_allocation():
    hero("AI Task Allocation", "Tasks are generated automatically by AI - there is no manual assignment")
    st.caption(f"Primary provider: **{ai.preferred()}** | fallback order: {' -> '.join(ai.provider_order()) or 'none'}"
               + (" -> offline rules" if config.ALLOW_OFFLINE_FALLBACK else ""))
    cands = db.list_candidates()
    st.subheader("Select candidates")
    st.caption("Only selected candidates with Registered status receive initial tasks and task emails.")
    select_all_col, deselect_all_col, selection_summary = st.columns([1, 1, 2])
    with select_all_col:
        select_all = st.button("Select All", width="stretch", disabled=not cands)
    with deselect_all_col:
        deselect_all = st.button("Deselect All", width="stretch", disabled=not cands)
    candidate_keys = {c["id"]: f"allocate_candidate_{c['id']}" for c in cands}
    if select_all or deselect_all:
        for key in candidate_keys.values():
            st.session_state[key] = select_all
    selected = []
    if cands:
        for c in cands:
            st.checkbox(
                f"{c['name']} ({c['id']}) - {c['status']}",
                key=candidate_keys[c["id"]],
            )
            if st.session_state[candidate_keys[c["id"]]]:
                selected.append(c)
    else:
        st.info("Import candidates before assigning tasks.")
    selected_pending = [c for c in selected if c["status"] == "Registered"]
    selected_existing = len(selected) - len(selected_pending)
    selection_summary.caption(
        f"Selected: {len(selected)} | Ready for initial task assignment: {len(selected_pending)}"
    )
    if selected_existing:
        st.caption(f"{selected_existing} selected candidate(s) already have a workflow; no duplicate initial tasks will be assigned.")
    if st.button(
        "Assign Tasks to Selected Candidates",
        type="primary",
        disabled=not selected_pending,
    ):
        bar, errs = st.progress(0.0), []
        for i, c in enumerate(selected_pending):
            try:
                wf.run_review0(c["id"])
            except Exception as e:  # noqa: BLE001
                errs.append(f"{c['name']}: {e}")
            bar.progress((i + 1) / len(selected_pending), text=f"Processed {c['name']}")
        st.success(f"Allocated tasks for {len(selected_pending) - len(errs)} selected candidate(s).")
        for e in errs:
            st.error(e)
    if st.button("Resume stalled workflows for selected candidates", disabled=not selected):
        with st.spinner("Resuming..."):
            try:
                done = wf.resume_all([c["id"] for c in selected])
                st.success("; ".join(done) if done else "Nothing to resume.")
            except Exception as e:  # noqa: BLE001
                st.error(str(e))
    names = {c["id"]: c["name"] for c in cands}
    rows = [{"ID": r["candidate_id"], "Name": names.get(r["candidate_id"]), "Review": r["review_no"],
             "Task": r["task_title"], "Difficulty": r["difficulty"], "Deadline": config.pretty(r["deadline"]),
             "Status": r["status"], "Score": r["score"], "AI": r["ai_provider"]}
            for r in db.list_reviews() if r["review_no"] >= 1]
    if rows:
        st.subheader("AI-generated tasks")
        st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    for c in cands:
        r0 = db.get_review(c["id"], 0)
        if r0:
            with st.expander(f"{c['id']} - {c['name']}: Review 0 analysis"):
                an = json.loads(r0["evaluation_json"] or "{}")
                st.write(an.get("summary", ""))
                st.markdown(f"**Skill level:** {an.get('skill_level')}  |  **AI:** {r0['ai_provider']}")
                st.markdown("**Strengths:** " + ", ".join(an.get("strengths", [])))
                st.markdown("**Skill gaps:** " + ", ".join(an.get("skill_gaps", [])))
                st.markdown("**Focus areas:** " + ", ".join(an.get("focus_areas", [])))


def admin_scheduling():
    hero("Review Scheduling", "Meetings are created automatically; reschedule if needed")
    st.caption(f"Meeting links: {'Google Calendar + Meet' if calendar_service.calendar_ready() else 'Jitsi fallback (Google Calendar not authorised)'}"
               f" | Emails: {'Gmail SMTP' if mail.smtp_ready() else 'SMTP not configured'}")
    names = {c["id"]: c["name"] for c in db.list_candidates()}
    revs = [r for r in db.list_reviews() if r["review_no"] >= 1]
    if not revs:
        st.info("No reviews scheduled yet. Run AI task allocation first.")
        return
    df = pd.DataFrame([{"ID": r["candidate_id"], "Name": names.get(r["candidate_id"]), "Review": r["review_no"],
                        "Task deadline": config.pretty(r["deadline"]), "Review meeting": config.pretty(r["review_datetime"]),
                        "Meeting link": r["meeting_link"], "Status": r["status"],
                        "Meeting reminder": "sent" if r["review_reminder_sent"] else "-",
                        "Deadline reminder": "sent" if r["deadline_reminder_sent"] else "-"} for r in revs])
    st.dataframe(df, width="stretch", hide_index=True,
                 column_config={"Meeting link": st.column_config.LinkColumn("Meeting link")})
    with st.form("resched"):
        st.markdown("**Reschedule a review meeting**")
        opts = [f"{r['candidate_id']} | Review {r['review_no']}" for r in revs]
        pick = st.selectbox("Review", opts)
        d = st.date_input("New date", value=config.now().date())
        t = st.time_input("New time", value=datetime.strptime("11:00", "%H:%M").time())
        go = st.form_submit_button("Reschedule and notify candidate", type="primary")
    if go:
        cid, n = pick.split(" | Review ")
        try:
            wf.reschedule(cid.strip(), int(n), datetime.combine(d, t))
            st.success("Rescheduled. Calendar event updated (if Google Calendar is connected) and candidate notified.")
        except Exception as e:  # noqa: BLE001
            st.error(str(e))
    if st.button("Run reminder check now"):
        st.success(f"{reminders.check_reminders()} reminder(s) sent (emails + in-app notifications).")
    st.caption(f"Background scheduler checks every {config.REMINDER_CHECK_MINUTES} min: meetings within "
               f"{config.REMINDER_HOURS} h and deadlines within 48 h.")


def admin_evaluations():
    hero("Submissions and AI Evaluations")
    cands = db.list_candidates()
    if not cands:
        st.info("No candidates yet.")
        return
    pick = st.selectbox("Candidate", [f"{c['id']} - {c['name']}" for c in cands])
    cid = pick.split(" - ")[0]
    for n in (1, 2, 3):
        rv = db.get_review(cid, n)
        if not rv:
            continue
        with st.expander(f"Review {n}: {rv['task_title']}  [{rv['status']}]", expanded=rv["status"] == "Evaluated"):
            if rv["status"] == "Assigned":
                st.write("Waiting for candidate submission.")
                continue
            info = json.loads(rv["submission_files"] or "{}")
            st.caption(f"Submitted {config.pretty(rv['submitted_at'])} | files: {', '.join(info.get('parsed', [])) or '-'}")
            if rv["status"] == "Submitted":
                if st.button("Run AI evaluation", key=f"ev{cid}{n}"):
                    try:
                        with st.spinner("AI is evaluating..."):
                            wf.evaluate_review(cid, n)
                        st.rerun()
                    except Exception as e:  # noqa: BLE001
                        st.error(str(e))
            else:
                render_eval(rv)


def admin_analytics():
    hero("Ranking and Performance Analytics")
    rows = []
    for c in db.list_candidates():
        m = wf.metrics(c["id"])
        if m["scores"]:
            rows.append({"ID": c["id"], "Name": c["name"], "Role": c["role"], "Reviews done": len(m["scores"]),
                         "Average": m["average"], "Latest": m["latest"], "Improvement %": m["improvement_pct"],
                         "Rank score": m["composite"], "Final status": c["final_status"] or "In progress",
                         **{f"Review {k}": v for k, v in m["scores"].items()}})
    if not rows:
        st.info("Rankings appear once at least one review is evaluated.")
        return
    df = pd.DataFrame(rows).sort_values("Rank score", ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)
    st.dataframe(df, width="stretch", hide_index=True)
    st.caption("Rank score = 60% average score + 40% latest score.")
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.bar(df, x="Name", y="Rank score", color="Final status", text="Rank score", range_y=[0, 100],
                           title="Candidate ranking"), width="stretch")
    c2.plotly_chart(px.bar(df, x="Name", y="Improvement %", color="Improvement %",
                           title="Improvement (first to latest review)"), width="stretch")
    long = df.melt(id_vars=["Name"], value_vars=[c for c in df.columns if c.startswith("Review ") and c != "Reviews done"],
                   var_name="Review", value_name="Score").dropna()
    st.plotly_chart(px.line(long, x="Review", y="Score", color="Name", markers=True, range_y=[0, 100],
                            title="Score progression per candidate"), width="stretch")


def admin_final():
    hero("Final Reports and Approval", "Admin approval is required to finalise each candidate")
    cands = db.list_candidates()
    pending = [c for c in cands if c["status"] == "Pending Admin Approval"]
    st.subheader(f"Awaiting approval ({len(pending)})")
    for c in pending:
        fin = json.loads(c["final_json"])
        with st.expander(f"{c['id']} - {c['name']}: {c['final_status']}", expanded=True):
            m, a = fin["metrics"], fin["assessment"]
            x = st.columns(4)
            x[0].metric("Average", m["average"])
            x[1].metric("Improvement", f"{m['improvement_pct']}%")
            x[2].metric("Trend", a["performance_trend"])
            x[3].metric("Predicted score", a["predicted_score"])
            st.write(a["summary"])
            st.write("**Prediction:** " + a["predicted_performance"])
            pdf_button(c["report_path"], "Download PDF report", f"pdf{c['id']}")
            if st.button(f"Approve final completion for {c['name']}", key=f"ap{c['id']}", type="primary"):
                try:
                    wf.approve(c["id"])
                    st.success("Approved. The candidate was notified and emailed the report.")
                    st.rerun()
                except Exception as e:  # noqa: BLE001
                    st.error(str(e))
    done = [c for c in cands if c["final_approved"]]
    st.subheader(f"Finalised ({len(done)})")
    for c in done:
        cols = st.columns([3, 2, 2, 2])
        cols[0].write(f"**{c['name']}** ({c['id']})")
        cols[1].write(c["final_status"])
        cols[2].write(config.pretty(c["approved_at"]))
        with cols[3]:
            pdf_button(c["report_path"], "PDF", f"pdfdone{c['id']}")


def admin_settings():
    hero("Settings, Notifications and Logs")
    st.subheader("Integration status")
    rows = [
        {"Integration": "Gemini API", "Status": "Key set" if ai.configured("gemini") else "Not configured", "Detail": config.GEMINI_MODEL},
        {"Integration": "OpenAI API", "Status": "Key set" if ai.configured("openai") else "Not configured", "Detail": config.OPENAI_MODEL},
        {"Integration": "Gmail SMTP", "Status": "Configured" if mail.smtp_ready() else "Not configured", "Detail": config.SMTP_USER or "-"},
        {"Integration": "Google Calendar / Meet", "Status": "Authorised" if calendar_service.calendar_ready() else "Jitsi fallback", "Detail": config.GOOGLE_TOKEN_FILE},
        {"Integration": "Offline rule fallback", "Status": "on" if config.ALLOW_OFFLINE_FALLBACK else "off", "Detail": "used only if all AI APIs fail"},
        {"Integration": "Scheduler", "Status": "running" if not isinstance(SCHED, str) else "stopped", "Detail": str(SCHED) if isinstance(SCHED, str) else f"every {config.REMINDER_CHECK_MINUTES} min"},
    ]
    st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
    c1, c2 = st.columns(2)
    for col, p in zip((c1, c2), ai.PROVIDERS):
        if col.button(f"Test {p.title()} connection", key=f"test{p}"):
            with st.spinner("Calling API..."):
                ok, msg = ai.test_provider(p)
            (col.success if ok else col.error)(f"{p}: {msg}")
    tab1, tab2 = st.tabs(["Admin notifications", "Email log"])
    with tab1:
        for n in db.get_notifications(wf.ADMIN, 60):
            st.markdown(f"{'**Unread** · ' if not n['is_read'] else ''}`{config.pretty(n['created_at'])}` {n['message']}")
        if st.button("Mark all read"):
            db.mark_all_read(wf.ADMIN)
            st.rerun()
    with tab2:
        log = db.list_email_log()
        st.dataframe(pd.DataFrame(log) if log else pd.DataFrame(columns=["recipient", "subject", "status"]),
                     width="stretch", hide_index=True)


ADMIN_PAGES = {"Dashboard": admin_dashboard, "Candidates": admin_candidates, "AI Task Allocation": admin_allocation,
               "Review Scheduling": admin_scheduling, "Submissions & Evaluations": admin_evaluations,
               "Ranking & Charts": admin_analytics, "Final Reports & Approval": admin_final,
               "Settings & Logs": admin_settings}


def admin_view():
    with st.sidebar:
        st.markdown("### Admin")
        prov = st.selectbox("Primary AI provider", list(ai.PROVIDERS), index=list(ai.PROVIDERS).index(ai.preferred()))
        ai.set_preferred(prov)
        st.caption("Fallback: the other provider, then offline rules." if config.ALLOW_OFFLINE_FALLBACK
                   else "Fallback: the other provider only.")
        page = st.radio("Navigate", list(ADMIN_PAGES), label_visibility="collapsed")
        if st.button("Logout"):
            st.session_state.update(role=None, cid=None)
            st.rerun()
    ADMIN_PAGES[page]()


# =====================================================================  CANDIDATE
def cand_tasks(c):
    hero(f"Welcome, {c['name']}", f"{c['role']} | {c['department']}")
    revs = {r["review_no"]: r for r in db.list_reviews(c["id"])}
    done = sum(1 for n in (1, 2, 3) if n in revs and revs[n]["status"] == "Evaluated")
    st.progress(done / 3, text=f"Progress: {done}/3 reviews evaluated | status: {c['status']}")
    if 0 not in revs:
        st.info("Your profile has not been analysed yet. The admin will start Review 0 soon.")
        return
    open_rv = next((revs[n] for n in (1, 2, 3) if n in revs and revs[n]["status"] in ("Assigned", "Submitted")), None)
    if open_rv:
        st.markdown(f"### Current: Review {open_rv['review_no']} ({open_rv['status']})")
        render_task(open_rv)
        left = config.parse(open_rv["deadline"]) - config.now()
        if open_rv["status"] == "Assigned":
            st.warning(f"{left.days} d {left.seconds // 3600} h left to submit") if left.total_seconds() > 0 else st.error("Deadline passed - late submissions are flagged.")
    elif c["status"] == "Pending Admin Approval":
        st.success("All reviews done! Your final report is awaiting admin approval.")
    elif c["final_approved"]:
        st.success(f"Internship finalised: **{c['final_status']}**")
    with st.expander("Your Review 0 profile analysis"):
        an = json.loads(revs[0]["evaluation_json"] or "{}")
        st.write(an.get("summary", ""))
        st.markdown(f"**Level:** {an.get('skill_level')} | **Focus areas:** {', '.join(an.get('focus_areas', []))}")
    st.subheader("Review schedule")
    st.dataframe(pd.DataFrame([{"Review": f"Review {n}", "Task": r["task_title"], "Deadline": config.pretty(r["deadline"]),
                                "Meeting": config.pretty(r["review_datetime"]), "Link": r["meeting_link"], "Status": r["status"],
                                "Score": r["score"]} for n, r in sorted(revs.items()) if n >= 1]),
                 width="stretch", hide_index=True,
                 column_config={"Link": st.column_config.LinkColumn("Meeting link")})


def cand_submit(c):
    hero("Submit Your Work")
    revs = {r["review_no"]: r for r in db.list_reviews(c["id"])}
    open_rv = next((revs[n] for n in (1, 2, 3) if n in revs and revs[n]["status"] in ("Assigned", "Submitted")), None)
    if not open_rv:
        st.info("There is no open task right now.")
        return
    n = open_rv["review_no"]
    st.markdown(f"**Review {n}:** {open_rv['task_title']}  |  Deadline: {config.pretty(open_rv['deadline'])}")
    if open_rv["status"] == "Submitted":
        st.warning("Your work is submitted but the AI evaluation did not finish. Click to retry.")
        if st.button("Retry AI evaluation", type="primary"):
            try:
                with st.spinner("Evaluating..."):
                    wf.evaluate_review(c["id"], n)
                st.rerun()
            except Exception as e:  # noqa: BLE001
                st.error(str(e))
        return
    with st.form(f"submit{n}", clear_on_submit=False):
        files = st.file_uploader("Upload code, documents or a project zip", accept_multiple_files=True,
                                 type=file_parser.UPLOAD_TYPES)
        notes = st.text_area("Notes for the reviewer (optional) - or paste code/answers here", height=160)
        go = st.form_submit_button("Submit for AI evaluation", type="primary")
    if go:
        try:
            with st.spinner("Submitting and running AI evaluation - this can take up to a minute..."):
                r = wf.submit(c["id"], n, [(f.name, f.getvalue()) for f in files or []], notes)
            st.success(f"Evaluated! Score: {r['score']}/100. See 'AI Feedback'."
                       + (" Your next task has been assigned." if n < 3 else " Your final report was generated for admin approval."))
        except ValueError as e:
            st.error(str(e))
        except Exception as e:  # noqa: BLE001
            st.error(f"Submission saved, but evaluation failed: {e}. You can retry from this page.")


def cand_feedback(c):
    hero("AI Feedback")
    revs = {r["review_no"]: r for r in db.list_reviews(c["id"])}
    evaluated = [n for n in (1, 2, 3) if n in revs and revs[n]["status"] == "Evaluated"]
    if not evaluated:
        st.info("No feedback yet - submit your first task.")
        return
    tabs = st.tabs([f"Review {n}" for n in evaluated])
    for tab, n in zip(tabs, evaluated):
        with tab:
            st.caption(revs[n]["task_title"])
            render_eval(revs[n])
    if len(evaluated) > 1:
        m = wf.metrics(c["id"])
        df = pd.DataFrame({"Review": [f"Review {k}" for k in sorted(m["scores"])], "Score": [m["scores"][k] for k in sorted(m["scores"])]})
        st.plotly_chart(px.line(df, x="Review", y="Score", markers=True, range_y=[0, 100], title="Your progress"),
                        width="stretch")
        st.metric("Improvement since Review 1", f"{m['improvement_pct']}%")
    if c["final_approved"]:
        st.success(f"Final status: {c['final_status']}")
        pdf_button(c["report_path"], "Download your final report", f"mypdf{c['id']}")


def cand_notifications(c):
    hero("Notifications")
    for n in db.get_notifications(c["id"]):
        st.markdown(f"{'**Unread** · ' if not n['is_read'] else ''}`{config.pretty(n['created_at'])}` {n['message']}")
    if st.button("Mark all read"):
        db.mark_all_read(c["id"])
        st.rerun()


def candidate_view():
    c = db.get_candidate(st.session_state["cid"])
    if not c:
        st.session_state.update(role=None, cid=None)
        st.rerun()
        return
    unread = db.unread_count(c["id"])
    pages = {"My Tasks & Schedule": cand_tasks, "Submit Work": cand_submit, "AI Feedback": cand_feedback,
             f"Notifications ({unread})": cand_notifications}
    with st.sidebar:
        st.markdown(f"### {c['name']}\n`{c['id']}`")
        page = st.radio("Navigate", list(pages), label_visibility="collapsed")
        if st.button("Logout"):
            st.session_state.update(role=None, cid=None)
            st.rerun()
    pages[page](c)


# =====================================================================  ROUTER
role = st.session_state["role"]
if role == "admin":
    admin_view()
elif role == "candidate":
    candidate_view()
else:
    login_view()
