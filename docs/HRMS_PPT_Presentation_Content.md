# HRMS — PPT Presentation Content
## Copy each section into one PowerPoint slide

---

## Slide 1 — Title Page

**Human Resource Management System (HRMS)**  
*Web-Based Payroll & HR Portal for College Faculty*

| Field | Fill in |
|--------|---------|
| **Student Name** | _________________________ |
| **Registration Number** | _________________________ |
| **Guide Name** | _________________________ |
| **Institution** | Dr. B. B. Hegde First Grade College, Kundapura |
| **Department** | _________________________ |
| **Academic Year** | 2025–2026 |

---

## Slide 2 — Introduction

- Colleges manage **faculty records, attendance, timetables, leave, and payroll** manually or across separate tools.
- **HRMS** is a unified **web application** that digitizes these processes for **administrators** and **teaching / non-teaching faculty**.
- Built with **Python (Flask)**, **MongoDB**, **HTML/CSS/JavaScript**, and **real-time features (Socket.IO)**.
- Supports **role-based login**, digital timetables, leave with class substitution, salary slips with **email**, **AI chatbot**, and **internal staff messaging** (WhatsApp-style UI).
- Accessible on **desktop and mobile** (responsive UI + optional Android WebView APK).

**Speaker note:** One platform replaces paper registers, Excel sheets, and scattered communication.

**Full overview (all modules, timetable AI time/cost, security):** see `docs/HRMS_Overview.md`

---

## Slide 3 — Objectives

1. To design and develop a **centralized HRMS** for faculty and admin users.
2. To automate **employee management**, **attendance tracking**, and **digital timetables**.
3. To implement a **structured leave workflow** with **class substitution** and **HOD approval**.
4. To provide **payroll / salary slip** generation and **email delivery** to faculty.
5. To integrate **real-time notifications** (Socket.IO) for assignments and leave updates.
6. To offer an **AI-powered HR assistant** (Ollama) for schedule and leave queries.
7. To enable **secure internal staff chat** (WhatsApp-style interface, in-app messaging).
8. To ensure **security** through login, bcrypt passwords, role access, and CSRF protection.

---

## Slide 4 — Existing System / Problem Statement

| Problem | Impact |
|---------|--------|
| Manual leave forms & registers | Delays, lost papers, no audit trail |
| No link between leave and **class coverage** | Classes missed without substitute planning |
| Timetables on paper / Excel | Hard to update and share |
| Attendance in separate files | No single dashboard for admin |
| Salary slips distributed manually | Time-consuming, easy to miss faculty |
| Communication via personal WhatsApp | No official, logged college channel |
| No role-based digital access | Data privacy and control issues |

**Conclusion:** Existing process is **slow, fragmented, and error-prone**.

---

## Slide 5 — Proposed System

**HRMS — Smart HR · Simple Payroll**

- **Single web portal** for Admin and Faculty.
- **MongoDB** stores users, leaves, timetables, salary, chat, and notifications.
- **Admin module:** staff management, leave approval, timetables upload (AI-assisted), attendance view, salary & email, permissions.
- **Faculty module:** dashboard, timetable, attendance view, apply leave (full / permission), class assignments, salary view, profile.
- **Integrations:**
  - **Email SMTP (Gmail)** — salary slips to faculty inbox.
  - **Staff chat** — in-app messaging (WhatsApp-like UI, not Meta WhatsApp API).
  - **AI chatbot** — HR queries via local Ollama (Mistral).
- **Security:** Flask-Login, bcrypt, CSRF, optional salary extra password.

---

## Slide 6 — Modules

| # | Module | User | Main functions |
|---|--------|------|----------------|
| 1 | **Authentication** | All | Login, logout, role-based access (Admin / Lecturer) |
| 2 | **Admin Dashboard** | Admin | Stats, teaching/non-teaching faculty count, quick links |
| 3 | **Staff Management** | Admin | Add/edit/delete faculty, bulk upload, passwords, HOD assign |
| 4 | **Timetable Management** | Admin, Faculty | Upload PDF/image, **Gemini AI** extraction (~30–40 s/image), JSON, live progress log |
| 5 | **Attendance** | Admin, Faculty | View attendance records & statistics |
| 6 | **Leave Management** | Faculty, Admin | Apply leave, substitution requests, HOD permission, approve/reject |
| 7 | **Class Allocation** | Faculty | Accept/reject substitute class requests (real-time) |
| 8 | **Permissions (Short Leave)** | Faculty, Admin | Time-based permission leave |
| 9 | **Salary / Payroll** | Admin, Faculty | Slip form, PDF, publish, bulk email, faculty view |
| 10 | **Broadcast Notifications** | Admin | College-wide notices with images |
| 11 | **Staff Chat** | Admin, Faculty | Real-time messaging, file share (Socket.IO) |
| 12 | **AI Chatbot** | Admin, Faculty | HR assistant (leave, schedule, attendance context) |
| 13 | **Mobile APK (optional)** | Faculty | Capacitor WebView shell for remote access |

