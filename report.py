"""Final PDF report (ReportLab)."""
import json
from xml.sax.saxutils import escape

from reportlab.graphics.charts.barcharts import VerticalBarChart
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

import config
import database as db

NAVY = colors.HexColor("#1F4E79")
STATUS_COLORS = {"Completed": "#2E7D32", "Completed with Improvement": "#1565C0", "Needs Improvement": "#C62828"}


def _p(text, style):
    return Paragraph(escape(str(text)), style)


def build_report(cid):
    c = db.get_candidate(cid)
    final = json.loads(c["final_json"])
    m, a = final["metrics"], final["assessment"]
    reviews = [r for r in db.list_reviews(cid) if r["review_no"] >= 1]
    config.REPORT_DIR.mkdir(parents=True, exist_ok=True)
    path = config.REPORT_DIR / f"{cid}_final_report.pdf"

    ss = getSampleStyleSheet()
    h1 = ParagraphStyle("h1", parent=ss["Title"], textColor=NAVY, fontSize=20)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], textColor=NAVY, spaceBefore=10)
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=9.5, leading=13)
    small = ParagraphStyle("s", parent=body, fontSize=8.5, textColor=colors.grey)
    bullet = ParagraphStyle("bl", parent=body, leftIndent=14, bulletIndent=4)

    def bullets(items):
        return [Paragraph(escape(str(i)), bullet, bulletText="\u2022") for i in items] or [_p("None", body)]

    def table(data, widths, header=True):
        t = Table(data, colWidths=widths, repeatRows=1 if header else 0)
        style = [("GRID", (0, 0), (-1, -1), 0.4, colors.lightgrey), ("VALIGN", (0, 0), (-1, -1), "TOP"),
                 ("FONTSIZE", (0, 0), (-1, -1), 9), ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F4F7FA")])]
        if header:
            style += [("BACKGROUND", (0, 0), (-1, 0), NAVY), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white)]
        t.setStyle(TableStyle(style))
        return t

    st = []
    st.append(_p("Internship Performance Report", h1))
    st.append(_p(f"{config.APP_NAME}  |  Generated {config.pretty(final['generated_at'])}", small))
    st.append(Spacer(1, 8))
    info = [["Candidate ID", c["id"], "Name", c["name"]], ["Email", c["email"], "Role", c["role"]],
            ["Department", c["department"], "Experience", c["experience"]]]
    st.append(table([[_p(x, body) for x in row] for row in info], [2.4 * cm, 5.6 * cm, 2.4 * cm, 6.6 * cm], header=False))
    st.append(Spacer(1, 4))
    st.append(_p(f"Skills: {c['skills']}", body))

    status = final["status"]
    approval = (f"Approved by admin on {config.pretty(c['approved_at'])}" if c["final_approved"]
                else "PENDING ADMIN APPROVAL")
    banner = Table([[Paragraph(f"<font color='white'><b>FINAL STATUS: {escape(status)}</b></font><br/>"
                               f"<font color='white' size=8>{escape(approval)}</font>", body)]], colWidths=[17 * cm])
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor(STATUS_COLORS.get(status, "#444"))),
                                ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    st += [Spacer(1, 10), banner, _p("Review-wise Scores", h2)]

    rows = [["Review", "Task", "Difficulty", "Score", "Coverage"]]
    for r in reviews:
        ev = json.loads(r["evaluation_json"] or "{}")
        rows.append([f"Review {r['review_no']}", r["task_title"], r["difficulty"], f"{r['score']}",
                     f"{ev.get('requirement_coverage', '-')}%"])
    hdr = ParagraphStyle("hdr", parent=body, textColor=colors.white, fontName="Helvetica-Bold")
    data = [[_p(x, hdr) for x in rows[0]]] + [[_p(x, body) for x in row] for row in rows[1:]]
    st.append(table(data, [2.2 * cm, 8 * cm, 2.3 * cm, 2 * cm, 2.5 * cm]))

    d = Drawing(420, 140)
    chart = VerticalBarChart()
    chart.x, chart.y, chart.width, chart.height = 40, 20, 340, 100
    chart.data = [[m["scores"][k] for k in sorted(m["scores"])]]
    chart.categoryAxis.categoryNames = [f"Review {k}" for k in sorted(m["scores"])]
    chart.valueAxis.valueMin, chart.valueAxis.valueMax, chart.valueAxis.valueStep = 0, 100, 20
    chart.bars[0].fillColor = NAVY
    chart.barLabelFormat = "%s"
    chart.barLabels.nudge = 7
    d.add(chart)
    st += [Spacer(1, 6), d, _p("Overall Performance", h2)]

    overall = [["Average score", f"{m['average']}"], ["Improvement (Review 1 to 3)", f"{m['improvement_pct']}%"],
               ["Performance trend", a["performance_trend"]], ["Predicted next score", f"{a['predicted_score']}"],
               ["Readiness", a["readiness"]]]
    st.append(table([[_p(x, body) for x in row] for row in overall], [6 * cm, 11 * cm], header=False))
    st += [Spacer(1, 6), _p(a["summary"], body), _p("Predicted performance: " + a["predicted_performance"], body)]

    st.append(_p("Strengths", h2)); st += bullets(a["strengths"])
    st.append(_p("Weaknesses", h2)); st += bullets(a["weaknesses"])
    st.append(_p("Key Improvements", h2)); st += bullets(a["key_improvements"])
    st.append(_p("Recommendations and Next Tasks", h2)); st += bullets(a["recommendations"] + a["next_tasks"])

    st.append(_p("Review-wise Details", h2))
    for r in reviews:
        ev = json.loads(r["evaluation_json"] or "{}")
        st.append(_p(f"Review {r['review_no']}: {r['task_title']}  -  {r['score']}/100", ParagraphStyle("rh", parent=body, fontName="Helvetica-Bold", spaceBefore=6)))
        st.append(_p(ev.get("summary", ""), body))
        for title, key in (("Strengths", "strengths"), ("Errors found", "errors"), ("Missing requirements", "missing_requirements"),
                           ("Corrections", "corrections")):
            if ev.get(key):
                st.append(_p(title + ":", ParagraphStyle("lbl", parent=body, fontName="Helvetica-Oblique")))
                st += bullets(ev[key][:6])

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.grey)
        canvas.drawString(2 * cm, 1.2 * cm, f"{config.APP_NAME} - {c['name']} ({c['id']})")
        canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
        canvas.restoreState()

    SimpleDocTemplate(str(path), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm,
                      bottomMargin=2 * cm, title=f"Report {cid}").build(st, onFirstPage=footer, onLaterPages=footer)
    return path
