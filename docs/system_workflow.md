# HRMS System Architecture & Leave Workflow

This document outlines the data storage structure and the advanced real-time leave application workflow implemented in the HRMS.

## 1. MongoDB Data Storage

All persistent data is stored in the `hrms_db` on MongoDB.

### a. `users` Collection
Stores all lecturer and administrator accounts.
- `_id`: Unique identifier
- `username`: Login ID
- `name`: Full display name
- `role`: "admin" or "lecturer"
- `staff_id`: Unique college staff code (e.g., BBHCF001)

### b. `leaves` Collection
Stores all finalized leave applications.
- `lecturer_id`: ID of the applicant
- `from_date` / `to_date`: Leave period
- `reason`: Explanation for leave
- `status`: Pending, Approved, Rejected, or Draft
- `mode`: "full" (Multi-day) or "time" (Short leave)

### c. `leave_class_allocations` Collection
Tracks the substitution of classes for each leave request.
- `leave_id`: Associated leave request
- `assigned_by`: Applicant ID
- `assigned_to`: Substitute lecturer ID
- `class_details`: JSON object containing subject, time, and room
- `status`: Pending, Accepted, or Rejected

### d. `faculty_notifications` Collection
Handles real-time alerts and dashboard notifications.
- `recipient_id`: Target lecturer
- `sender_name`: Name of requesting colleague
- `message`: Notification text
- `type`: "class_assignment" or "leave_status"
- `status`: unread or read

---

## 2. Integrated Leave Apply Workflow

The system follows a strict, high-integrity workflow to ensure all classes are covered before management review.

### Step 1: Initialization
1. Lecturer enters **Reason for Leave** (Top of form).
2. Selects **Leave Type** and picks **From/To Dates**.
3. System automatically restores any unsaved work using `localStorage`.

### Step 2: Timetable Processing
1. Lecturer clicks **"Check classes for these dates"**.
2. System calls the Timetable API, converts the Date (e.g., 20/04/2026) into a Day (e.g., Monday).
3. All scheduled subjects for those days are pulled and displayed in a **Grouped Table View**.

### Step 3: Individual Substitutions (Phase 1)
1. For each class, the lecturer selects a colleague and clicks **"Send Request"**.
2. Colleague receives a **Real-time Socket Notification** on their dashboard.
3. The row on the applicant's page turns **Yellow** (Waiting...).

### Step 4: Real-time Response
1. **Approval**: If the colleague "Accepts", the row instantly turns **Green** (Approved) via Socket.io.
2. **Rejection**: If the colleague "Rejects", the row turns **Red**, and the **Reassign** dropdown reappears.

### Step 5: Final Submission (Phase 2)
1. The **"Submit Application"** button remains locked until **ALL** classes are Green (Approved).
2. Once coverage is 100%, the lecturer clicks Submit.
3. The request is finalized and passed to the **Management Login** for administrative approval.

---

## 3. Persistence & Safety
- **Auto-Save**: Form data is cached locally to prevent loss on browser refresh.
- **Draft Mode**: Future implementation includes auto-saving drafts to the `leave_drafts` collection in MongoDB for cross-device synchronization.