---

## Slide 7 — CFD (Context Flow Diagram)

**Level 0 — System context**

```
                    ┌─────────────────────────────────────┐
   Admin ──────────►│                                     │
                    │         HRMS WEB APPLICATION        │
   Faculty ────────►│    (Flask + MongoDB + Socket.IO)    │
                    │                                     │
   HOD (Faculty) ───►│                                     │
                    └──────────┬────────────┬─────────────┘
                               │            │
              ┌────────────────┼────────────┼────────────────┐
              ▼                ▼            ▼                ▼
        ┌──────────┐    ┌──────────┐  ┌──────────┐   ┌──────────┐
        │ MongoDB  │    │ Gmail    │  │ Ollama   │   │ File     │
        │ Database │    │ SMTP     │  │ (AI)     │   │ Storage  │
        └──────────┘    └──────────┘  └──────────┘   └──────────┘
```

**External entities:** Admin, Faculty, HOD, MongoDB, Gmail SMTP, **Google Gemini** (timetable OCR), **Ollama** (HR chatbot), File System.

---

## Slide 8 — DFD Level 1 (Process: Leave Management)

```
Faculty ──► [1.0 Apply Leave & Assign Classes] ──► MongoDB (leaves, allocations)
                │
                ▼
         Colleague ──► [2.0 Accept/Reject Substitution] ──► Socket.IO notify
                │
                ▼
Faculty ──► [3.0 Request HOD Permission] ──► MongoDB (hod_requests)
                │
                ▼
HOD ──────► [4.0 Approve/Reject HOD] ──► Socket.IO notify applicant
                │
                ▼
Faculty ──► [5.0 Submit Leave] ──► MongoDB (final leave record)
                │
                ▼
Admin ────► [6.0 Approve/Reject Leave] ──► Faculty notification
```

**Data stores:** `users`, `leaves`, `leave_class_allocations`, `hod_requests`, `leave_drafts`, `faculty_notifications`

---

## Slide 9 — DFD Level 1 (Process: Salary & Communication)

```
Admin ──► [1.0 Manage Salary Slip] ──► MongoDB (salaries)
              │
              ▼
Admin ──► [2.0 Publish to Faculty Portal]
              │
              ▼
Admin ──► [3.0 Send Email via SMTP] ──► Gmail ──► Faculty Email
              │
Faculty ◄── [4.0 View / Download Salary Slip]

Faculty ◄──► [5.0 Staff Chat] ◄──► Faculty
              │        Socket.IO + MongoDB (staff_messages)
              ▼
Faculty ──► [6.0 AI Chatbot Query] ──► Ollama ──► Response (stream)
              │        Context from MongoDB (timetable, leave, attendance)
```

---

## Slide 10 — Interface 1: Faculty Dashboard

**Screenshot:** Faculty dashboard (`/lecturer/dashboard`)

**Highlight on slide:**
- Welcome + profile card with photo
- Quick actions: **My Timetable, Attendance, My Assignments, Apply Leave, Permission Leave, My Salary**
- Recent leave history
- **Green button (bottom-left):** Staff chat launcher
- **Blue robot (bottom-right):** AI HRMS Assistant
- Mobile-responsive tile layout

**Caption:** *Unified faculty command center for daily HR tasks.*

---

## Slide 11 — Timetable AI Generation (Gemini)

**What it does:** Reads a **photo/PDF of a printed timetable** → structured JSON → links to faculty in database.

| Item | Detail |
|------|--------|
| **Single image** | 1 upload → **1 Gemini API call** → ~**30–40 seconds** total |
| **Bulk PDF** | Each page split **top + bottom** → **2 API calls per page** |
| **Model** | `gemini-2.5-flash` (`.env`: `GOOGLE_API_KEY`) |
| **After AI** | Python matches name to staff (no extra API) → saves `json_timetables/BBHCFxxx.json` |
| **UI** | Live log: Initializing → Gemini → Extracted name → DB sync → Done |
| **Billing** | Google charges **tokens**, not “1 credit = 1 image” (~$0.01–$0.03/image paid tier) |
| **Retries** | 503/429 may add time + extra calls; keys rotate via `api_keys.txt` |

