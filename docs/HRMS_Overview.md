# HRMS — Complete System Overview

**Human Resource Management System** for Dr. B. B. Hegde First Grade College, Kundapura  
Web portal for **Admin (Management)** and **Faculty** — payroll, attendance, timetables, leave, messaging, and AI assistant.

---

## 1. What HRMS Does (One Line)

A **single web application** that replaces paper registers, Excel sheets, and scattered WhatsApp for college faculty HR: staff records, digital timetables, leave with class substitution, attendance, salary slips with email, internal chat, and an AI helper.

---

## 2. Users & Roles

| Role | Login | Main access |
|------|--------|-------------|
| **Admin (Management)** | Management Login | Dashboard, staff, timetables (AI upload), leave approve/reject, attendance (all faculty), salary & SMTP, permissions, broadcast, staff chat |
| **Lecturer (Faculty)** | Faculty Login | Dashboard, my timetable, attendance, apply leave / permission, class assignments, salary view, profile, staff chat, AI chatbot |
| **HOD** | Same as faculty (flag on user) | Extra: approve/reject HOD permission step on colleague’s leave |

**Staff ID pattern:** Teaching `BBHCF###` · Non-teaching `BBHCFN###`

---

## 3. Technology Stack

| Layer | Technology |
|-------|------------|
| Frontend | HTML, CSS, JavaScript, Tailwind CSS |
| Backend | Python 3, Flask |
| Database | MongoDB (`hrms_db`) |
| Real-time | Flask-SocketIO |
| Timetable AI | Google Gemini API (`gemini-2.5-flash`) |
| HR chatbot | Ollama (Mistral) — local, not Gemini |
| Email | Gmail SMTP (port **465** SSL) |
| Security | Flask-Login, bcrypt, CSRF (Flask-WTF), session |
| Mobile (optional) | Capacitor WebView APK |

---

## 4. All Modules — Main Things (By Side)

### 4.1 Authentication & Security

- Username + password login; passwords stored with **bcrypt**.
- **Admin** vs **lecturer** routes protected (`admin_required`, `lecturer_required`).
- **CSRF** on forms and AJAX (`csrf_token`, `hrms_csrf.js`).
- **Manage Salary** extra password (`SALARY_ACCESS_PASSWORD` in `.env`).
- Secrets in `.env` (`GOOGLE_API_KEY`, `SMTP_*`, `SECRET_KEY`).

### 4.2 Admin (Management) Dashboard

- Counts: **teaching faculty**, **non-teaching faculty**, **pending leaves**.
- **Recent Leave Requests** table: Faculty, Type, Dates, Status, **Sheet** / **Approve** / **Reject**.
- **Salary slip sender email** (collapsible): Gmail + App Password, test connection, save, use `.env` sender — no salary unlock needed.
- Quick links: Add staff, Manage salary, Leave portal, Timetabling, Generate report, Permission manager, Attendance, Staff chat.
- **Broadcast** global notifications (real-time to faculty).
- Real-time updates via Socket.IO (new leave, status changes).

### 4.3 Staff Management (Admin)

- Add / edit / delete faculty; profile photo, department, designation, email.
- Bulk upload (Excel/CSV).
- Assign **HOD** per department.
- Leave balances per leave type.

### 4.4 Timetable Management (Admin + Faculty View)

**Purpose:** Store each faculty’s weekly timetable as structured JSON for leave “check classes” and displays.

| Mode | UI | What happens |
|------|-----|----------------|
| **Single image** | Timetables → one JPG/PNG | 1 image → **1 Gemini call** → match faculty in DB → save `static/json_timetables/{staff_id}.json` |
| **Bulk PDF** | Timetables → PDF upload | Each page → split **top + bottom** → **1 Gemini call per slice** → save each faculty |

**Live progress (UI):** Dark panel shows logs: Initializing → Sending to Gemini → AI extracted faculty → Syncing with database → Successfully processed. Progress bar 0–100%.

**After processing:** JSON file + MongoDB `timetable` collection; faculty sees timetable on dashboard.

**Edit:** Admin can open JSON timetable editor per staff.

---

### 4.5 Timetable AI — Time, API Calls & Cost (Detail)

#### Flow (single image)

