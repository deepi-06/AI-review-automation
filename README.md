# AI Review Automation System

AI-driven internship management: upload candidates from Excel, let **Gemini / OpenAI** allocate personalised tasks,
auto-email candidates, create **Google Calendar + Meet** review meetings, evaluate submissions through
**Review 0 → 3**, and produce a **PDF final report** that the admin approves.

**Stack:** Python, Streamlit, Gemini API, OpenAI API, SQLite, Pandas, OpenPyXL, Gmail SMTP, Google Calendar API,
APScheduler, Plotly, ReportLab.

The responsive white, navy and light-grey interface is configured in `.streamlit/config.toml` and `styles.css`.

## 1. Setup (VS Code terminal)

Open the folder in VS Code (`File > Open Folder`), then `Terminal > New Terminal`.

**Windows (PowerShell)**
```powershell
py -3.10 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.\.venv\Scripts\python.exe -m streamlit run app.py
```
**macOS / Linux**
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install --upgrade pip
./.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
./.venv/bin/python -m streamlit run app.py
```
Edit `.env` (next section) before starting the app. Always use the project virtual environment's Python to install dependencies and run Streamlit; this keeps the app isolated from other projects and avoids Windows file-lock conflicts when another Streamlit server is running.

## 2. Configure `.env` (nothing is hard-coded)

| Variable | What to put |
|---|---|
| `ADMIN_PASSWORD` | Any strong password – used for Admin login (required) |
| `AI_PROVIDER` | `gemini` or `openai` = primary. The other is the automatic fallback (also switchable in the Admin sidebar) |
| `GEMINI_API_KEY` | Free key from https://aistudio.google.com/app/apikey |
| `OPENAI_API_KEY` | From https://platform.openai.com/api-keys |
| `GEMINI_MODEL` / `OPENAI_MODEL` | Change if a model name is retired (defaults: `gemini-3.8-flash`, `gpt-4o-mini`) |
| `ALLOW_OFFLINE_FALLBACK` | `true` = if both APIs fail, a built-in rule engine keeps the demo running (evaluations are labelled `offline-rules`). `false` = real AI only |
| `SMTP_USER`, `SMTP_APP_PASSWORD` | Gmail address + **App Password** (Google Account → Security → 2-Step Verification → App passwords) |

Fallback chain: **primary API → other API → offline rules**. Use *Admin → Settings & Logs → Test … connection* to verify keys.

Troubleshooting AI provider errors: `HTTP 404` from Gemini can indicate that `GEMINI_MODEL` is no longer available; change it in `.env` to a model enabled for your API account. `HTTP 429` from OpenAI with `insufficient_quota` means the API account has no available credits or quota; restore API billing/quota or leave `ALLOW_OFFLINE_FALLBACK=true` to use the built-in offline rule engine when providers fail.

### Google Calendar / Meet (optional but recommended)
1. https://console.cloud.google.com → create a project → **APIs & Services → Library → enable "Google Calendar API"**.
2. **OAuth consent screen**: External, add your Gmail as a *Test user*.
3. **Credentials → Create credentials → OAuth client ID → Desktop app** → download JSON → save as `credentials.json` in the project folder.
4. Run once: `python authorize_calendar.py` (browser opens, approve access). This creates `token.json`.

Without this step, the app still works: it creates a real, working **Jitsi Meet** link instead (shown as "Jitsi fallback" in Settings).

## 3. Demo flow (5 minutes)
1. Login as **Admin** → **Candidates** → upload `sample_candidates.xlsx` → *Import*. Re-importing a Candidate ID, including different letter casing, updates its profile instead of creating a second candidate or changing their review history.
   (Edit a few emails to your own inboxes first if you want to receive real emails.)
2. **AI Task Allocation** → *Run Review 0* → AI analyses every candidate and assigns Task 1, schedules the review, emails them.
3. Open a second browser window → **Candidate login** (email + ID, e.g. `C001`) → *Submit Work* → upload a `.py`, `.pdf`, `.docx` or `.zip` → AI evaluation appears in *AI Feedback*.
4. Review 2 (personalised improvement task) and Review 3 (final task) are generated automatically after each evaluation. Repeat the submission.
5. After Review 3 a PDF report is built → Admin → **Final Reports & Approval** → download and **Approve** (candidate is emailed the PDF).
6. **Ranking & Charts** shows ranking, improvement % and score progression.

To test only a subset, open **AI Task Allocation**, select the candidates to include (or use **Select All** / **Deselect All**), then choose **Assign Tasks to Selected Candidates**. Only selected candidates whose status is **Registered** receive initial tasks and task emails; candidates already in a workflow are not assigned duplicate initial tasks. Stalled-workflow retries are also limited to the selected candidates.

Status of each final report: **Completed** (average ≥ 70 and last ≥ 70), **Completed with Improvement** (last ≥ 60 and improvement ≥ 15 %), otherwise **Needs Improvement**.

## 4. Test the whole workflow without any keys
```bash
python tests/test_workflow.py
```
Runs Review 0–3 end-to-end on a temporary database (import, AI allocation, submissions incl. zip, syntax-error detection, auto next tasks, PDF, approval, reminders, provider fallback with mocked HTTP).

## 5. Project structure
```
app.py               Streamlit dashboard (Admin + Candidate)
styles.css           Responsive dashboard styling and subtle interaction animations
.streamlit/config.toml  Shared light theme and color palette
workflow.py          Review 0–3 orchestration, metrics, approval
ai_engine.py         Gemini + OpenAI clients, fallback, prompts, offline rules
file_parser.py       Reads py/js/java/…/pdf/docx/ipynb/zip + Python static analysis
email_service.py     Gmail SMTP + HTML templates (all sends logged)
calendar_service.py  Google Calendar API + Meet links (Jitsi fallback)
authorize_calendar.py  One-time Google OAuth
reminders.py / scheduler.py  APScheduler reminders (emails + in-app)
report.py            ReportLab PDF
database.py, excel_utils.py, config.py, utils.py
sample_candidates.xlsx   10 sample candidates (fictional)
```

## 6. Notes
- Reset all data: stop the app and delete `data/review_system.db` (and the `uploads/`, `reports/` contents).
- Secrets live only in `.env`, `token.json`, `credentials.json` – all are git-ignored.
- Uploads: max 10 MB total, text/code/pdf/docx/ipynb/zip only; zip files are read in memory (never extracted to disk).
- Troubleshooting: *emails not sent* → see Admin → Settings & Logs → Email log. *AI evaluation failed* → use **Retry AI evaluation** (candidate) or **Resume stalled workflows** (admin).