**Caption:** *AI turns paper timetables into digital data for leave class-checking.*

---

## Slide 12 — Interface 2: Apply Leave + Class Assignment

**Screenshot:** Apply Leave page (`/lecturer/apply_leave`)

**Highlight on slide:**
- Leave type, date range, reason
- **Check classes** — pulls timetable for leave dates
- Per-class **substitute faculty** dropdown + **Send Request**
- Real-time status: Pending (yellow) → Accepted (green) / Rejected (red)
- **HOD permission** section with request button
- Submit enabled only when all classes covered + HOD approved

**Caption:** *Leave workflow ensures every class has a substitute before submission.*

---

## Slide 13 — Admin Dashboard Highlights

- **Stats:** Teaching / non-teaching faculty count, pending leaves.
- **Recent requests:** Sheet → formal leave page with **Approve / Reject** at top.
- **Salary sender email:** Gmail App Password on dashboard (test & save).
- **Quick actions:** Staff, salary, leave, timetabling, attendance, staff chat, broadcast.
- **Real-time:** Socket.IO updates when faculty apply or colleagues accept classes.

**Caption:** *Management control center for HR operations.*

---

## Slide 14 — Conclusion

- Successfully designed and implemented a **web-based HRMS** for college faculty and administration.
- **MongoDB** provides flexible storage; **Flask** delivers secure, modular backend logic.
- **Leave module** with substitution and HOD approval improves academic continuity.
- **Salary module** with PDF and **email** reduces manual distribution effort.
- **Socket.IO** enables real-time class assignments and chat without page refresh.
- **AI chatbot** and **staff messaging** improve user experience and internal communication.
- System is **scalable**, **role-based**, and suitable for **education-sector HR** needs.

---

## Slide 15 — Future Scope

1. **Native mobile app** (full Flutter/React Native) beyond WebView APK.
2. **Biometric / RFID attendance** integration with hardware.
3. **SMS / official WhatsApp Business API** for alerts (optional).
4. **Advanced analytics** — charts for attendance trends, leave patterns.
5. **Multi-department / multi-campus** support.
6. **Cloud deployment** (AWS/Azure) with HTTPS and backup automation.
7. **Gemini/cloud AI** fallback when Ollama is offline.
8. **Two-factor authentication (2FA)** for admin accounts.
9. **Audit logs** for all admin actions.
10. **Integration with college ERP / student systems.**

---

## Slide 16 — References

1. Flask Documentation — https://flask.palletsprojects.com/
2. MongoDB Documentation — https://www.mongodb.com/docs/
3. Flask-SocketIO — https://flask-socketio.readthedocs.io/
4. Flask-Login & Flask-Bcrypt — https://pypi.org/
5. Flask-WTF (CSRF) — https://flask-wtf.readthedocs.io/
6. Ollama — https://ollama.com/
7. Google App Passwords (Gmail SMTP) — https://myaccount.google.com/apppasswords
8. Socket.IO Client — https://socket.io/docs/v4/
9. Tailwind CSS — https://tailwindcss.com/
10. Capacitor (Mobile WebView) — https://capacitorjs.com/
11. Google Gemini API Pricing — https://ai.google.dev/gemini-api/docs/pricing
12. HRMS Full Overview (this project) — `docs/HRMS_Overview.md`

---

## Slide 17 — Thank You

# Thank You

**Questions?**

---

## Optional backup slides (if examiner asks)

### Technology stack
| Layer | Technology |
|-------|------------|
| Frontend | HTML, CSS, JavaScript, Tailwind |
| Backend | Python 3, Flask |
| Database | MongoDB |
| Real-time | Flask-SocketIO |
| Timetable AI | Google Gemini 2.5 Flash |
| HR chatbot | Ollama (Mistral) |
| Email | SMTP (Gmail, port 465 SSL) |
| Security | Bcrypt, CSRF, session login |

### HRMS overview document
All modules, admin/faculty sides, timetable time/API/cost, security, files — **`docs/HRMS_Overview.md`**

### Security methods (12)
Login, bcrypt hashing, sessions, RBAC, salary password gate, CSRF, unique usernames, file upload limits, `.env` secrets, timing-safe compare, MongoDB ObjectId validation, HTTPS (deployment).