1. Upload image → background thread (page stays open; AJAX).
2. **Initializing** — start worker (no API).
3. **Sending image to Gemini** — one `generate_content` request.
4. **AI extracted faculty** — JSON with name, department, weekly slots.
5. **Syncing with database** — **Python only** (name matching, no API).
6. **Successfully processed** — save JSON + update DB.

#### Typical time (from production logs)

| Stage | Time |
|--------|------|
| Gemini API only | **~25–35 seconds** |
| DB match + save | &lt; 1 second |
| **Total (normal)** | **~30–40 seconds** |

Slow cases: retries on 503 (3s, 6s, 12s), key rotation on 429/403 — can exceed **1–2 minutes**.

#### API calls per operation

| Operation | Gemini calls |
|-----------|----------------|
| 1 faculty image (success) | **1** |
| 1 PDF page (top + bottom) | **2** |
| Name matching / save JSON | **0** |
| Each retry / invalid JSON retry | **+1** |

**Model:** `gemini-2.5-flash` (override with `GEMINI_MODEL` in `.env`).  
**Keys:** `GOOGLE_API_KEY` / `GEMINI_API_KEY` in `.env`; rotation via `api_keys.txt`.

#### Credits / billing (Google — not “1 credit = 1 image”)

Google bills **tokens**, not fixed credits in HRMS.

| | Paid tier (approx.) |
|--|---------------------|
| Input (text + image) | **$0.30 / 1M tokens** |
| Output (JSON) | **$2.50 / 1M tokens** |

**Per successful single image (rough):**

- Long prompt (schema + rules): ~3,000–6,000+ input tokens  
- Timetable image (PNG, high DPI in bulk): ~280–1,120+ tokens  
- JSON output: ~2,000–8,000+ tokens  
- **Estimated cost:** ~**$0.01–$0.03** per image (varies by size and output length)

**Bulk example:** 20 PDF pages → 40 slices → **~40 API calls** → ~40× single-image cost; **4 second delay** between slices to reduce rate limits.

**Free tier:** Daily/minute limits (RPM/RPD) in Google AI Studio — no dollar charge until billing enabled.

