"""AI layer: Gemini + OpenAI (configurable primary, automatic fallback) with a rule-based last resort.

Every AI feature returns (result_dict, provider_used). Provider used is stored with each review.
"""
import json
import logging
import re
import time
from datetime import datetime

import requests

import config

log = logging.getLogger("ai_engine")
PROVIDERS = ("gemini", "openai")
_preferred = None

SYSTEM = ("You are an expert technical mentor and a strict but fair reviewer in an internship programme. "
          "Respond with ONE valid JSON object only: no markdown fences, no commentary.")


class AIError(Exception):
    pass


# ---------------------------------------------------------------- provider plumbing
def set_preferred(p):
    global _preferred
    _preferred = p if p in PROVIDERS else None


def preferred():
    return _preferred or (config.AI_PROVIDER if config.AI_PROVIDER in PROVIDERS else "gemini")


def configured(p):
    return bool(config.GEMINI_API_KEY if p == "gemini" else config.OPENAI_API_KEY)


def provider_order():
    first = preferred()
    other = "openai" if first == "gemini" else "gemini"
    return [p for p in (first, other) if configured(p)]


def _post(url, headers, payload):
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=config.AI_TIMEOUT)
        except requests.RequestException as e:
            last = AIError(f"network error: {e.__class__.__name__}")
            time.sleep(1.5 * (attempt + 1))
            continue
        if r.status_code in (429, 500, 502, 503, 504) and attempt < 2:
            last = AIError(f"HTTP {r.status_code}")
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code != 200:
            raise AIError(f"HTTP {r.status_code}: {r.text[:200]}")
        return r.json()
    raise last or AIError("request failed")


def call_gemini(system, prompt):
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL}:generateContent"
    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4, "responseMimeType": "application/json"},
    }
    data = _post(url, {"x-goog-api-key": config.GEMINI_API_KEY, "Content-Type": "application/json"}, payload)
    try:
        return "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
    except (KeyError, IndexError, TypeError):
        raise AIError(f"unexpected Gemini response: {str(data)[:200]}")


def call_openai(system, prompt):
    payload = {
        "model": config.OPENAI_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    data = _post("https://api.openai.com/v1/chat/completions",
                 {"Authorization": f"Bearer {config.OPENAI_API_KEY}", "Content-Type": "application/json"}, payload)
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        raise AIError(f"unexpected OpenAI response: {str(data)[:200]}")


CALLERS = {"gemini": call_gemini, "openai": call_openai}


def parse_json(text):
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        a, b = text.find("{"), text.rfind("}")
        if a != -1 and b > a:
            return json.loads(text[a:b + 1])
        raise AIError("model did not return valid JSON")


def ask(prompt, normalise, offline):
    """Try configured providers in order, then the offline rule engine (if allowed)."""
    errors = []
    for p in provider_order():
        try:
            return normalise(parse_json(CALLERS[p](SYSTEM, prompt))), p
        except Exception as e:  # noqa: BLE001
            errors.append(f"{p}: {e}")
            log.warning("AI provider %s failed: %s", p, e)
    if config.ALLOW_OFFLINE_FALLBACK:
        return normalise(offline()), "offline-rules"
    raise AIError("All AI providers failed or are not configured. " + " | ".join(errors))


def test_provider(p):
    """Real connectivity check used by the admin Settings page."""
    if not configured(p):
        return False, "API key not set"
    try:
        out = parse_json(CALLERS[p](SYSTEM, 'Return exactly this JSON: {"ok": true}'))
        return bool(out.get("ok")), "Connected"
    except Exception as e:  # noqa: BLE001
        return False, str(e)[:200]


# ---------------------------------------------------------------- normalisers
def _list(v):
    if v is None:
        return []
    if isinstance(v, str):
        return [v] if v.strip() else []
    out = []
    for x in v:
        out.append("; ".join(f"{k}: {val}" for k, val in x.items()) if isinstance(x, dict) else str(x))
    return [s for s in out if s.strip()]


def _num(v, lo=0, hi=100, default=0):
    try:
        return max(lo, min(hi, round(float(v), 1)))
    except (TypeError, ValueError):
        return default


def _norm_task(t):
    t = t if isinstance(t, dict) else {}
    diff = str(t.get("difficulty", "Medium")).title()
    return {
        "title": str(t.get("title") or "Internship Task").strip(),
        "description": str(t.get("description") or "").strip(),
        "requirements": _list(t.get("requirements"))[:8],
        "difficulty": diff if diff in ("Easy", "Medium", "Hard") else "Medium",
        "deadline_days": int(_num(t.get("deadline_days"), 2, 7, config.TASK_DAYS)),
        "deliverables": _list(t.get("deliverables")),
        "instructions": str(t.get("instructions") or "Submit your source files (or a zip) plus a short README.").strip(),
    }


def _norm_analysis(a):
    a = a if isinstance(a, dict) else {}
    lvl = str(a.get("skill_level", "Intermediate")).title()
    return {
        "summary": str(a.get("summary") or ""),
        "skill_level": lvl if lvl in ("Beginner", "Intermediate", "Advanced") else "Intermediate",
        "strengths": _list(a.get("strengths")),
        "skill_gaps": _list(a.get("skill_gaps")),
        "focus_areas": _list(a.get("focus_areas")),
    }


def _norm_review0(d):
    return {"analysis": _norm_analysis(d.get("analysis")), "task": _norm_task(d.get("task"))}


def _norm_eval(d):
    return {
        "score": _num(d.get("score")),
        "requirement_coverage": _num(d.get("requirement_coverage")),
        "quality_score": _num(d.get("quality_score")),
        "summary": str(d.get("summary") or ""),
        "strengths": _list(d.get("strengths")),
        "errors": _list(d.get("errors")),
        "missing_requirements": _list(d.get("missing_requirements")),
        "weaknesses": _list(d.get("weaknesses")),
        "corrections": _list(d.get("corrections")),
        "suggestions": _list(d.get("suggestions")),
        "next_task_recommendation": str(d.get("next_task_recommendation") or ""),
    }


def _norm_final(d):
    trend = str(d.get("performance_trend", "Stable")).title()
    ready = str(d.get("readiness", "Nearly ready"))
    return {
        "summary": str(d.get("summary") or ""),
        "performance_trend": trend if trend in ("Improving", "Stable", "Declining") else "Stable",
        "strengths": _list(d.get("strengths")),
        "weaknesses": _list(d.get("weaknesses")),
        "key_improvements": _list(d.get("key_improvements")),
        "predicted_performance": str(d.get("predicted_performance") or ""),
        "predicted_score": _num(d.get("predicted_score")),
        "readiness": ready,
        "recommendations": _list(d.get("recommendations")),
        "next_tasks": _list(d.get("next_tasks")),
    }


# ---------------------------------------------------------------- prompts
def _profile(c):
    return (f"Name: {c['name']}\nRole: {c['role']}\nDepartment: {c['department']}\n"
            f"Skills: {c['skills']}\nExperience: {c['experience']}")


TASK_SHAPE = ('{"title": str, "description": str (3-5 sentences), "requirements": [str, 4-7 concrete testable items], '
              '"difficulty": "Easy|Medium|Hard", "deadline_days": int 2-7, "deliverables": [str], "instructions": str}')


def review0(c):
    prompt = (f"Candidate profile:\n{_profile(c)}\n\nDo two things:\n"
              "1. Analyse the candidate's skills, role and experience.\n"
              "2. Design a personalised FIRST internship task that fits their role, skills and experience level.\n"
              'Return JSON: {"analysis": {"summary": str, "skill_level": "Beginner|Intermediate|Advanced", '
              '"strengths": [str], "skill_gaps": [str], "focus_areas": [str]}, "task": ' + TASK_SHAPE + "}")
    return ask(prompt, _norm_review0, lambda: _offline_review0(c))


def _history_text(history):
    out = []
    for h in history:
        ev = h["evaluation"]
        out.append(f"--- Review {h['review_no']} (score {h['score']}) task: {h.get('task_title','')}\n"
                   f"Weaknesses: {ev.get('weaknesses')}\nErrors: {ev.get('errors')}\n"
                   f"Missing requirements: {ev.get('missing_requirements')}\nSuggestions: {ev.get('suggestions')}\n"
                   f"Recommended next task: {ev.get('next_task_recommendation')}")
    return "\n".join(out)


def next_task(c, review_no, history):
    goal = ("a PERSONALISED IMPROVEMENT task that directly targets the weaknesses, errors and missing requirements "
            "from the previous review. It must not repeat the previous task." if review_no == 2 else
            "the FINAL comprehensive task combining everything learned. It should be slightly harder and verify that "
            "earlier feedback from Review 1 and Review 2 has been applied.")
    prompt = (f"Candidate profile:\n{_profile(c)}\n\nPrevious review results:\n{_history_text(history)}\n\n"
              f"Design {goal}\nReturn JSON: " + TASK_SHAPE)
    return ask(prompt, _norm_task, lambda: _offline_next_task(c, review_no, history))


def evaluate(c, review, bundle, late):
    reqs = json.loads(review["requirements"] or "[]")
    prompt = (f"Candidate profile:\n{_profile(c)}\n\nTASK: {review['task_title']}\n{review['task_description']}\n"
              "REQUIREMENTS:\n" + "\n".join(f"- {r}" for r in reqs) +
              f"\nDifficulty: {review['difficulty']}\nSubmitted after deadline: {'YES' if late else 'no'}\n"
              "STATIC ANALYSIS FINDINGS:\n" + ("\n".join(bundle['findings']) or "none") +
              f"\n\nSUBMISSION CONTENT{' (truncated)' if bundle['truncated'] else ''}:\n{bundle['text'][:45000]}\n\n"
              "Evaluate the submission strictly against the task and requirements. Be evidence-based: cite file names / "
              "line numbers for errors. An empty or irrelevant submission must score below 20.\n"
              'Return JSON: {"score": 0-100, "requirement_coverage": 0-100, "quality_score": 0-100, "summary": str, '
              '"strengths": [str], "errors": [str: "file/line - problem - fix"], "missing_requirements": [str], '
              '"weaknesses": [str], "corrections": [str], "suggestions": [str], "next_task_recommendation": str}')
    ev, provider = ask(prompt, _norm_eval, lambda: _offline_evaluate(reqs, bundle, late))
    ev["static_findings"] = bundle["findings"]
    ev["late"] = bool(late)
    return ev, provider


def final_assessment(c, history, metrics):
    prompt = (f"Candidate profile:\n{_profile(c)}\n\nAll review results:\n{_history_text(history)}\n\n"
              f"Computed metrics: scores={metrics['scores']}, average={metrics['average']}, "
              f"improvement from first to last review={metrics['improvement_pct']}%.\n"
              "Compare the performance across reviews, predict future performance and give a final evaluation.\n"
              'Return JSON: {"summary": str, "performance_trend": "Improving|Stable|Declining", "strengths": [str], '
              '"weaknesses": [str], "key_improvements": [str], "predicted_performance": str, "predicted_score": 0-100, '
              '"readiness": "Ready|Nearly ready|Needs more training", "recommendations": [str], "next_tasks": [str]}')
    return ask(prompt, _norm_final, lambda: _offline_final(c, history, metrics))


# ---------------------------------------------------------------- offline rule engine (last-resort fallback)
TEMPLATES = [
    (("machine learning", "data scientist"), "Customer Churn Prediction Model",
     "Build and evaluate a machine-learning model that predicts customer churn from a tabular dataset.",
     ["Load and clean a churn dataset (handle missing values)", "Perform exploratory analysis with at least 3 plots",
      "Train two classification models and compare them", "Report accuracy, precision, recall and a confusion matrix",
      "Explain the most important features", "Document findings in a README"]),
    (("data analy",), "Sales Data Cleaning and Insights Report",
     "Clean a messy sales dataset and produce an insights report that a manager could act on.",
     ["Clean duplicates, nulls and inconsistent formats with Pandas", "Compute monthly revenue and top 5 products",
      "Create at least 3 charts with clear titles", "Write 5 business insights with supporting numbers",
      "Include SQL queries that reproduce two key metrics"]),
    (("data engineer", "etl", "spark", "airflow"), "ETL Pipeline with Data Quality Checks",
     "Design a small ETL pipeline that ingests CSV data, validates it and loads it into a SQL database.",
     ["Extract data from at least two CSV sources", "Validate schema, nulls and duplicates with logged results",
      "Transform data into a clean star-style schema", "Load into SQLite or PostgreSQL idempotently",
      "Add error handling and logging", "Document how to run the pipeline"]),
    (("devops", "cloud", "infrastructure"), "Containerise and Automate a Web App Pipeline",
     "Containerise a simple web application and automate its build and test with a CI workflow.",
     ["Write a Dockerfile that builds and runs the app", "Add a docker-compose file with the app and a database",
      "Create a CI workflow that installs, tests and builds on every push", "Use environment variables for configuration",
      "Document deployment steps and rollback"]),
    (("qa", "test"), "Test Automation Suite for a Login Module",
     "Create an automated test suite for a web login flow covering positive and negative paths.",
     ["Write at least 10 test cases in a test-case document", "Automate the cases with PyTest and Selenium or Requests",
      "Cover invalid password, empty fields and lockout scenarios", "Generate a test report",
      "Report two defects with clear reproduction steps"]),
    (("security", "cyber"), "Web Application Vulnerability Assessment",
     "Assess an intentionally vulnerable web application and report the findings professionally.",
     ["Identify at least 5 vulnerabilities mapped to the OWASP Top 10", "Provide reproduction steps for each finding",
      "Rate severity (CVSS style) for every issue", "Recommend concrete fixes",
      "Write an executive summary for non-technical readers"]),
    (("java", "spring"), "Library Management REST API (Java)",
     "Build a REST API for managing books and members with Spring Boot and a relational database.",
     ["CRUD endpoints for books and members", "Input validation with meaningful error responses",
      "Persist data with JPA and MySQL or H2", "Write at least 5 JUnit tests", "Provide API documentation or a Postman collection"]),
    (("full stack", "fullstack"), "Full-Stack Notes App with Authentication",
     "Build a notes application with user registration, login and per-user notes.",
     ["Register and login with hashed passwords", "CRUD API for notes protected by authentication",
      "React or HTML front end consuming the API", "Persist data in a database", "Handle errors and validate input",
      "README with setup steps"]),
    (("frontend", "front end", "ui"), "Responsive Task Manager Web App",
     "Build a responsive task-manager interface with filtering and local persistence.",
     ["Add, edit, complete and delete tasks", "Filter tasks by status", "Responsive layout for mobile and desktop",
      "Persist tasks in localStorage", "Accessible form labels and keyboard support"]),
    (("python", "backend", "developer"), "Student Records REST API with Flask",
     "Build a small REST API that manages student records backed by SQLite.",
     ["CRUD endpoints for students", "Input validation and clear error codes", "Use SQLite with parameterised queries",
      "Add at least 5 unit tests", "README explaining setup and endpoints"]),
]
SKILL_FALLBACK = [
    (("scikit", "tensorflow", "pytorch"), 0), (("pandas", "power bi", "tableau"), 1), (("docker", "kubernetes"), 3),
    (("selenium", "pytest"), 4), (("react", "javascript", "css"), 8), (("flask", "django", "fastapi"), 9),
]
GENERIC = ("Mini Project: Problem-Solving Tool",
           "Build a small tool relevant to your role and document how it works.",
           ["Clearly define the problem", "Implement a working solution", "Handle invalid input", "Add basic tests or checks",
            "Write a README with setup and usage"])


def _template(c):
    role = f"{c['role']} {c['department']}".lower()
    for keys, *rest in TEMPLATES:
        if any(k in role for k in keys):
            return rest
    skills = c["skills"].lower()
    for keys, idx in SKILL_FALLBACK:
        if any(k in skills for k in keys):
            return list(TEMPLATES[idx][1:])
    return list(GENERIC)


def _months(text):
    total = 0.0
    for num, unit in re.findall(r"(\d+(?:\.\d+)?)\s*(year|yr|month)", text.lower()):
        total += float(num) * (12 if unit.startswith("y") else 1)
    return total or 6


def _level(c):
    m = _months(c["experience"])
    return "Easy" if m < 6 else "Medium" if m < 18 else "Hard"


def _offline_review0(c):
    title, desc, reqs = _template(c)
    skills = [s.strip() for s in c["skills"].split(",") if s.strip()]
    level = {"Easy": "Beginner", "Medium": "Intermediate", "Hard": "Advanced"}[_level(c)]
    return {
        "analysis": {
            "summary": f"{c['name']} is a {c['role']} candidate with {c['experience']}. Core skills: {', '.join(skills[:5])}.",
            "skill_level": level, "strengths": skills[:3],
            "skill_gaps": ["Testing and documentation habits to be verified", "Production-grade error handling"],
            "focus_areas": ["Code quality", "Requirement coverage", "Documentation"]},
        "task": {"title": title, "description": desc, "requirements": reqs, "difficulty": _level(c),
                 "deadline_days": config.TASK_DAYS, "deliverables": ["Source files or zip", "README.md"],
                 "instructions": "Submit your source files (or one zip) and a README explaining how to run your work."}}


def _offline_next_task(c, review_no, history):
    title, desc, reqs = _template(c)
    last = history[-1]["evaluation"] if history else {}
    fixes = (last.get("missing_requirements") or []) + (last.get("corrections") or []) + (last.get("suggestions") or [])
    fixes = list(dict.fromkeys(fixes))
    if review_no == 2:
        return {"title": f"Improvement Task: {title} (v2)",
                "description": "Revise and extend your previous work. Focus on these weaknesses: "
                               + ("; ".join(last.get("weaknesses", [])[:3]) or "overall polish and completeness") + ".",
                "requirements": (fixes[:5] or reqs[:4]) + ["Add a CHANGELOG listing every fix you made"],
                "difficulty": _level(c), "deadline_days": config.TASK_DAYS,
                "deliverables": ["Updated source", "CHANGELOG.md"], "instructions": "Show clearly how each feedback item was addressed."}
    harder = {"Easy": "Medium", "Medium": "Hard", "Hard": "Hard"}[_level(c)]
    return {"title": f"Final Capstone: {title}",
            "description": "Deliver a polished, production-quality version of your project that applies all earlier feedback. " + desc,
            "requirements": reqs[:5] + ["Resolve every issue raised in Review 1 and Review 2", "Include tests and a clear README"],
            "difficulty": harder, "deadline_days": config.TASK_DAYS,
            "deliverables": ["Final source", "README.md", "Short demo notes"], "instructions": "This task is evaluated for the final report."}


_STOP = {"should", "must", "using", "include", "implement", "create", "least", "with", "that", "from", "their", "which",
         "every", "other", "about", "there", "where", "write", "clear", "basic", "least", "least", "add", "each", "make"}


def _kw(text):
    return {w for w in re.findall(r"[a-z][a-z0-9_\-]{3,}", text.lower()) if w not in _STOP}


def _offline_evaluate(reqs, bundle, late):
    text = bundle["text"]
    low = text.lower()
    covered, missing = 0, []
    for r in reqs:
        kws = _kw(r)
        hit = sum(1 for k in kws if k in low)
        if kws and hit / len(kws) >= 0.4:
            covered += 1
        else:
            missing.append(r)
    coverage = 100 * covered / len(reqs) if reqs else 50
    findings = bundle["findings"]
    syntax = sum(1 for f in findings if "SYNTAX ERROR" in f)
    todos = sum(1 for f in findings if "TODO" in f)
    other = len(findings) - syntax - todos
    score = 30 + 0.5 * coverage + min(10, len(text) / 800) - 10 * syntax - min(10, 2 * todos) - min(8, 2 * other)
    if len(text) < 80:
        score = min(score, 15)
    score = max(0, min(100, score))
    quality = max(0, min(100, 70 - 12 * syntax - 4 * other - 2 * todos + min(20, len(text) / 500)))
    strengths, weaknesses = [], []
    if coverage >= 70:
        strengths.append("Covers most of the stated requirements")
    if syntax == 0 and any(n.lower().endswith(".py") for n in bundle["file_names"]):
        strengths.append("Python files parse without syntax errors")
    if len(text) > 3000:
        strengths.append("Substantial, detailed submission")
    if not strengths:
        strengths.append("Submission received and readable")
    if missing:
        weaknesses.append(f"{len(missing)} of {len(reqs)} requirements show no clear evidence in the submission")
    weaknesses += [f for f in findings[:4]]
    if late:
        weaknesses.append("Submitted after the deadline")
    return {
        "score": round(score, 1), "requirement_coverage": round(coverage, 1), "quality_score": round(quality, 1),
        "summary": f"[Offline rule engine - no AI API was reachable] Estimated coverage {coverage:.0f}% of requirements; "
                   f"{len(findings)} static finding(s).",
        "strengths": strengths, "errors": [f for f in findings if "SYNTAX" in f or "unsafe" in f or "secret" in f],
        "missing_requirements": missing, "weaknesses": weaknesses or ["No major weakness detected by rules"],
        "corrections": [f"Fix: {f}" for f in findings[:5]] or ["Re-read each requirement and add explicit evidence for it"],
        "suggestions": ["Add a README with setup and usage", "Add input validation and error handling", "Add automated tests"],
        "next_task_recommendation": ("Address: " + missing[0]) if missing else "Extend the project with tests and documentation",
    }


def _offline_final(c, history, metrics):
    scores = [metrics["scores"][k] for k in sorted(metrics["scores"])]
    first, last, imp = scores[0], scores[-1], metrics["improvement_pct"]
    trend = "Improving" if imp >= 5 else "Declining" if imp <= -5 else "Stable"
    pred = max(0, min(100, last + 0.25 * (last - first)))
    ready = "Ready" if pred >= 75 else "Nearly ready" if pred >= 55 else "Needs more training"
    strengths = list(dict.fromkeys(s for h in history for s in h["evaluation"].get("strengths", [])))[:5]
    weaknesses = list(dict.fromkeys(s for h in history[-1:] for s in h["evaluation"].get("weaknesses", [])))[:5]
    return {
        "summary": f"{c['name']} scored {', '.join(str(s) for s in scores)} across the reviews (average {metrics['average']}). Trend: {trend.lower()}.",
        "performance_trend": trend, "strengths": strengths, "weaknesses": weaknesses,
        "key_improvements": [f"Overall score changed by {imp}% from first to last review"],
        "predicted_performance": f"If the current trend continues, the expected next-task score is about {pred:.0f}/100.",
        "predicted_score": round(pred, 1), "readiness": ready,
        "recommendations": ["Keep practising on real projects", "Continue adding tests and documentation"],
        "next_tasks": [history[-1]["evaluation"].get("next_task_recommendation") or "Contribute to a small open-source project"]}