**Where to see exact usage:** [Google AI Studio](https://aistudio.google.com/) → usage / billing (HRMS does not show token counts in UI).

#### What Gemini returns

Structured JSON: `faculty`, `faculty_id`, `department`, `timetable` (days Monday–Saturday, periods 0–VII, class/section/subject/lab flags).

#### Matching & files

- Python matches OCR name to MongoDB (`match_and_save`).
- Matched → `static/json_timetables/BBHCFxxx.json`.
- Unmatched → `unknown_1.json`, etc.
- Log file: `reconstruction_log.txt`.

---

### 4.6 Attendance

- **Admin:** Faculty attendance view (all staff).
- **Faculty:** Own attendance view.
- On **leave/permission approve**, system can update attendance log (half-day, permission time window).

### 4.7 Leave Management

**Types:** Full-day leave (CL, EL, etc.), **Permission** (time-based same day).

**Faculty flow:**

1. Reason, type, dates → **Check classes** (reads digital timetable).
2. For each class: pick substitute → **Send Request** (Socket.IO to colleague).
3. Colleague **Accept** (green) / **Reject** (red, reassign).
4. **HOD permission** if required.
5. **Submit** only when all classes approved + HOD OK.
6. Status **Pending** → Management **Approve** / **Reject**.

**Admin:**

- Leave portal (all requests).
- Dashboard recent table + **Sheet** opens formal **Leave Application** PDF-style page.
- **Approve / Reject** on dashboard or **on sheet page top** (same API as dashboard).
- Class allocation sheet for substitution details.

**Data:** `leaves`, `leave_class_allocations`, `leave_drafts`, `hod_requests`, `faculty_notifications`.

### 4.8 Permissions (Short Leave)

- Time-based permission (from–to time on one date).
- Admin **Permission Manager**; approve/reject via API (`/admin/permission/api/...`).
- Counted separately from full leave balances.

### 4.9 Salary / Payroll

**Admin (after salary unlock password):**

- Monthly salary slip form per faculty.
- Publish slip to faculty portal.
- **Bulk email** PDF to faculty Gmail (SMTP).
- Sender settings: Gmail + **16-character App Password** (not normal password); test connection; port **465** SSL.

**Admin dashboard (without salary unlock):**

- Sender email card only — change mailing account for slips.

**Faculty:**

- View / download published slip for month.

**Data:** `salaries` collection; PDF via `utils/salary_pdf.py`; email via `utils/salary_email.py`.

### 4.10 Broadcast Notifications

- Admin posts message (+ optional image).
- Delivered to faculty dashboards in real time.

### 4.11 Staff Chat

- **WhatsApp-style UI** inside HRMS (not Meta WhatsApp Business API).
- Real-time messages + file uploads via Socket.IO.
- MongoDB: `staff_conversations`, `staff_messages`.
- Launcher from faculty/admin dashboard (green button).

### 4.12 AI Chatbot (HR Assistant)

- Floating widget (robot icon).
- **Ollama + Mistral** locally (`chatbot_engine.py`, `/api/chat`).
- Answers using context: timetable, leave, attendance (from DB).
- **Separate from timetable Gemini** — timetable uses Google; chatbot uses Ollama.

### 4.13 Reports & Utilities

- Generate report (admin).
- Timetable history / backups on leave assignment.
- Optional Android APK (WebView to college server URL).

---

## 5. External Systems (Context Diagram)

```
        Admin ──────────┐
        Faculty ────────┤
        HOD ────────────┼──►  HRMS (Flask + MongoDB + Socket.IO)
                        │
        ┌───────────────┼───────────────┬──────────────┬─────────────┐
        ▼               ▼               ▼              ▼             ▼
    MongoDB         Gmail SMTP      Google Gemini    Ollama      File storage
    (all data)      (salary mail)   (timetable OCR)  (chatbot)   (uploads, JSON)
```

---

## 6. Key Files & Folders

| Path | Purpose |
|------|---------|
| `app.py` | Routes, Socket.IO, business logic |
| `utils/timetable_processor.py` | PDF split, Gemini extract, match & save |
| `utils/gemini_runtime.py` | Model name, retries, error handling |
| `utils/salary_email.py` / `salary_pdf.py` | Payroll email & PDF |
| `chatbot_engine.py` | Ollama chatbot |
| `static/json_timetables/` | Per-faculty timetable JSON |
| `reconstruction_log.txt` | Timetable processing log |
| `.env` | API keys, SMTP, secrets |
| `templates/admin/` · `templates/lecturer/` | UI pages |

---

## 7. Security Summary (12 Points)

1. Login required (Flask-Login)  
2. Bcrypt password hashing  
3. Server-side sessions  
4. Role-based access (admin / lecturer)  
5. Salary module extra password  
6. CSRF on POST/AJAX  
7. Unique usernames  
8. Upload size / type limits  
9. Secrets in `.env` (not in code)  
10. Timing-safe comparisons where needed  
11. MongoDB ObjectId validation on IDs  
12. HTTPS recommended in production  

---

## 8. Problems Solved vs Old System

| Old problem | HRMS solution |
|-------------|----------------|
| Paper leave forms | Digital apply + audit trail |
| No class substitute plan | Timetable-linked substitution + real-time accept |
| Paper/Excel timetables | AI extraction to JSON + online view |
| Manual salary distribution | PDF + email to faculty |
| Personal WhatsApp only | Official in-app staff chat |
| Scattered data | One MongoDB + one portal |

---

## 9. Future Scope (Short)

Biometric attendance, SMS/WhatsApp Business API, analytics dashboards, multi-campus, cloud deploy with 2FA, audit logs, ERP integration, Gemini fallback for chatbot when Ollama offline.

---

## 10. Quick Reference Card

| Question | Answer |
|----------|--------|
| Timetable: time for 1 image? | ~30–40 s (Gemini ~25–35 s) |
| Timetable: API calls per image? | 1 (more if retries) |
| Timetable: cost per image? | ~$0.01–$0.03 paid tier (token-based) |
| Chatbot AI? | Ollama (local), not Gemini |
| Timetable AI? | Google Gemini 2.5 Flash |
| Salary email port? | 465 SSL (Gmail App Password) |
| Real-time features? | Socket.IO (leave, chat, assignments, timetable logs) |

---

*For slide-by-slide presentation text, see `docs/HRMS_PPT_Presentation_Content.md`. For leave workflow detail, see `docs/system_workflow.md`.*
