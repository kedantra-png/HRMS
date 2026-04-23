from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_socketio import SocketIO, emit
from io import BytesIO
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from utils.db import users, leaves, salaries, timetable, init_db, db, leave_class_allocations, faculty_notifications, timetable_history, leave_drafts, leave_types
from bson.objectid import ObjectId
import os

app = Flask(__name__)
app.jinja_env.add_extension('jinja2.ext.do')
app.secret_key = os.urandom(24)
socketio = SocketIO(app, async_mode='threading')

bcrypt = Bcrypt(app)
login_manager = LoginManager(app)
login_manager.login_view = 'login'

class User(UserMixin):
    def __init__(self, user_data):
        self.id = str(user_data['_id'])
        self.username = user_data['username']
        self.role = user_data['role']
        self.name = user_data.get('name', '')

@login_manager.user_loader
def load_user(user_id):
    user_data = users.find_one({"_id": ObjectId(user_id)})
    if user_data:
        return User(user_data)
    return None

@app.route('/')
def index():
    if current_user.is_authenticated:
        if current_user.role == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('lecturer_dashboard'))
    return render_template('landing.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        user_data = users.find_one({"username": username})
        
        if user_data and bcrypt.check_password_hash(user_data['password'], password):
            user_obj = User(user_data)
            login_user(user_obj)
            if user_obj.role == 'admin':
                return redirect(url_for('admin_dashboard'))
            return redirect(url_for('lecturer_dashboard'))
        else:
            flash('Invalid username or password', 'danger')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return redirect(url_for('index'))

from utils.auth import admin_required, lecturer_required
from datetime import datetime, timedelta
from utils.timetable_processor import extract_timetable_structure, log_event
from difflib import get_close_matches
import json
import re
import csv
import fitz  # PyMuPDF for optional page-level images
from pathlib import Path

def normalize_name(name: str) -> str:
    """
    Make names comparable between OCR, faculty_detail.json and DB.
    """
    name = (name or "").upper()
    name = re.sub(r"^FACULTY\s*[:\-]\s*", "", name)
    name = re.split(r"\b(MENTOR|TOTAL|DEPARTMENT|DEPT)\b", name, maxsplit=1)[0]
    name = re.sub(r"\b(MR|MRS|MS|MISS|DR|PROF|PROFESSOR)\.?\b", "", name)
    name = re.sub(r"[^A-Z\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name

def surname_key(norm_name: str) -> str:
    parts = (norm_name or "").split()
    return parts[-1] if parts else ""

def partial_match(norm_small: str, norm_big: str) -> bool:
    if not norm_small or not norm_big:
        return False
    if len(norm_small) > len(norm_big):
        norm_small, norm_big = norm_big, norm_small
    if norm_small in norm_big:
        return True
    small_tokens = set(norm_small.split())
    big_tokens = set(norm_big.split())
    return len(small_tokens & big_tokens) >= min(2, len(small_tokens))

def _safe_filename(base: str, fallback: str) -> str:
    base = (base or "").strip().lower()
    base = base.replace("&", "and")
    base = re.sub(r"\s+", "_", base)
    base = re.sub(r"[^a-z0-9_\-]+", "_", base)
    base = re.sub(r"_+", "_", base).strip("._- ")
    if not base:
        base = fallback
    if base in {"con", "prn", "aux", "nul"} or re.fullmatch(r"com[1-9]|lpt[1-9]", base):
        base = f"{fallback}_{base}"
    return base[:80]

def process_timetable_async(pdf_bytes, known_faculty_names, all_lecturers, lecturers_by_staff_id, lecturers_by_norm_name, lecturers_by_surname, norm_json_name_to_staff_id, upload_folder, json_folder):
    try:
        pages_with_name, pages_without_name = pdf_to_faculty_images(
            pdf_bytes, known_faculty_names=known_faculty_names or None
        )
        
        total_pages = len(pages_with_name)
        matched_count = 0
        unmatched_pages = []

        log_event(f"Starting async processing of {total_pages} pages.", socketio=socketio)

        for idx, page in enumerate(pages_with_name):
            progress = int(((idx + 1) / total_pages) * 100)
            faculty_name_raw = page["faculty_name"]
            
            log_event(f"Processing page {idx+1}/{total_pages}: {faculty_name_raw}", socketio=socketio)
            socketio.emit('timetable_progress', {'progress': progress, 'status': f'Processing {faculty_name_raw}...'})

            if not faculty_name_raw:
                continue

            norm_ocr_name = normalize_name(faculty_name_raw)
            lecturer = None

            staff_id = norm_json_name_to_staff_id.get(norm_ocr_name)
            if staff_id:
                lecturer = lecturers_by_staff_id.get(staff_id)
            if not lecturer:
                lecturer = lecturers_by_norm_name.get(norm_ocr_name)
            if not lecturer and lecturers_by_norm_name:
                for norm_name, lect in lecturers_by_norm_name.items():
                    if partial_match(norm_ocr_name, norm_name):
                        lecturer = lect
                        break
            if not lecturer and lecturers_by_norm_name:
                norm_lecturer_names = list(lecturers_by_norm_name.keys())
                best = get_close_matches(norm_ocr_name, norm_lecturer_names, n=1, cutoff=0.6)
                if best:
                    lecturer = lecturers_by_norm_name.get(best[0])
            if not lecturer:
                sk = surname_key(norm_ocr_name)
                if sk:
                    candidates = lecturers_by_surname.get(sk, [])
                    if len(candidates) == 1:
                        lecturer = candidates[0]

            if not lecturer:
                unmatched_pages.append({
                    "faculty_name": faculty_name_raw,
                    "normalized_name": norm_ocr_name,
                    "page_index": page["page_index"],
                })
                log_event(f"FAILED to match lecturer for: {faculty_name_raw}", socketio=socketio)
                continue

            matched_display_name = lecturer.get("name", faculty_name_raw)
            log_event(f"Matched to: {matched_display_name}", socketio=socketio)

            fallback = f"lect_{str(lecturer['_id'])}"
            safe_name = _safe_filename(matched_display_name, fallback=fallback)
            filename = f"{safe_name}.png"
            fs_image_path = os.path.join(upload_folder, filename)
            page["image"].save(fs_image_path, format="PNG")
            url_image_path = f"timetables/{filename}"

            log_event(f"Extracting structure via Gemini for {matched_display_name}...", socketio=socketio)
            structured = extract_timetable_structure(page["image"], faculty_name_hint=matched_display_name, socketio=socketio)
            
            timetable.update_one(
                {"lecturer_id": str(lecturer["_id"])},
                {
                    "$set": {
                        "lecturer_id": str(lecturer["_id"]),
                        "lecturer_name": matched_display_name,
                        "image_path": url_image_path,
                        "structured": structured or {},
                        "uploaded_at": datetime.now(),
                    }
                },
                upsert=True,
            )
            # Save to separate JSON file by staff_id
            staff_id = lecturer.get("staff_id")
            if staff_id:
                json_filename = f"{staff_id}.json"
                json_path = os.path.join(json_folder, json_filename)
                try:
                    with open(json_path, "w", encoding="utf-8") as jf:
                        json.dump(structured or {}, jf, indent=2, ensure_ascii=False)
                    log_event(f"JSON saved: {json_filename}", socketio=socketio)
                except Exception as je:
                    log_event(f"Failed to save JSON for {staff_id}: {je}", socketio=socketio)

            matched_count += 1
            log_event(f"SUCCESS: Saved timetable for {matched_display_name}", socketio=socketio)

        final_msg = f"Completed. Matched {matched_count}/{total_pages} timetables."
        log_event(final_msg, socketio=socketio)
        socketio.emit('timetable_progress', {'progress': 100, 'status': final_msg, 'done': True})

    except Exception as e:
        err_msg = f"Async Error: {str(e)}"
        log_event(err_msg, socketio=socketio)
        socketio.emit('timetable_progress', {'progress': 0, 'status': err_msg, 'error': True})

# Leave Types Management Routes
@app.route('/admin/api/leave-types', methods=['GET'])
@login_required
@admin_required
def get_leave_types_api():
    types = list(leave_types.find().sort("name", 1))
    # Calculate usage stats for each type
    all_lecturers = list(users.find({"role": "lecturer"}))
    
    result = []
    for t in types:
        name = t['name']
        # Unify to leave_balances
        total_allocated = sum(float(l.get('leave_balances', {}).get(name, 0)) for l in all_lecturers)
        # Also count how many lecturers have this type
        in_use_count = sum(1 for l in all_lecturers if name in l.get('leave_balances', {}))
        
        result.append({
            "id": str(t['_id']),
            "name": name,
            "total_allocated": total_allocated,
            "in_use_count": in_use_count
        })
    return jsonify(result)

@app.route('/admin/api/leave-types', methods=['POST'])
@login_required
@admin_required
def add_leave_type():
    data = request.get_json()
    name = data.get('name', '').strip()
    if not name:
        return jsonify({"success": False, "message": "Name is required"}), 400
    if leave_types.find_one({"name": name}):
        return jsonify({"success": False, "message": "Type already exists"}), 400
    leave_types.insert_one({"name": name})
    socketio.emit('leave_types_updated')
    return jsonify({"success": True})

@app.route('/admin/api/leave-types/<name>', methods=['DELETE'])
@login_required
@admin_required
def delete_global_leave_type(name):
    # 1. Remove from global list
    res = leave_types.delete_one({"name": name})
    
    # 2. CASCADING DELETE: Remove this type from all lecturers' balances
    if res.deleted_count > 0:
        all_lecturers = users.find({"role": "lecturer", f"leave_balances.{name}": {"$exists": True}})
        for l in all_lecturers:
            balances = l.get("leave_balances", {})
            if name in balances:
                del balances[name]
                users.update_one({"_id": l["_id"]}, {"$set": {"leave_balances": balances}})
    
    socketio.emit('leave_types_updated')
    return jsonify({"success": res.deleted_count > 0})

@app.route('/admin/api/leave-types/cleanup', methods=['POST'])
@login_required
@admin_required
def cleanup_unused_types():
    # Find all types currently in use by any lecturer
    in_use = set()
    for l in users.find({"role": "lecturer"}):
        balances = l.get('leave_balances', {})
        for k, v in balances.items():
            try:
                if float(v) > 0: in_use.add(k)
            except: continue
            
    # Delete global types not in use
    res = leave_types.delete_many({"name": {"$nin": list(in_use)}})
    socketio.emit('leave_types_updated')
    return jsonify({"success": True, "deleted_count": res.deleted_count})

@app.route('/admin/api/leave-types/clear-all', methods=['POST'])
@login_required
@admin_required
def clear_all_leave_config():
    """Wipe all types and all associated balcony allocations (Full Reset)"""
    # 1. Clear global types
    leave_types.delete_many({})
    
    # 2. Clear all leave_balances from all lecturers
    users.update_many(
        {"role": "lecturer"},
        {"$unset": {"leave_balances": "", "leaves_per_month": ""}}
    )
    
    socketio.emit('leave_types_updated')
    return jsonify({"success": True, "message": "All leave categories and allocations cleared."})

# Admin Routes
@app.route('/admin/dashboard')
@login_required
@admin_required
def admin_dashboard():
    stats = {
        "staff_count": users.count_documents({"role": "lecturer"}),
        "pending_leaves": leaves.count_documents({"status": "Pending"}),
        "timetable_entries": timetable.count_documents({})
    }
    # Only show recent PENDING leaves in the dashboard widget
    recent_leaves = list(leaves.find({"status": "Pending"}).sort("_id", -1).limit(5))

    # Pre-serialize recent leaves for use in inline JS (ObjectId is not JSON serializable)
    recent_leaves_serialized = [
        {
            "id": str(doc.get("_id")),
            "lecturer_name": doc.get("lecturer_name", ""),
            "type": doc.get("type", ""),
            "from_date": doc.get("from_date", ""),
            "to_date": doc.get("to_date", ""),
            "status": doc.get("status", ""),
            "half_day": doc.get("half_day", False),
            "session": doc.get("session", "")
        }
        for doc in recent_leaves
    ]

    return render_template(
        'admin/dashboard.html',
        stats=stats,
        recent_leaves=recent_leaves,
        recent_leaves_serialized=recent_leaves_serialized,
    )


@app.route('/admin/api/recent-leaves')
@login_required
@admin_required
def admin_api_recent_leaves():
    """
    Small JSON API for polling recent pending leaves on the dashboard
    (used for near real-time updates without a full page refresh).
    """
    items = []
    for doc in leaves.find({"status": "Pending"}).sort("_id", -1).limit(5):
        items.append({
            "id": str(doc.get("_id")),
            "lecturer_name": doc.get("lecturer_name", ""),
            "type": doc.get("type", ""),
            "from_date": doc.get("from_date", ""),
            "to_date": doc.get("to_date", ""),
            "status": doc.get("status", ""),
        })
    return jsonify(items)

@app.route('/admin/staff')
@login_required
@admin_required
def manage_staff():
    # Always show lecturers sorted by Staff ID (BBHCF001, BBHCF002, ...)
    all_staff = list(users.find({"role": "lecturer"}).sort("staff_id", 1))
    return render_template('admin/manage_staff.html', staff=all_staff)

@app.route('/admin/staff/new', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_staff_new():
    error = None
    form = {
        "staff_id": "",
        "name": "",
        "designation": "",
        "department": "",
        "category": "Teaching Faculty",
        "email": "",
        "username": "",
    }

    if request.method == 'POST':
        staff_id = (request.form.get('staff_id') or '').strip()
        name = (request.form.get('name') or '').strip()
        designation = (request.form.get('designation') or '').strip()
        department = (request.form.get('department') or '').strip()
        category = (request.form.get('category') or '').strip()
        email = (request.form.get('email') or '').strip()
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password') or ''

        # Auto-set username = staff_id if not provided
        if not username and staff_id:
            username = staff_id.lower()

        # Default password = "123456" if not provided
        if not password:
            password = "123456"

        form.update(
            staff_id=staff_id,
            name=name,
            designation=designation,
            department=department,
            category=category,
            email=email,
            username=username,
        )

        if not staff_id or not name or not designation or not department or not category:
            error = "Please fill all required fields."
        elif users.find_one({"staff_id": staff_id}):
            error = "This Staff ID already exists."
        elif username and users.find_one({"username": username}):
            error = "This username already exists."

        if not error:
            password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
            users.insert_one({
                "role": "lecturer",
                "staff_id": staff_id,
                "name": name,
                "designation": designation,
                "department": department,
                "category": category,
                "email": email,
                "username": username,
                "password": password_hash,
                "display_password": password,  # Store for admin display
                "created_date": datetime.now(),  # Store creation date
                "assigned_subjects": "",  # Initialize assigned subjects
            })
            return redirect(url_for('manage_staff'))

    return render_template('admin/staff_form.html', mode="create", form=form, error=error)

@app.route('/admin/staff/<id>/edit', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_staff_edit(id):
    error = None
    staff_doc = users.find_one({"_id": ObjectId(id), "role": "lecturer"})
    if not staff_doc:
        return redirect(url_for('manage_staff'))

    form = {
        "staff_id": staff_doc.get("staff_id", ""),
        "name": staff_doc.get("name", ""),
        "designation": staff_doc.get("designation", ""),
        "department": staff_doc.get("department", ""),
        "category": staff_doc.get("category", "Teaching Faculty"),
        "email": staff_doc.get("email", ""),
        "username": staff_doc.get("username", ""),
    }

    if request.method == 'POST':
        staff_id = (request.form.get('staff_id') or '').strip()
        name = (request.form.get('name') or '').strip()
        designation = (request.form.get('designation') or '').strip()
        department = (request.form.get('department') or '').strip()
        category = (request.form.get('category') or '').strip()
        email = (request.form.get('email') or '').strip()
        username = (request.form.get('username') or '').strip()
        new_password = request.form.get('password') or ''

        # Auto-set username = staff_id if not provided
        if not username and staff_id:
            username = staff_id.lower()

        form.update(
            staff_id=staff_id,
            name=name,
            designation=designation,
            department=department,
            category=category,
            email=email,
            username=username,
        )

        if not staff_id or not name or not designation or not department or not category:
            error = "Please fill all required fields."
        else:
            existing_staff_id = users.find_one({"staff_id": staff_id, "_id": {"$ne": staff_doc["_id"]}})
            if existing_staff_id:
                error = "This Staff ID already exists."
            elif not username:
                error = "Username is required for lecturer login."
            else:
                existing_username = users.find_one({"username": username, "_id": {"$ne": staff_doc["_id"]}})
                if existing_username:
                    error = "This username already exists."

        if not error:
            update = {
                "staff_id": staff_id,
                "name": name,
                "designation": designation,
                "department": department,
                "category": category,
                "email": email,
                "username": username,
            }
            if new_password.strip():
                update["password"] = bcrypt.generate_password_hash(new_password).decode('utf-8')
                update["display_password"] = new_password  # Store for admin display

            users.update_one({"_id": staff_doc["_id"]}, {"$set": update})
            return redirect(url_for('manage_staff'))

    return render_template('admin/staff_form.html', mode="edit", form=form, error=error, staff_id=str(staff_doc["_id"]))

@app.route('/admin/staff/<id>/delete', methods=['POST'])
@login_required
@admin_required
def admin_staff_delete(id):
    users.delete_one({"_id": ObjectId(id), "role": "lecturer"})
    return redirect(url_for('manage_staff'))


@app.route('/admin/staff/delete-all', methods=['POST'])
@login_required
@admin_required
def admin_staff_delete_all():
    result = users.delete_many({"role": "lecturer"})
    count = result.deleted_count
    return redirect(url_for('manage_staff', delete_all_success=count))

@app.route('/admin/staff/<id>/change-password', methods=['POST'])
@login_required
@admin_required
def admin_staff_change_password(id):
    staff_doc = users.find_one({"_id": ObjectId(id), "role": "lecturer"})
    if not staff_doc:
        return redirect(url_for('manage_staff'))
    
    new_password = request.form.get('new_password', '').strip()
    if not new_password:
        return redirect(url_for('manage_staff', password_error="Password cannot be empty."))
    
    password_hash = bcrypt.generate_password_hash(new_password).decode('utf-8')
    # Store display_password for admin view (not secure but for display purposes)
    users.update_one({"_id": ObjectId(id)}, {"$set": {
        "password": password_hash,
        "display_password": new_password  # Store plain text for display only
    }})
    return redirect(url_for('manage_staff', password_updated='1', updated_name=staff_doc.get('name', 'lecturer')))

@app.route('/admin/staff/export-excel')
@login_required
@admin_required
def admin_staff_export_excel():
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment
        from datetime import datetime
        
        all_staff = list(users.find({"role": "lecturer"}).sort("staff_id", 1))
        
        wb = Workbook()
        ws = wb.active
        ws.title = "Lecturers"
        
        # Headers - only essential adding information
        headers = ["Lecturer ID", "Name", "Username", "Password"]
        ws.append(headers)
        
        # Style header row
        header_fill = PatternFill(start_color="366092", end_color="366092", fill_type="solid")
        header_font = Font(bold=True, color="FFFFFF", size=11)
        header_alignment = Alignment(horizontal="center", vertical="center")
        
        for col in range(1, len(headers) + 1):
            cell = ws.cell(row=1, column=col)
            cell.fill = header_fill
            cell.font = header_font
            cell.alignment = header_alignment
        
        # Add data rows - only essential information
        for staff in all_staff:
            lecturer_id = staff.get('staff_id', '')
            name = staff.get('name', '')
            username = staff.get('username', staff.get('staff_id', '').lower())
            password = staff.get('display_password', '123456')
            
            ws.append([
                lecturer_id,
                name,
                username,
                password
            ])
        
        # Auto-adjust column widths
        column_widths = {
            'A': 15,  # Lecturer ID
            'B': 35,  # Name
            'C': 15,  # Username
            'D': 15   # Password
        }
        for col, width in column_widths.items():
            ws.column_dimensions[col].width = width
        
        # Create in-memory file
        output = BytesIO()
        wb.save(output)
        output.seek(0)
        
        # Generate filename with timestamp
        filename = f"Lecturer_Management_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename
        )
    except ImportError:
        flash('openpyxl library not installed. Please install it: pip install openpyxl', 'error')
        return redirect(url_for('manage_staff'))
    except Exception as e:
        flash(f'Error exporting Excel: {str(e)}', 'error')
        return redirect(url_for('manage_staff'))

@app.route('/admin/staff/bulk-upload', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_staff_bulk_upload():
    if request.method == 'POST':
        if 'excel_file' not in request.files:
            return render_template('admin/bulk_upload.html', error="No file selected.")
        
        file = request.files['excel_file']
        if file.filename == '':
            return render_template('admin/bulk_upload.html', error="No file selected.")
        
        if not file.filename.endswith(('.xlsx', '.xls')):
            return render_template('admin/bulk_upload.html', error="Please upload a valid Excel file (.xlsx or .xls).")
        
        try:
            import openpyxl
            from openpyxl import load_workbook
            
            workbook = load_workbook(file)
            sheet = workbook.active
            
            # Expected columns: Staff ID, Name, Designation, Department, Category, Email, Username, Password
            headers = [cell.value for cell in sheet[1]]
            
            # Find column indices (accept Staff ID, Name/Faculty Name/Faculty for display name)
            col_map = {}
            for idx, header in enumerate(headers, start=1):
                header_lower = str(header).lower().strip() if header else ""
                if 'staff' in header_lower and 'id' in header_lower:
                    col_map['staff_id'] = idx
                elif header_lower in ('faculty name', 'name') or header_lower == 'faculty':
                    col_map['name'] = idx  # Faculty Name / Name for full display (e.g. Mr.Umesh)
                elif 'name' in header_lower and 'user' not in header_lower:
                    col_map['name'] = idx  # e.g. Staff Name, but not Username
                elif 'designation' in header_lower:
                    col_map['designation'] = idx
                elif 'department' in header_lower:
                    col_map['department'] = idx
                elif 'category' in header_lower:
                    col_map['category'] = idx
                elif 'email' in header_lower:
                    col_map['email'] = idx
                elif 'username' in header_lower:
                    col_map['username'] = idx
                elif 'password' in header_lower:
                    col_map['password'] = idx
            
            if 'staff_id' not in col_map or 'name' not in col_map:
                return render_template('admin/bulk_upload.html', error="Excel file must have 'Staff ID' and 'Name' (or 'Faculty Name') columns.")
            
            success_count = 0
            error_rows = []
            
            for row_num, row in enumerate(sheet.iter_rows(min_row=2, values_only=False), start=2):
                try:
                    staff_id = str(row[col_map['staff_id'] - 1].value or '').strip()
                    name = str(row[col_map['name'] - 1].value or '').strip()
                    
                    if not staff_id or not name:
                        continue
                    
                    designation = str(row[col_map.get('designation', 0) - 1].value or '').strip() if col_map.get('designation') else ''
                    department = str(row[col_map.get('department', 0) - 1].value or '').strip() if col_map.get('department') else ''
                    category = str(row[col_map.get('category', 0) - 1].value or '').strip() if col_map.get('category') else 'Teaching Faculty'
                    email = str(row[col_map.get('email', 0) - 1].value or '').strip() if col_map.get('email') else ''
                    username = str(row[col_map.get('username', 0) - 1].value or '').strip() if col_map.get('username') else staff_id.lower()
                    password = str(row[col_map.get('password', 0) - 1].value or '').strip() if col_map.get('password') else '123456'
                    
                    # Check if staff_id already exists
                    if users.find_one({"staff_id": staff_id}):
                        error_rows.append(f"Row {row_num}: Staff ID {staff_id} already exists")
                        continue
                    
                    # Check if username already exists
                    if username and users.find_one({"username": username}):
                        error_rows.append(f"Row {row_num}: Username {username} already exists")
                        continue
                    
                    password_hash = bcrypt.generate_password_hash(password).decode('utf-8')
                    
                    users.insert_one({
                        "role": "lecturer",
                        "staff_id": staff_id,
                        "name": name,
                        "designation": designation,
                        "department": department,
                        "category": category,
                        "email": email,
                        "username": username,
                        "password": password_hash,
                        "display_password": password,  # Store for admin display
                        "created_date": datetime.now(),  # Store creation date
                        "assigned_subjects": "",  # Initialize assigned subjects
                    })
                    success_count += 1
                except Exception as e:
                    error_rows.append(f"Row {row_num}: {str(e)}")
            
            message = f"Successfully imported {success_count} record(s)."
            if error_rows:
                message += f" {len(error_rows)} error(s) occurred."
            
            # Redirect back to staff list with status so refresh is safe (no re-upload)
            return redirect(url_for('manage_staff', bulk_success=message))
        except ImportError:
            return render_template('admin/bulk_upload.html', error="openpyxl library not installed. Please install it: pip install openpyxl")
        except Exception as e:
            return render_template('admin/bulk_upload.html', error=f"Error processing file: {str(e)}")
    
    return render_template('admin/bulk_upload.html')

@app.route('/api/timetable/<staff_id>')
@login_required
def get_timetable_metadata(staff_id):
    """Fetch timetable structured data and image URL for a specific staff member."""
    # Find the user by staff_id
    u = users.find_one({"staff_id": {"$regex": f"^{staff_id}$", "$options": "i"}})
    if not u:
        return jsonify({"error": "User not found"}), 404
        
    tt_doc = timetable.find_one({"lecturer_id": str(u["_id"])})
    image_url = None
    if tt_doc and tt_doc.get("image_path"):
        image_path = (tt_doc.get("image_path") or "").replace("\\", "/")
        image_url = url_for("static", filename=image_path)
    
    # Also fallback to check if JSON exists on disk if not in tt_doc
    structured = tt_doc.get("structured") if tt_doc else {}
    if not structured:
        json_path = os.path.join(os.path.dirname(__file__), "static", "json_timetables", f"{staff_id}.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    structured = json.load(f)
            except:
                pass
                
    return jsonify({
        "staff_id": staff_id,
        "name": u.get("name"),
        "image_url": image_url,
        "structured": structured
    })

def get_leave_types():
    types = list(db.leave_types.find().sort("name", 1))
    return types

@app.route('/admin/leaves/api/types', methods=['GET', 'POST'])
@login_required
@admin_required
def manage_leave_types():
    if request.method == 'POST':
        name = request.json.get("name", "").strip()
        days = float(request.json.get("default_days", 10))
        if name:
            db.leave_types.insert_one({"name": name, "default_days": days})
        return jsonify({"success": True})
    
    return jsonify({"types": [{"id": str(t["_id"]), "name": t["name"], "default_days": t["default_days"]} for t in get_leave_types()]})

@app.route('/admin/leaves/api/types/<id>', methods=['DELETE'])
@login_required
@admin_required
def delete_leave_type(id):
    db.leave_types.delete_one({"_id": ObjectId(id)})
    return jsonify({"success": True})


@app.route('/admin/leaves')
@login_required
@admin_required
def admin_leaves():
    # Optional filters: search query and month (YYYY-MM)
    q = (request.args.get("q") or "").strip()
    month = (request.args.get("month") or "").strip()

    all_leaves = list(leaves.find().sort("_id", -1))

    def matches_filters(doc):
        text_ok = True
        month_ok = True

        if q:
            q_lower = q.lower()
            text_fields = [
                str(doc.get("lecturer_name", "")),
                str(doc.get("type", "")),
                str(doc.get("reason", "")),
                str(doc.get("status", "")),
            ]
            text_ok = any(q_lower in field.lower() for field in text_fields)

        if month:
            # Expect month in format YYYY-MM, match against from_date / to_date strings
            from_date = str(doc.get("from_date", ""))
            to_date = str(doc.get("to_date", ""))
            month_ok = month in from_date or month in to_date

        return text_ok and month_ok

    filtered_leaves = [doc for doc in all_leaves if matches_filters(doc)]

    # For allocations view, show lecturers in the same sorted Staff ID order
    # as the "Add Lecturer / Manage Staff" page (BBHCF001, BBHCF002, ...).
    all_lecturers = list(users.find({"role": "lecturer"}).sort("staff_id", 1))
    
    # Inject detailed leave stats for each lecturer
    for lec in all_lecturers:
        lec['leave_stats'] = get_all_leave_stats(str(lec['_id']))

    return render_template(
        'admin/leave_requests.html',
        leaves=filtered_leaves,
        q=q,
        month=month,
        lecturers=all_lecturers,
        leave_types=get_leave_types(),
    )

@app.route('/admin/all-assignments')
@login_required
@admin_required
def admin_all_assignments():
    """Detailed overview of all class substitution assignments in the system"""
    all_allocs = list(leave_class_allocations.find().sort("created_at", -1))
    return render_template('admin/all_assignments.html', assignments=all_allocs)

@app.route('/admin/api/clear-all-assignments', methods=['POST'])
@login_required
@admin_required
def admin_clear_all_assignments():
    """Wipe all substitution data from the entire system (Assignments and Notifications)"""
    try:
        # 1. Clear all allocations
        leave_class_allocations.delete_many({})
        # 2. Clear all related notifications
        faculty_notifications.delete_many({"type": "class_assignment"})
        
        flash("All assignment data has been cleared from the system.", "success")
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})

@app.route('/admin/leaves/api/set_allocation/<id>', methods=['POST'])
@login_required
@admin_required
def api_set_leave_allocation(id):
    allocated = request.json.get('leaves_per_month', 1)
    leave_type = request.json.get('leave_type', '').strip()
    if not leave_type:
        return jsonify({"success": False, "message": "Leave type is required"}), 400
        
    try:
        allocated = float(allocated)
    except:
        allocated = 1
        
    # AUTO-REGISTER: If this type doesn't exist globally, add it
    if not leave_types.find_one({"name": leave_type}):
        leave_types.insert_one({"name": leave_type})
        
    user = users.find_one({"_id": ObjectId(id)})
    if user:
        balances = user.get("leave_balances", {})
        balances[leave_type] = allocated
        
        # also set the legacy leaves_per_month if it's the main type so backward compatibility works somewhat
        # and only if it's Casual Leave or the first one
        update_fields = {"leave_balances": balances}
        if leave_type == "Casual Leave" or not user.get("leaves_per_month"):
            update_fields["leaves_per_month"] = allocated
            
        users.update_one({"_id": ObjectId(id)}, {"$set": update_fields})
        
    socketio.emit('leave_types_updated')
    return jsonify({"success": True})
    
@app.route('/admin/leaves/api/get_balances/<id>', methods=['GET'])
@login_required
@admin_required
def api_get_leave_balances(id):
    user = users.find_one({"_id": ObjectId(id)})
    if not user:
        return jsonify({"success": False, "message": "User not found"}), 404
        
    stats = get_all_leave_stats(id)
    return jsonify({"success": True, "stats": stats})

@app.route('/admin/leaves/api/delete_allocation/<id>/<type>', methods=['POST'])
@login_required
@admin_required
def api_delete_leave_allocation(id, type):
    user = users.find_one({"_id": ObjectId(id)})
    if user:
        balances = user.get("leave_balances", {})
        if type in balances:
            del balances[type]
            users.update_one({"_id": ObjectId(id)}, {"$set": {"leave_balances": balances}})
            socketio.emit('leave_types_updated')
            return jsonify({"success": True})
    return jsonify({"success": False, "message": "Not found"})

@app.route('/lecturer/api/leave-balance', methods=['GET'])
@login_required
def api_get_lecturer_balance():
    leave_type = request.args.get('type', 'Casual Leave')
    balance = calculate_leaves_left(current_user.id, leave_type)
    
    # Also return the total allocated for this type
    user_doc = users.find_one({"_id": ObjectId(current_user.id)})
    total = 0
    if user_doc:
        if "leave_balances" in user_doc:
            total = user_doc["leave_balances"].get(leave_type, 0)
        else:
            total = user_doc.get("leaves_per_month", 0)
            
    return jsonify({
        "success": True, 
        "balance": balance, 
        "total": total,
        "display": f"{total}/{int(balance) if balance.is_integer() else balance}"
    })

@app.route('/lecturer/api/all-balances', methods=['GET'])
@login_required
def api_get_lecturer_all_balances():
    stats = get_all_leave_stats(current_user.id)
    return jsonify({"success": True, "stats": stats})


@app.route('/admin/leaves/delete-all', methods=['POST'])
@login_required
@admin_required
def admin_leaves_delete_all():
    result = leaves.delete_many({})
    count = result.deleted_count
    flash(f"Deleted {count} leave record(s).", "success")
    return redirect(url_for('admin_leaves'))


@app.route('/admin/leave/delete/<id>', methods=['POST'])
@login_required
@admin_required
def admin_leave_delete(id):
    leaves.delete_one({"_id": ObjectId(id)})
    return jsonify({"success": True})


@app.route('/admin/timetables', methods=['GET'])
@login_required
@admin_required
def admin_timetables():
    # Fetch all lecturers
    staff_id_regex = r"^BBHCF\d+$"
    all_lecturers = list(
        users.find(
            {
                "role": "lecturer",
                "staff_id": {"$regex": staff_id_regex, "$options": "i"},
            }
        ).sort("staff_id", 1)
    )
    
    # Path for JSON timetables
    json_dir = os.path.join(os.path.dirname(__file__), "static", "json_timetables")
    os.makedirs(json_dir, exist_ok=True)
    
    # Map staff_id to whether it exists on disk
    existing_files = {f.split('.')[0] for f in os.listdir(json_dir) if f.endswith('.json')}
    
    lecturers_data = []
    uploaded_count = 0
    for lect in all_lecturers:
        staff_id = lect.get("staff_id")
        has_tt = staff_id in existing_files
        if has_tt: uploaded_count += 1
        
        lecturers_data.append({
            "id": str(lect["_id"]),
            "staff_id": staff_id,
            "name": lect.get("name"),
            "department": lect.get("department"),
            "has_timetable": has_tt
        })
    
    return render_template(
        'admin/timetables.html',
        lecturers=lecturers_data,
        total=len(all_lecturers),
        uploaded_count=uploaded_count,
        pending_count=len(all_lecturers) - uploaded_count,
        error=request.args.get('error'),
        message=request.args.get('message')
    )

@app.route('/admin/timetables/upload', methods=['POST'])
@login_required
@admin_required
def admin_timetables_upload():
    file = request.files.get('timetable_pdf')
    if not file or file.filename == '':
        return redirect(url_for('admin_timetables', error="No PDF file selected."))
    
    if not file.filename.lower().endswith('.pdf'):
        return redirect(url_for('admin_timetables', error="Only PDF files are allowed."))

    try:
        pdf_bytes = file.read()
        import threading
        from utils.timetable_processor import process_background_pipeline, stop_events
        
        task_id = "main_worker"
        if task_id in stop_events:
            return redirect(url_for('admin_timetables', error="A bulk process is already running. Please stop it or wait for it to finish."))
            
        # Register the stop event BEFORE starting the thread
        stop_events[task_id] = threading.Event()
        
        threading.Thread(target=process_background_pipeline, args=(
            pdf_bytes, task_id, socketio, db
        )).start()
        
        return redirect(url_for('admin_timetables', message="PDF upload successful. Processing started..."))
    except Exception as e:
        return redirect(url_for('admin_timetables', error=f"Upload error: {str(e)}"))

@app.route('/admin/timetables/upload-image', methods=['POST'])
@login_required
@admin_required
def admin_timetables_upload_image():
    file = request.files.get('timetable_image')
    if not file or file.filename == '':
        return redirect(url_for('admin_timetables', error="No image file selected."))

    try:
        img_bytes = file.read()
        import threading
        from utils.timetable_processor import extract_from_image, match_and_save, log_event

        def process_image_worker(image_data):
            try:
                log_event("Initializing single image process...", socketio=socketio, progress=10)
                log_event("Sending image to Gemini AI...", socketio=socketio, progress=30)
                data = extract_from_image(image_data)
                if "error" in data:
                    log_event(f"AI Error: {data['error']}", socketio=socketio, status="error", progress=0)
                    return
                
                log_event(f"AI extracted faculty: {data.get('faculty', 'Unknown')}", socketio=socketio, progress=70)
                log_event("Syncing with database...", socketio=socketio, progress=90)
                match_and_save(data, db, socketio)
                log_event(f"Successfully processed: {data.get('faculty', 'Unknown')}", socketio=socketio, progress=100)
                socketio.emit('timetable_progress', {'progress': 100, 'done': True, 'status': "Process complete."})
            except Exception as e:
                log_event(f"Worker Error: {str(e)}", socketio=socketio, status="error")

        threading.Thread(target=process_image_worker, args=(img_bytes,)).start()
        return redirect(url_for('admin_timetables', message="Image upload successful. Processing started..."))
    except Exception as e:
        return redirect(url_for('admin_timetables', error=f"Image upload error: {str(e)}"))

@app.route('/admin/timetables/stop', methods=['POST'])
@login_required
@admin_required
def admin_timetables_stop():
    from utils.timetable_processor import stop_events
    task_id = "main_worker"
    if task_id in stop_events:
        stop_events[task_id].set()
        # Also log it
        from utils.timetable_processor import log_event
        log_event("🛑 Manual stop requested by admin.", socketio=socketio, status="warning")
        return jsonify({"success": True, "message": "Stopping... Please wait for current slice to finish."})
    return jsonify({"success": False, "message": "No active process found."})

@app.route('/admin/timetables/delete/<staff_id>', methods=['POST'])
@login_required
@admin_required
def admin_timetable_delete(staff_id):
    json_path = os.path.join(os.path.dirname(__file__), "static", "json_timetables", f"{staff_id}.json")
    if os.path.exists(json_path):
        os.remove(json_path)
    
    # Also clear from DB
    lecturer = users.find_one({"staff_id": staff_id})
    if lecturer:
        timetable.delete_one({"lecturer_id": str(lecturer["_id"])})
        
    return redirect(url_for('admin_timetables', message=f"Timetable for {staff_id} deleted."))

@app.route('/admin/timetables/bulk-delete', methods=['POST'])
@login_required
@admin_required
def admin_timetables_bulk_delete():
    import shutil
    
    # Directories to clear
    dirs_to_clear = [
        os.path.join(os.path.dirname(__file__), "static", "json_timetables"),
        os.path.join(os.path.dirname(__file__), "static", "timetables_json"),
        os.path.join(os.path.dirname(__file__), "static", "timetable_splits"),
        os.path.join(os.path.dirname(__file__), "static", "timetable_images"),
        os.path.join(os.path.dirname(__file__), "static", "timetables")
    ]
    
    for d in dirs_to_clear:
        if os.path.exists(d):
            shutil.rmtree(d)
        os.makedirs(d, exist_ok=True)
    
    # Also delete tracking file
    track_file = os.path.join(os.path.dirname(__file__), "static", "processed_slices.json")
    if os.path.exists(track_file):
        os.remove(track_file)
    
    # Clear Table from DB
    timetable.delete_many({})
    
    return redirect(url_for('admin_timetables', message="All JSONs, images, tracking files, and database records cleared."))

@app.route('/admin/timetables/edit/<staff_id>', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_timetable_edit(staff_id):
    json_path = os.path.join(os.path.dirname(__file__), "static", "json_timetables", f"{staff_id}.json")
    if not os.path.exists(json_path):
        flash("JSON file not found for this faculty.", "danger")
        return redirect(url_for('admin_timetables'))
    
    # Load JSON
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
    json_str = json.dumps(data, indent=4, ensure_ascii=False)
    
    if request.method == 'POST':
        new_json = request.form.get('json_data')
        try:
            data = json.loads(new_json)
            with open(json_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=4, ensure_ascii=False)
            flash("Timetable JSON updated.", "success")
            return redirect(url_for('admin_timetables'))
        except Exception as e:
            flash(f"Invalid JSON: {e}", "danger")
            json_str = new_json

    return render_template('admin/edit_json.html', staff_id=staff_id, json_str=json_str)

@app.route('/admin/leave/<id>/<status>', methods=['GET', 'POST'])
@login_required
@admin_required
def review_leave(id, status):
    if status not in ("Approved", "Rejected", "Pending"):
        flash("Invalid status.", "danger")
        return redirect(request.referrer or url_for('admin_leaves'))

    leave_doc = leaves.find_one({"_id": ObjectId(id)})

    # Update the leave status
    leaves.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"status": status, "reviewed_at": datetime.now()}},
    )
    
    # CASCADE APPROVAL: If Management approves the leave, all allocations become 'approved'
    if status == "Approved":
        leave_class_allocations.update_many(
            {"leave_id": id},
            {"$set": {"status": "approved"}}
        )
    
    # CASCADE REJECTION: If Rejected, delete everything
    elif status == "Rejected":
        # Get all allocation IDs first to ensure notification cleanup is absolute
        allocations = list(leave_class_allocations.find({"leave_id": id}))
        alloc_ids = [str(a['_id']) for a in allocations]
        
        # 1. Delete all allocations linked to this leave
        leave_class_allocations.delete_many({"leave_id": id})
        # 2. Delete all related notifications (by leave_id or allocation_id)
        faculty_notifications.delete_many({
            "$or": [
                {"leave_id": id},
                {"allocation_id": {"$in": alloc_ids}}
            ]
        })
        # 3. Notify colleagues via socket so their screens refresh instantly
        for a in allocations:
            socketio.emit('assignment_recalled', {"allocation_id": str(a['_id'])}, room=None)
        
        # 4. CLEAR DRAFT: So they start fresh for the next attempt
        if leave_doc:
            leave_drafts.delete_one({"user_id": str(leave_doc['lecturer_id'])})
    
    if leave_doc:
        leaves_left = calculate_leaves_left(leave_doc['lecturer_id'])
        socketio.emit('leave_status_update', {
            'id': id,
            'status': status,
            'lecturer_id': leave_doc['lecturer_id'],
            'leaves_left': leaves_left
        })
        
    flash(f"Leave {status.lower()} successfully!", "success")
    return redirect(url_for('admin_leaves'))

def get_all_leave_stats(lecturer_id):
    user_doc = users.find_one({"_id": ObjectId(lecturer_id)})
    if not user_doc: return []
    
    balances = user_doc.get("leave_balances", {})
    # Legacy fallback: if NO leave_balances object exists at all, use leaves_per_month for Casual
    if not balances and "leaves_per_month" in user_doc:
        balances = {"Casual Leave": user_doc.get("leaves_per_month", 20)}
    
    # Get all approved leaves for this lecturer once
    approved_leaves = list(leaves.find({"lecturer_id": str(lecturer_id), "status": "Approved"}))
    
    stats = []
    # Fetch standard types from dynamic collection - THIS IS THE SOURCE OF TRUTH
    db_types = [t['name'] for t in leave_types.find().sort("name", 1)]
    
    # We ONLY show types that are in the global leave_types collection.
    # If a type was deleted globally, it will no longer show up here
    # even if it still exists in the user's document (this handles orphaned data nicely).
    all_types = db_types
    
    for lt in all_types:
        # If the type is not in balances, it's 0.0 (unassigned)
        total = float(balances.get(lt, 0))
        used = 0.0
        
        type_leaves = [l for l in approved_leaves if l.get('type') == lt]
        for l in type_leaves:
            # logic from calculate_leaves_left
            if l.get('half_day'): 
                used += 0.5
            elif l.get('mode') == 'time': 
                used += 1.0 # Assuming time-based leaves count as 1 day for now
            else:
                try:
                    f_date = datetime.strptime(l['from_date'].split(' ')[0], '%Y-%m-%d')
                    t_date = datetime.strptime(l['to_date'].split(' ')[0], '%Y-%m-%d')
                    days = (t_date - f_date).days + 1
                    if days > 0: 
                        used += float(days)
                except: 
                    used += 1.0
        
        stats.append({
            "type": lt,
            "total": total,
            "used": used,
            "left": max(0.0, total - used)
        })
    return stats

@app.route('/admin/leave/api/<id>/<status>', methods=['POST'])
@login_required
@admin_required
def api_review_leave(id, status):
    if status not in ("Approved", "Rejected", "Pending"):
        return jsonify({"success": False, "message": "Invalid status"}), 400

    leave_doc = leaves.find_one({"_id": ObjectId(id)})
    if not leave_doc:
        return jsonify({"success": False, "message": "Not found"}), 404

    leaves.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"status": status, "reviewed_at": datetime.now()}},
    )
    
    # CASCADE APPROVAL: If Management approves the leave, all allocations become 'approved'
    if status == "Approved":
        leave_class_allocations.update_many(
            {"leave_id": id},
            {"$set": {"status": "approved"}}
        )
    
    # CASCADE REJECTION: If Rejected, delete everything
    elif status == "Rejected":
        leave_class_allocations.delete_many({"leave_id": id})
        faculty_notifications.delete_many({"leave_id": id})
        # CLEAR DRAFT: So they start fresh for the next attempt
        leave_drafts.delete_one({"user_id": str(leave_doc['lecturer_id'])})

    leaves_left = calculate_leaves_left(leave_doc['lecturer_id'])
    socketio.emit('leave_status_update', {
        'id': id,
        'status': status,
        'lecturer_id': leave_doc['lecturer_id'],
        'leaves_left': leaves_left
    })
    
    return jsonify({"success": True})

def calculate_leaves_left(lecturer_id, leave_type="Casual Leave"):
    user_doc = users.find_one({"_id": ObjectId(lecturer_id)})
    
    if user_doc and "leave_balances" in user_doc:
        total_leaves = user_doc["leave_balances"].get(leave_type, 0)
    else:
        # If no balances object at all, fallback to legacy field or 0
        total_leaves = user_doc.get("leaves_per_month", 0) if user_doc else 0
        
    approved_leaves = list(leaves.find({"lecturer_id": lecturer_id, "status": "Approved", "type": leave_type}))
    used_days = 0
    for l in approved_leaves:
        mode = l.get('mode', 'full')
        is_half_day = l.get('half_day', False)
        
        if is_half_day:
            used_days += 0.5
        elif mode == 'time':
            used_days += 1
        else:
            try:
                f_date = datetime.strptime(l['from_date'].split(' ')[0], '%Y-%m-%d')
                t_date = datetime.strptime(l['to_date'].split(' ')[0], '%Y-%m-%d')
                days = (t_date - f_date).days + 1
                if days > 0:
                    used_days += days
            except Exception:
                used_days += 1
    return max(0, total_leaves - used_days)

# ============ TIMETABLE & CLASS ALLOCATION HELPERS ============

def get_day_name_from_date(date_str):
    """Get day name (MONDAY, TUESDAY, etc.) from date string"""
    try:
        date_obj = datetime.strptime(date_str, '%Y-%m-%d')
        return date_obj.strftime('%A').upper()
    except:
        return None

def load_faculty_timetable(staff_id):
    """Load timetable from JSON file for a faculty member"""
    json_dir = os.path.join(os.path.dirname(__file__), "static", "json_timetables")
    
    # Try the exact staff_id first
    json_path = os.path.join(json_dir, f"{staff_id}.json")
    
    if os.path.exists(json_path):
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error loading timetable for {staff_id}: {e}")
    
    # If not found, try alternative formats (with/without 'N')
    alternative_id = None
    if 'N' in staff_id:
        # If ID has N (e.g., BBHCFN028), try without N (BBHCF028)
        alternative_id = staff_id.replace('N', '')
    elif 'BBHCF' in staff_id:
        # If ID doesn't have N (e.g., BBHCF028), try with N (BBHCFN028)
        alternative_id = staff_id.replace('BBHCF', 'BBHCFN')
    
    if alternative_id:
        alt_path = os.path.join(json_dir, f"{alternative_id}.json")
        if os.path.exists(alt_path):
            try:
                with open(alt_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"Error loading alternative timetable for {alternative_id}: {e}")
    
    return None

def get_classes_on_date(staff_id, date_str):
    """Total Scan: Retrieve every single hour that has a class assigned (Deep Search)"""
    classes = []
    try:
        # Normalize date format
        date_obj = None
        for fmt in ('%Y-%m-%d', '%d-%m-%Y'):
            try:
                date_obj = datetime.strptime(date_str, fmt)
                break
            except: continue
        if not date_obj: return []
        
        day_name = date_obj.strftime('%A').upper() 
        iso_date = date_obj.strftime('%Y-%m-%d')
        
        # Load Faculty JSON
        data = load_faculty_timetable(staff_id)
        if not data or not isinstance(data, dict): return []
        
        tt = data.get('timetable', {})
        period_meta = {str(p.get('period', '')): p.get('time', 'TBD') for p in tt.get('periods', [])}
        days = tt.get('days', [])
        
        # Find Day (Support MON vs MONDAY)
        day_block = next((d for d in days if d.get('day', '').upper() == day_name or d.get('day', '').upper().startswith(day_name[:3])), None)
        if not day_block: return []
        
        slots = day_block.get('slots', {})
        for p_key, s_data in slots.items():
            # CAPTURE if either subject OR class exists
            if s_data and (s_data.get('subject') or s_data.get('class')):
                subject_name = s_data.get('subject') or s_data.get('class') or "Class Assignment"
                classes.append({
                    'period': p_key,
                    'time': period_meta.get(str(p_key), 'TBD'),
                    'class': s_data.get('class') or "N/A",
                    'section': s_data.get('section', ''),
                    'subject': subject_name,
                    'room': s_data.get('class') or "N/A",
                    'date': iso_date,
                    'day': day_name,
                    'class_id': f"{staff_id}_{iso_date.replace('-', '')}_{p_key}"
                })
        
        # Sort: 0, I, II, III, IV, V, VI, VII
        sort_map = {"0": 0, "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7}
        classes.sort(key=lambda x: sort_map.get(str(x['period']), 99))
        
    except Exception as e:
        print(f"Deep Scan Error: {e}")
    return classes

def get_classes_for_leave_period(staff_id, from_date, to_date):
    """Hard-Inclusive scanner for all days in selected range"""
    all_rows = []
    try:
        def to_dt(s):
            for f in ('%Y-%m-%d', '%d-%m-%Y'):
                try: return datetime.strptime(s, f).date()
                except: continue
            return None
            
        start = to_dt(from_date)
        end = to_dt(to_date)
        if not start or not end: return []
        
        curr = start
        while curr <= end: # The <= ensures the 'To Date' is always included
            day_list = get_classes_on_date(staff_id, curr.strftime('%Y-%m-%d'))
            all_rows.extend(day_list)
            curr += timedelta(days=1)
    except Exception as e:
        print(f"Range Scanner Critical Error: {e}")
    return all_rows

def get_available_faculty_for_slot(date_str, time_slot, exclude_staff_id=None):
    """Get faculty members who are free during a specific time slot"""
    all_faculty = list(users.find({"role": "lecturer"}))
    available = []
    
    day_name = get_day_name_from_date(date_str)
    
    for faculty in all_faculty:
        staff_id = faculty.get('staff_id')
        if not staff_id or staff_id == exclude_staff_id:
            continue
        
        # Check if faculty has any class at this time
        faculty_classes = get_classes_on_date(staff_id, date_str)
        has_conflict = False
        
        for cls in faculty_classes:
            if cls.get('time') == time_slot:
                has_conflict = True
                break
        
        if not has_conflict:
            available.append({
                'staff_id': staff_id,
                'name': faculty.get('name'),
                'user_id': str(faculty.get('_id')),
                'designation': faculty.get('designation', 'Lecturer')
            })
    
    return available

def create_class_assignment_notification(leave_id, assigned_to_id, assigned_by_id, class_details):
    """Create notification for faculty about class assignment"""
    notification = {
        'leave_id': leave_id,
        'type': 'class_assignment',
        'assigned_to': assigned_to_id,
        'assigned_by': assigned_by_id,
        'class_details': class_details,
        'status': 'pending',  # pending, accepted, rejected
        'created_at': datetime.now(),
        'read': False
    }
    return faculty_notifications.insert_one(notification)

def save_timetable_backup(staff_id, original_data, reason="leave_assignment"):
    """Save original timetable before making changes"""
    backup = {
        'staff_id': staff_id,
        'original_data': original_data,
        'backup_date': datetime.now(),
        'reason': reason
    }
    return timetable_history.insert_one(backup)

# Lecturer Routes
@app.route('/lecturer/dashboard')
@login_required
@lecturer_required
def lecturer_dashboard():
    my_leaves = list(leaves.find({"lecturer_id": current_user.id}).sort("_id", -1).limit(5))

    tt_doc = timetable.find_one({"lecturer_id": current_user.id})
    timetable_image_url = None
    has_timetable = False
    if tt_doc and tt_doc.get("image_path"):
        image_path = (tt_doc.get("image_path") or "").replace("\\", "/")
        timetable_image_url = url_for("static", filename=image_path)
        has_timetable = True

    leaves_left = calculate_leaves_left(current_user.id)

    return render_template(
        'lecturer/dashboard.html',
        leaves=my_leaves,
        has_timetable=has_timetable,
        timetable_image_url=timetable_image_url,
        leaves_left=leaves_left
    )


@app.route('/admin/api-keys', methods=['GET', 'POST'])
@login_required
@admin_required
def admin_api_keys():
    api_keys_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "api_keys.txt")
    if request.method == 'POST':
        new_key = request.form.get('new_key', '').strip()
        if new_key:
            with open(api_keys_path, 'a') as f:
                f.write(f"\n{new_key}")
            flash('API Key added successfully!', 'success')
        return redirect(url_for('admin_timetables'))
        
    keys = []
    if os.path.exists(api_keys_path):
        with open(api_keys_path, 'r') as f:
            keys = [line.strip() for line in f if line.strip() and not line.startswith("#")]
    return jsonify(keys)


@app.route('/admin/view-logs')
@login_required
@admin_required
def admin_view_logs():
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconstruction_log.txt")
    if os.path.exists(log_path):
        with open(log_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return content
    return "No logs found."

@app.route('/admin/clear-logs', methods=['POST'])
@login_required
@admin_required
def admin_clear_logs():
    log_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reconstruction_log.txt")
    try:
        with open(log_path, 'w', encoding='utf-8') as f:
            f.write(f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] Logs cleared by admin.\n")
        flash('Logs cleared successfully!', 'success')
    except Exception as e:
        flash(f'Error clearing logs: {e}', 'danger')
    return redirect(url_for('admin_timetables'))

def _parse_date_loose(text: str):
    """
    Parse a date from common user formats to YYYY-MM-DD.
    Accepts:
    - YYYY-MM-DD
    - DD-MM-YYYY / D-M-YYYY
    - DD/MM/YYYY / D/M/YYYY
    Returns iso_date string or None.
    """
    raw = (text or "").strip()
    if not raw:
        return None

    # ISO first
    m = re.fullmatch(r"(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})", raw)
    if m:
        try:
            dt = datetime(int(m["y"]), int(m["m"]), int(m["d"]))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    m = re.fullmatch(r"(?P<d>\d{1,2})[\/\-](?P<m>\d{1,2})[\/\-](?P<y>\d{4})", raw)
    if m:
        try:
            dt = datetime(int(m["y"]), int(m["m"]), int(m["d"]))
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return None

    return None

def _extract_date_range(message: str):
    """
    Best-effort date range extraction from a message.
    Returns (from_iso, to_iso) or (None, None).
    """
    msg = (message or "").strip()
    if not msg:
        return (None, None)

    # Find all date-like tokens in the message
    tokens = re.findall(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[\/\-]\d{1,2}[\/\-]\d{4})\b", msg)
    parsed = [_parse_date_loose(t) for t in tokens]
    parsed = [p for p in parsed if p]
    if len(parsed) >= 2:
        return (parsed[0], parsed[1])

    return (None, None)

CHATBOT_INTENT_PHRASES = {
    # Base seed phrases (always available)
    "apply_leave": [
        "apply leave", "apply for leave", "leave apply", "need leave", "want leave", "take leave",
        "i need leave", "i want leave", "i want to apply leave", "i need to apply leave",
        "leave", "apply leave please",
    ],
    "leave_balance": [
        "leave balance", "leaves left", "leave left", "how many leaves", "how many leave", "leave remaining",
        "remaining leave", "my leave balance",
    ],
    "attendance": [
        "attendance", "my attendance", "attendance report", "present days", "absent days",
    ],
    "timetable": [
        "timetable", "my timetable", "time table", "schedule", "my schedule",
    ],
    "dashboard": [
        "dashboard", "home", "main page",
    ],
    "greeting": [
        "hi", "hello", "hey", "good morning", "good evening", "good afternoon",
    ],
}

def _load_excel_phrase_booster():
    """
    Use the Excel dataset (ID, Sentence) to boost phrase matching.
    This is NOT supervised training (no labels in the file). We auto-assign sentences
    into intents by keyword rules and add them as extra phrases.
    """
    try:
        xlsx_path = Path(os.path.dirname(__file__)) / "static" / "chatbot_data_trani" / "chatbot_100k_sentences_dataset.xlsx"
        if not xlsx_path.exists():
            return

        # Allow disabling via env (startup time / memory)
        if (os.getenv("CHATBOT_EXCEL_BOOST", "1") or "1").strip() in {"0", "false", "False", "no", "NO"}:
            return

        import pandas as pd

        df = pd.read_excel(str(xlsx_path))
        if "Sentence" not in df.columns:
            return

        # keyword buckets for auto intent assignment
        buckets = {
            "apply_leave": ["apply leave", "leave apply", "need leave", "want leave", "take leave", "apply for leave"],
            "leave_balance": ["leave balance", "leaves left", "leave remaining", "remaining leave"],
            "attendance": ["attendance", "present", "absent", "checkin", "checkout"],
            "timetable": ["timetable", "time table", "schedule", "class", "period"],
            "dashboard": ["dashboard", "home"],
            "greeting": ["hi", "hello", "hey", "good morning", "good evening", "good afternoon"],
        }

        # cap per bucket to avoid huge memory growth
        cap = int((os.getenv("CHATBOT_EXCEL_BOOST_CAP", "3000") or "3000").strip() or 3000)
        added = {k: 0 for k in buckets.keys()}

        for s in df["Sentence"].astype(str).fillna("").tolist():
            s_norm = s.strip().lower()
            if not s_norm:
                continue
            # Keep phrase length reasonable (avoid huge paragraphs)
            if len(s_norm) > 140:
                continue

            for intent, keys in buckets.items():
                if added[intent] >= cap:
                    continue
                if any(k in s_norm for k in keys):
                    CHATBOT_INTENT_PHRASES.setdefault(intent, []).append(s_norm)
                    added[intent] += 1
                    break
    except Exception:
        # Never fail app startup due to phrase booster
        return

# Load phrase booster once on import/startup
_load_excel_phrase_booster()

def _chatbot_make_reply(text: str, *, actions=None, cards=None, ok: bool = True, message: str | None = None):
    return jsonify({
        "ok": ok,
        "text": text,
        "message": message,
        "actions": actions or [],
        "cards": cards or [],
        "ts": datetime.now().isoformat(),
    })

@app.route('/lecturer/api/chat', methods=['POST'])
def lecturer_chat_api():
    """
    Lightweight lecturer chatbot endpoint (session-based state).
    The UI sends either:
    - { "message": "..." } for normal chat
    - { "action": "...", "payload": {...} } for button clicks
    """
    body = request.get_json(silent=True) or {}
    message = (body.get("message") or "").strip()
    action = (body.get("action") or "").strip()
    payload = body.get("payload") or {}

    # API-friendly auth (do not redirect with HTML)
    if not current_user.is_authenticated:
        return _chatbot_make_reply(
            "Session expired. Please login again.",
            ok=False,
            message="unauthorized",
            actions=[{"type": "navigate", "label": "Login", "url": url_for("login", next=request.path)}],
        ), 401
    if getattr(current_user, "role", None) != "lecturer":
        return _chatbot_make_reply(
            "Lecturer access required.",
            ok=False,
            message="forbidden",
            actions=[{"type": "navigate", "label": "Go Home", "url": url_for("index")}],
        ), 403

    state = session.get("lecturer_chat_state") or {}
    pending = state.get("pending") or {}

    def reset_pending():
        state["pending"] = {}
        session["lecturer_chat_state"] = state

    def set_pending(p):
        state["pending"] = p
        session["lecturer_chat_state"] = state

    # Handle explicit actions from UI buttons
    if action == "navigate":
        url = payload.get("url") or url_for("lecturer_dashboard")
        return _chatbot_make_reply("Opening…", actions=[{"type": "navigate", "url": url}])

    if action == "cancel_flow":
        reset_pending()
        return _chatbot_make_reply("Okay, cancelled. What would you like to do next?")

    # IMPORTANT: handle slot-setting actions before flow continuation
    if action == "set_leave_type":
        if pending.get("intent") != "apply_leave":
            pending = {"intent": "apply_leave"}
        pending["leave_type"] = (payload.get("value") or payload.get("leave_type") or "").strip()
        set_pending(pending)
        return _chatbot_make_reply("Okay. Now tell me the leave dates (example: 03-01-2026 to 06-01-2026)." if (not pending.get("from_date") or not pending.get("to_date")) else "Okay. What’s the reason for your leave?")

    if action == "confirm_leave":
        if pending.get("intent") != "apply_leave":
            reset_pending()
            return _chatbot_make_reply("I don’t have a leave request ready to submit. Try: “Apply leave from 03-01-2026 to 06-01-2026”.")

        leave_type = (pending.get("leave_type") or "").strip()
        from_date = (pending.get("from_date") or "").strip()
        to_date = (pending.get("to_date") or "").strip()
        reason = (pending.get("reason") or "").strip()
        description = (pending.get("description") or "").strip()

        if not (leave_type and from_date and to_date and reason):
            return _chatbot_make_reply("Some fields are missing. Please continue the leave flow.")

        # Validate date ordering
        try:
            fdt = datetime.strptime(from_date, "%Y-%m-%d")
            tdt = datetime.strptime(to_date, "%Y-%m-%d")
            if fdt > tdt:
                return _chatbot_make_reply("From date cannot be after To date. Please re-enter the dates.")
        except Exception:
            return _chatbot_make_reply("I couldn’t validate your dates. Please provide them as DD-MM-YYYY or YYYY-MM-DD.")

        leave_data = {
            "lecturer_id": current_user.id,
            "lecturer_name": current_user.name,
            "type": leave_type,
            "from_date": from_date,
            "to_date": to_date,
            "reason": reason,
            "description": description,
            "status": "Pending",
            "created_at": datetime.now(),
            "mode": "full",
        }
        res = leaves.insert_one(leave_data)
        socketio.emit('new_leave_request', {
            "id": str(res.inserted_id),
            "lecturer_name": current_user.name,
            "type": leave_type,
            "from_date": from_date,
            "to_date": to_date,
            "status": "Pending"
        })

        reset_pending()
        return _chatbot_make_reply(
            f"Submitted. Your leave request is Pending (ID: {str(res.inserted_id)}).",
            actions=[
                {"type": "navigate", "label": "Open Dashboard", "url": url_for("lecturer_dashboard")},
                {"type": "navigate", "label": "Apply another leave", "url": url_for("apply_leave", mode="full")},
            ],
        )

    # If user is in the middle of a flow, continue slot-filling
    if pending.get("intent") == "apply_leave":
        # allow user to restart with new dates
        if message:
            from_d, to_d = _extract_date_range(message)
            if from_d and to_d:
                pending["from_date"] = from_d
                pending["to_date"] = to_d

            if not pending.get("leave_type"):
                # Dynamically match against known types from global list
                active_types = {t['name'].lower(): t['name'] for t in leave_types.find()}
                for k, v in active_types.items():
                    if k in msg_lower:
                        pending["leave_type"] = v
                        break

            elif not pending.get("reason"):
                pending["reason"] = message
            elif not pending.get("description"):
                pending["description"] = message

            set_pending(pending)

        missing = []
        if not pending.get("from_date") or not pending.get("to_date"):
            missing.append("dates")
        if not pending.get("leave_type"):
            missing.append("leave type")
        if not pending.get("reason"):
            missing.append("reason")
        if not pending.get("description"):
            missing.append("description")

        if missing:
            if missing[0] == "dates":
                return _chatbot_make_reply("Tell me the leave dates (example: 03-01-2026 to 06-01-2026).", actions=[{"type": "cancel_flow", "label": "Cancel"}])
            if missing[0] == "leave type":
                dynamic_actions = [
                    {"type": "set_leave_type", "label": name, "value": name} 
                    for name in [t['name'] for t in leave_types.find().sort("name", 1)]
                ]
                dynamic_actions.append({"type": "cancel_flow", "label": "Cancel"})
                
                return _chatbot_make_reply(
                    "Choose a leave type.",
                    actions=dynamic_actions,
                )
            if missing[0] == "reason":
                return _chatbot_make_reply("What’s the reason for your leave?")
            if missing[0] == "description":
                return _chatbot_make_reply("Add a short description (1 line).")

        # All fields present → show confirmation card
        leaves_left = calculate_leaves_left(current_user.id)
        card = {
            "type": "leave_confirm",
            "title": "Confirm leave application",
            "fields": [
                {"label": "From", "value": pending.get("from_date")},
                {"label": "To", "value": pending.get("to_date")},
                {"label": "Type", "value": pending.get("leave_type")},
                {"label": "Reason", "value": pending.get("reason")},
                {"label": "Description", "value": pending.get("description")},
                {"label": "Leaves left (approx.)", "value": str(leaves_left)},
            ],
        }
        return _chatbot_make_reply(
            "Please confirm.",
            cards=[card],
            actions=[
                {"type": "confirm_leave", "label": "Confirm & Submit"},
                {"type": "cancel_flow", "label": "Cancel"},
            ],
        )

    # No active flow: detect intent from message
    msg_lower = message.lower()
    if not message:
        return _chatbot_make_reply(
            "Examples you can type:\n- Apply leave from 03-01-2026 to 06-01-2026\n- My timetable\n- My attendance\n- Leave balance",
            actions=[
                {"type": "navigate", "label": "Dashboard", "url": url_for("lecturer_dashboard")},
                {"type": "navigate", "label": "Timetable", "url": url_for("lecturer_timetable")},
                {"type": "navigate", "label": "Attendance", "url": url_for("lecturer_attendance")},
                {"type": "navigate", "label": "Apply Leave", "url": url_for("apply_leave", mode="full")},
            ],
        )

    # Quick navigation intents
    def _match_any(intent_key: str) -> bool:
        phrases = CHATBOT_INTENT_PHRASES.get(intent_key) or []
        return any(p in msg_lower for p in phrases)

    if "timetable" in msg_lower or _match_any("timetable"):
        return _chatbot_make_reply("Opening your timetable.", actions=[{"type": "navigate", "url": url_for("lecturer_timetable")}])
    if "attendance" in msg_lower or _match_any("attendance"):
        return _chatbot_make_reply("Opening your attendance.", actions=[{"type": "navigate", "url": url_for("lecturer_attendance")}])
    if "dashboard" in msg_lower or "home" in msg_lower or _match_any("dashboard"):
        return _chatbot_make_reply("Opening dashboard.", actions=[{"type": "navigate", "url": url_for("lecturer_dashboard")}])

    # Leaves left
    if ("leave" in msg_lower and ("left" in msg_lower or "balance" in msg_lower)) or _match_any("leave_balance"):
        leaves_left = calculate_leaves_left(current_user.id)
        return _chatbot_make_reply(f"You have {leaves_left} leave day(s) left (based on approved leaves).")

    # Apply leave intent
    is_leave_apply = (
        ("leave" in msg_lower and any(k in msg_lower for k in ("apply", "need", "want", "take")))
        or msg_lower.startswith("leave")
        or _match_any("apply_leave")
        or msg_lower.strip() in {"leave", "apply leave"}
    )
    if is_leave_apply:
        from_d, to_d = _extract_date_range(message)
        p = {"intent": "apply_leave"}
        if from_d and to_d:
            p["from_date"] = from_d
            p["to_date"] = to_d
        set_pending(p)
        if not (from_d and to_d):
            return _chatbot_make_reply(
                "Sure — first tell me the date range (example: 03-01-2026 to 06-01-2026).",
                actions=[
                    {"type": "navigate", "label": "Open Leave Form", "url": url_for("apply_leave", mode="full")},
                    {"type": "cancel_flow", "label": "Cancel"},
                ],
            )
        dynamic_actions = [
            {"type": "set_leave_type", "label": name, "value": name} 
            for name in [t['name'] for t in leave_types.find().sort("name", 1)]
        ]
        dynamic_actions.append({"type": "cancel_flow", "label": "Cancel"})

        return _chatbot_make_reply(
            "Got the dates. Now choose a leave type.",
            actions=dynamic_actions,
        )

    # Default fallback
    return _chatbot_make_reply(
        "I can help with Leave, Timetable, Attendance, and Leave balance. Try: “Apply leave from 03-01-2026 to 06-01-2026”.",
        actions=[
            {"type": "navigate", "label": "Apply Leave", "url": url_for("apply_leave", mode="full")},
            {"type": "navigate", "label": "Timetable", "url": url_for("lecturer_timetable")},
            {"type": "navigate", "label": "Attendance", "url": url_for("lecturer_attendance")},
        ],
    )


@app.route('/lecturer/attendance')
@login_required
@lecturer_required
def lecturer_attendance():
    """
    Attendance view for the logged-in lecturer.
    Reads JSON attendance files from ATTENDANCE_DIR and filters by this lecturer's staff ID.
    """
    base_dir = (os.getenv("ATTENDANCE_DIR") or "").strip()
    from datetime import datetime

    # Filters
    selected_month = (request.args.get("month") or "").strip()
    search_q = (request.args.get("q") or "").strip().lower()

    if not selected_month:
        selected_month = datetime.now().strftime("%Y-%m")

    # Find staff_id for current lecturer
    staff_doc = users.find_one({"_id": ObjectId(current_user.id)})
    staff_id = staff_doc.get("staff_id") if staff_doc else None

    records = []
    debug_info = {
        "base_dir": base_dir,
        "dir_exists": os.path.isdir(base_dir) if base_dir else False,
        "staff_id": staff_id,
        "json_files": [],
        "total_rows_all_files": 0,
        "rows_for_staff_before_filters": 0,
    }
    today_str = datetime.now().strftime("%Y-%m-%d")

    if base_dir and debug_info["dir_exists"] and staff_id:
        for fname in os.listdir(base_dir):
            if not fname.lower().endswith(".json"):
                continue
            fpath = os.path.join(base_dir, fname)
            debug_info["json_files"].append(fname)

            try:
                with open(fpath, encoding="utf-8") as f:
                    # Try to load as a JSON array or object first
                    try:
                        data = json.load(f)
                        if isinstance(data, dict):
                            data = [data]
                    except json.JSONDecodeError:
                        # Fallback: newline-delimited JSON objects
                        f.seek(0)
                        data = []
                        for line in f:
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                data.append(json.loads(line))
                            except Exception:
                                continue

                for row in data:
                    debug_info["total_rows_all_files"] += 1

                    if row.get("staff_id") != staff_id:
                        continue
                    debug_info["rows_for_staff_before_filters"] += 1

                    checkin = row.get("checkin") or ""
                    checkout = row.get("checkout") or ""
                    name = row.get("name") or ""

                    # Derive date and month from checkin
                    iso_date = ""
                    display_date = ""
                    time_in = ""
                    time_out = ""
                    if checkin:
                        try:
                            dt = datetime.fromisoformat(checkin)
                            iso_date = dt.date().isoformat()
                            display_date = dt.date().strftime("%d-%m-%Y")
                            time_in = dt.time().strftime("%H:%M")
                        except Exception:
                            # Fallback: first 10 chars as date, last 8 as time if possible
                            if len(checkin) >= 10:
                                iso_date = checkin[:10]
                                try:
                                    dparts = iso_date.split("-")
                                    if len(dparts) == 3:
                                        display_date = f"{dparts[2]}-{dparts[1]}-{dparts[0]}"
                                except Exception:
                                    display_date = iso_date
                            if len(checkin) >= 19:
                                time_in = checkin[11:16]

                    if checkout:
                        try:
                            dt_out = datetime.fromisoformat(checkout)
                            time_out = dt_out.time().strftime("%H:%M")
                        except Exception:
                            if len(checkout) >= 19:
                                time_out = checkout[11:16]

                    # Month filter based on iso_date (YYYY-MM)
                    if iso_date and not iso_date.startswith(selected_month):
                        continue

                    # Simple status from presence of checkin/checkout
                    if checkin and checkout:
                        status = "Present"
                    elif checkin:
                        status = "Checked-in"
                    else:
                        status = "Unknown"

                    extra = f"In: {checkin}  Out: {checkout}"
                    if name:
                        extra = f"{name} | " + extra

                    text_blob = f"{iso_date} {time_in} {time_out} {status} {extra}".lower()
                    if search_q and search_q not in text_blob:
                        continue

                    records.append({
                        "date": iso_date,
                        "display_date": display_date or iso_date,
                        "time_in": time_in,
                        "time_out": time_out,
                        "status": status,
                        "extra": extra,
                    })
            except Exception:
                continue

    # Sort by date+time descending
    def sort_key(rec):
        return (rec.get("date") or "", rec.get("time") or "")

    records.sort(key=sort_key, reverse=True)

    today_records = [r for r in records if r.get("date") == today_str]

    return render_template(
        "lecturer/attendance.html",
        records=records,
        today_records=today_records,
        month=selected_month,
        q=search_q,
        debug_info=debug_info,
    )

@app.route('/lecturer/apply-leave', methods=['GET', 'POST'])
@login_required
@lecturer_required
def apply_leave():
    mode = request.args.get('mode', 'full')
    if request.method == 'POST':
        leave_mode = request.form.get('mode', 'full')
        
        if leave_mode == 'time':
            today_str = datetime.now().strftime('%Y-%m-%d')
            time_from = request.form.get('time_from', '')
            time_to = request.form.get('time_to', '')
            from_date = f"{today_str} {time_from}"
            to_date = f"{today_str} {time_to}"
        else:
            from_date = request.form.get('from_date')
            to_date = request.form.get('to_date')

        # FRESH START: Delete any old drafts/temporary allocations before submitting new application
        leave_class_allocations.delete_many({
            "assigned_by": str(current_user.id),
            "is_draft": True
        })

        leave_data = {
            "lecturer_id": current_user.id,
            "lecturer_name": current_user.name,
            "type": request.form.get('type'),
            "from_date": from_date,
            "to_date": to_date,
            "reason": request.form.get('reason'),
            "status": "Pending",
            "created_at": datetime.now(),
            "mode": leave_mode,
            "half_day": request.form.get('half_day') == 'on',
            "session": request.form.get('session') if request.form.get('half_day') == 'on' else None
        }
        res = leaves.insert_one(leave_data)
        leave_id = str(res.inserted_id)
        
        # Process assignments if provided (integrated flow)
        assignments_raw = request.form.get('assignments_json')
        if assignments_raw:
            try:
                import json
                assignments_data = json.loads(assignments_raw)
                for entry in assignments_data:
                    assigned_to_id = entry.get('assigned_to_id')
                    class_details = entry.get('class_details')
                    if assigned_to_id and class_details:
                        # DUPLICATE SHIELD: Check if this class is already assigned and accepted/approved
                        existing = leave_class_allocations.find_one({
                            "assigned_by": str(current_user.id),
                            "assigned_to": assigned_to_id,
                            "class_details.date": class_details.get('date'),
                            "class_details.time": class_details.get('time'),
                            "class_details.subject": class_details.get('subject'),
                            "status": {"$in": ["accepted", "approved", "finalized"]}
                        })
                        if existing:
                            # Already officially linked, just update the leave_id and skip notification
                            leave_class_allocations.update_one({"_id": existing['_id']}, {"$set": {"leave_id": leave_id}})
                            continue

                        target_faculty = users.find_one({"_id": ObjectId(assigned_to_id)})
                        # 1. Save Allocation Record
                        alloc_res = leave_class_allocations.insert_one({
                            "leave_id": leave_id,
                            "assigned_by": str(current_user.id),
                            "assigned_by_name": current_user.name,
                            "assigned_to": assigned_to_id,
                            "assigned_to_name": target_faculty.get('name', 'Unknown') if target_faculty else 'Unknown',
                            "class_details": class_details,
                            "status": "Pending",
                            "created_at": datetime.now()
                        })
                        alloc_id = str(alloc_res.inserted_id)

                        # 2. Create Notification for the assigned faculty
                        faculty_notifications.insert_one({
                            "recipient_id": assigned_to_id,
                            "sender_id": str(current_user.id),
                            "sender_name": current_user.name,
                            "message": f"{current_user.name} requested you for substitution: {class_details.get('subject')} on {class_details.get('date')} at {class_details.get('time')}.",
                            "type": "class_assignment",
                            "allocation_id": alloc_id,
                            "leave_id": leave_id,  # LINKED FOR CLEANUP
                            "class_details": class_details,
                            "status": "unread",
                            "created_at": datetime.now()
                        })

                        # 3. Real-time Socket Notification
                        socketio.emit('new_class_assignment', {
                            "recipient_id": assigned_to_id,
                            "message": f"New class substitution request from {current_user.name}",
                            "allocation_id": alloc_id
                        })
            except Exception as e:
                print(f"DEBUG: Assignment Error: {e}")

        socketio.emit('new_leave_request', {
            "id": leave_id,
            "lecturer_name": current_user.name,
            "type": request.form.get('type'),
            "from_date": from_date,
            "to_date": to_date,
            "status": "Pending"
        })
        
        # CLEANUP: Clear the draft after successful submission
        leave_drafts.delete_one({"user_id": str(current_user.id)})

        flash("Leave application submitted successfully with class assignments!", "success")
        return redirect(url_for('lecturer_dashboard'))
    
    return render_template('lecturer/apply_leave.html', mode=mode)


@app.route('/lecturer/leave/<id>/cancel', methods=['POST'])
@login_required
@lecturer_required
def cancel_leave(id):
    """
    Allow a lecturer to cancel one of their own pending leave requests.
    """
    leave_doc = leaves.find_one({"_id": ObjectId(id), "lecturer_id": current_user.id})
    if not leave_doc:
        flash("Leave request not found.", "danger")
        return redirect(url_for('lecturer_dashboard'))

    if leave_doc.get("status") != "Pending":
        flash("Only pending leave requests can be cancelled.", "warning")
        return redirect(url_for('lecturer_dashboard'))

    leaves.update_one(
        {"_id": leave_doc["_id"]},
        {"$set": {"status": "Cancelled", "cancelled_at": datetime.now()}},
    )
    
    socketio.emit('leave_cancelled', {'id': id, 'lecturer_id': current_user.id})
    flash("Leave request cancelled.", "success")
    return redirect(url_for('lecturer_dashboard'))

@app.route('/lecturer/leave/api/<id>/cancel', methods=['POST'])
@login_required
@lecturer_required
def api_cancel_leave(id):
    leave_doc = leaves.find_one({"_id": ObjectId(id), "lecturer_id": current_user.id})
    if not leave_doc:
        return jsonify({"success": False, "message": "Not found"}), 404
        
    if leave_doc.get("status") != "Pending":
        return jsonify({"success": False, "message": "Not pending"}), 400
        
    leaves.update_one(
        {"_id": leave_doc["_id"]},
        {"$set": {"status": "Cancelled", "cancelled_at": datetime.now()}},
    )
    
    socketio.emit('leave_cancelled', {'id': id, 'lecturer_id': current_user.id})
    return jsonify({"success": True})

@app.route('/lecturer/salary')
@login_required
@lecturer_required
def view_salary():
    my_salaries = list(salaries.find({"lecturer_id": current_user.id}).sort("month_year", -1))
    return render_template('lecturer/salary.html', salaries=my_salaries)


@app.route('/lecturer/timetable')
@login_required
@lecturer_required
def lecturer_timetable():
    """Show the logged-in lecturer's own timetable image and structured data."""
    # 1. Try DB first
    tt_doc = timetable.find_one({"lecturer_id": current_user.id})
    
    # 2. Try disk-based JSON if DB is missing or structured is empty
    structured = tt_doc.get("structured") if tt_doc else {}
    if not structured:
        # Try finding by staff_id or username
        staff_doc = users.find_one({"_id": ObjectId(current_user.id)})
        staff_id = staff_doc.get("staff_id") if staff_doc else current_user.username.upper()
        
        json_path = os.path.join(os.path.dirname(__file__), "static", "json_timetables", f"{staff_id}.json")
        if os.path.exists(json_path):
            try:
                with open(json_path, 'r', encoding='utf-8') as f:
                    structured = json.load(f)
            except:
                pass

    # 3. Default empty structure if still nothing (allows manual creation)
    if not structured:
        structured = {
            "faculty": current_user.name,
            "timetable": {
                "periods": [
                    {"period": "0", "time": ""},
                    {"period": "I", "time": "9.45-10.35"},
                    {"period": "II", "time": "10.40-11.30"},
                    {"period": "III", "time": "11.35-12.25"},
                    {"period": "IV", "time": "1.05-1.55"},
                    {"period": "V", "time": "2.00-2.50"},
                    {"period": "VI", "time": "2.55-3.45"},
                    {"period": "VII", "time": ""}
                ],
                "days": [{"day": d, "slots": {}} for d in ["MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY"]]
            }
        }

    image_url = None
    if tt_doc and tt_doc.get("image_path"):
        image_path = (tt_doc.get("image_path") or "").replace("\\", "/")
        image_url = url_for("static", filename=image_path)
    
    return render_template(
        'lecturer/timetable.html',
        has_timetable=image_url is not None,
        timetable_image_url=image_url,
        structured=structured,
    )


@app.route('/lecturer/timetable/edit', methods=['GET', 'POST'])
@login_required
@lecturer_required
def edit_lecturer_timetable():
    """
    Saves or updates the structured timetable data for the current lecturer.
    """
    tt_doc = timetable.find_one({"lecturer_id": current_user.id})
    
    if request.method == 'POST':
        raw = request.form.get("structured_json", "").strip()
        if not raw:
            flash("Timetable data cannot be empty.", "danger")
            return redirect(url_for('lecturer_timetable'))
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Data must be a JSON object.")
        except Exception as exc:
            flash(f"Invalid data format: {exc}", "danger")
            return redirect(url_for('lecturer_timetable'))

        # Update or Create in DB
        timetable.update_one(
            {"lecturer_id": current_user.id},
            {
                "$set": {
                    "lecturer_id": current_user.id,
                    "lecturer_name": current_user.name,
                    "structured": data,
                    "updated_at": datetime.now()
                }
            },
            upsert=True
        )

        # Persistence: Sync back to the static JSON folder
        staff_doc = users.find_one({"_id": ObjectId(current_user.id)})
        staff_id = staff_doc.get("staff_id") if staff_doc else current_user.username.upper()
        
        json_dir = os.path.join(os.path.dirname(__file__), "static", "json_timetables")
        os.makedirs(json_dir, exist_ok=True)
        json_path = os.path.join(json_dir, f"{staff_id}.json")
        
        try:
            with open(json_path, "w", encoding="utf-8") as f_json:
                json.dump(data, f_json, indent=4, ensure_ascii=False)
        except Exception as json_err:
            print(f"Error saving JSON file at {json_path}: {json_err}")

        flash("Timetable synchronized successfully.", "success")
        return redirect(url_for('lecturer_timetable'))

    # GET request - should ideally not reach here if using modal edit on the main timetable page
    # but kept for backward compatibility if template uses a separate page.
    structured = tt_doc.get("structured", {}) if tt_doc else {}
    return render_template(
        'lecturer/edit_timetable.html',
        structured_json=json.dumps(structured, indent=2, ensure_ascii=False),
    )


# ============ LEAVE CLASS ALLOCATION ROUTES ============

@app.route('/lecturer/leave/<leave_id>/allocate-classes')
@login_required
@lecturer_required
def allocate_leave_classes(leave_id):
    """Page to allocate classes to other faculty members"""
    leave_doc = leaves.find_one({"_id": ObjectId(leave_id), "lecturer_id": current_user.id})
    if not leave_doc:
        flash("Leave request not found.", "danger")
        return redirect(url_for('lecturer_dashboard'))
    
    if leave_doc.get('status') != 'Pending':
        flash("Can only allocate classes for pending leave requests.", "warning")
        return redirect(url_for('lecturer_dashboard'))
    
    # Get staff_id for current lecturer
    staff_doc = users.find_one({"_id": ObjectId(current_user.id)})
    staff_id = staff_doc.get('staff_id') if staff_doc else None
    
    # Get classes during leave period
    from_date = leave_doc.get('from_date', '').split(' ')[0] if ' ' in leave_doc.get('from_date', '') else leave_doc.get('from_date', '')
    to_date = leave_doc.get('to_date', '').split(' ')[0] if ' ' in leave_doc.get('to_date', '') else leave_doc.get('to_date', '')
    
    if leave_doc.get('mode') == 'time':
        # For time-based leave, just use today
        today = datetime.now().strftime('%Y-%m-%d')
        classes = get_classes_on_date(staff_id, today) if staff_id else []
        # Filter by time if specified
        time_from = leave_doc.get('from_date', '').split(' ')[1] if ' ' in leave_doc.get('from_date', '') else None
        time_to = leave_doc.get('to_date', '').split(' ')[1] if ' ' in leave_doc.get('to_date', '') else None
        if time_from and time_to:
            classes = [c for c in classes if time_from <= c.get('time', '') <= time_to]
    else:
        classes = get_classes_for_leave_period(staff_id, from_date, to_date) if staff_id else []
    
    # Get all faculty except current user for assignment
    all_faculty_raw = list(users.find({
        "role": "lecturer",
        "_id": {"$ne": ObjectId(current_user.id)}
    }).sort("name", 1))
    
    # Convert faculty data to JSON-serializable format
    all_faculty = []
    for f in all_faculty_raw:
        faculty_data = {
            'id': str(f['_id']),
            'name': f.get('name', ''),
            'staff_id': f.get('staff_id', ''),
            'email': f.get('email', ''),
            'department': f.get('department', '')
        }
        all_faculty.append(faculty_data)
    
    # Get existing allocations
    existing_allocations_raw = list(leave_class_allocations.find({"leave_id": leave_id}))
    existing_allocations = []
    for a in existing_allocations_raw:
        alloc_data = {
            'id': str(a['_id']),
            'leave_id': a.get('leave_id'),
            'assigned_by': a.get('assigned_by'),
            'assigned_to': a.get('assigned_to'),
            'assigned_to_name': a.get('assigned_to_name'),
            'class_details': a.get('class_details', {}),
            'status': a.get('status'),
            'created_at': a.get('created_at').isoformat() if a.get('created_at') else None
        }
        existing_allocations.append(alloc_data)
    
    allocated_class_ids = [a['class_details'].get('class_id') for a in existing_allocations if a['class_details']]
    
    return render_template(
        'lecturer/allocate_classes.html',
        leave=leave_doc,
        classes=classes,
        faculty=all_faculty,
        allocated_class_ids=allocated_class_ids,
        existing_allocations=existing_allocations
    )


@app.route('/lecturer/leave/<leave_id>/assign-class', methods=['POST'])
@login_required
@lecturer_required
def assign_leave_class(leave_id):
    """Assign a specific class to another faculty member"""
    leave_doc = leaves.find_one({"_id": ObjectId(leave_id), "lecturer_id": current_user.id})
    if not leave_doc:
        return jsonify({"success": False, "message": "Leave not found"}), 404
    
    class_data = request.json
    assigned_to_id = class_data.get('assigned_to_id')
    class_details = class_data.get('class_details', {})
    
    # Create class allocation record
    allocation = {
        'leave_id': leave_id,
        'assigned_by': current_user.id,
        'assigned_to': assigned_to_id,
        'class_details': class_details,
        'status': 'pending_faculty',  # pending_faculty, accepted, rejected, approved
        'created_at': datetime.now()
    }
    
    result = leave_class_allocations.insert_one(allocation)
    allocation_id = str(result.inserted_id)
    
    # Create notification for assigned faculty
    notification = {
        'type': 'class_assignment',
        'allocation_id': allocation_id,
        'leave_id': leave_id,
        'recipient_id': assigned_to_id,
        'sender_id': current_user.id,
        'sender_name': current_user.name,
        'message': f"{current_user.name} has requested you to take their class on {class_details.get('date')} at {class_details.get('time')} ({class_details.get('subject')})",
        'class_details': class_details,
        'status': 'unread',
        'created_at': datetime.now()
    }
    faculty_notifications.insert_one(notification)
    
    # Emit socket event for real-time notification
    socketio.emit('new_class_assignment', {
        'recipient_id': assigned_to_id,
        'message': notification['message'],
        'allocation_id': allocation_id
    })
    
    return jsonify({
        "success": True,
        "allocation_id": allocation_id,
        "message": "Class assigned successfully. Waiting for faculty approval."
    })


@app.route('/lecturer/api/request-substitution', methods=['POST'])
@login_required
@lecturer_required
def api_request_substitution():
    data = request.json
    faculty_id = data.get('faculty_id')
    class_details = data.get('class_details')
    
    if not faculty_id or not class_details:
        return jsonify({"success": False, "message": "Missing data"}), 400
        
    target_faculty = users.find_one({"_id": ObjectId(faculty_id)})
    if not target_faculty:
        return jsonify({"success": False, "message": "Faculty not found"}), 404
        
    # Create allocation record (without leave_id yet, using 'Draft' or 'Pre-Leave')
    alloc_data = {
        "assigned_by": str(current_user.id),
        "assigned_by_name": current_user.name,
        "assigned_to": faculty_id,
        "assigned_to_name": target_faculty.get('name', 'Unknown'),
        "class_details": class_details,
        "status": "Pending",
        "created_at": datetime.now(),
        "is_draft": True
    }
    res = leave_class_allocations.insert_one(alloc_data)
    alloc_id = str(res.inserted_id)
    
    # Send Notification
    faculty_notifications.insert_one({
        "recipient_id": faculty_id,
        "sender_id": str(current_user.id),
        "sender_name": current_user.name,
        "message": f"{current_user.name} requested substitution for: {class_details.get('subject')} on {class_details.get('date')}.",
        "type": "class_assignment",
        "allocation_id": alloc_id,
        "class_details": class_details,
        "status": "unread",
        "created_at": datetime.now()
    })
    
    socketio.emit('new_class_assignment', {
        "recipient_id": faculty_id,
        "message": f"New substitution request from {current_user.name}",
        "allocation_id": alloc_id
    })
    
    return jsonify({"success": True, "allocation_id": alloc_id})

@app.route('/lecturer/api/save-draft', methods=['POST'])
@login_required
@lecturer_required
def api_save_leave_draft():
    data = request.json
    if not data:
        return jsonify({"success": False, "message": "No data provided"}), 400
    
    # Update or insert draft for current user
    leave_drafts.update_one(
        {"user_id": str(current_user.id)},
        {"$set": {
            "draft_data": data,
            "updated_at": datetime.now()
        }},
        upsert=True
    )
    return jsonify({"success": True})

@app.route('/lecturer/api/clear-draft', methods=['POST'])
@login_required
@lecturer_required
def api_clear_leave_draft():
    leave_drafts.delete_one({"user_id": str(current_user.id)})
    return jsonify({"success": True})

@app.route('/lecturer/api/get-draft', methods=['GET'])
@login_required
@lecturer_required
def api_get_leave_draft():
    draft = leave_drafts.find_one({"user_id": str(current_user.id)})
    if draft:
        return jsonify({"success": True, "draft": draft['draft_data']})
    return jsonify({"success": False, "message": "No draft found"})

@app.route('/lecturer/api/preview-classes')
@login_required
@lecturer_required
def api_preview_classes():
    """Preview classes that occur during a proposed leave period"""
    from_date = request.args.get('from_date')
    to_date = request.args.get('to_date')
    mode = request.args.get('mode', 'full')
    time_from = request.args.get('time_from')
    time_to = request.args.get('time_to')
    half_day = request.args.get('half_day') == 'true'
    session_type = request.args.get('session')  # 'morning' or 'afternoon'
    
    # Get staff_id
    staff_doc = users.find_one({"_id": ObjectId(current_user.id)})
    staff_id = staff_doc.get('staff_id') if staff_doc else None
    
    if not staff_id:
        return jsonify({"success": False, "message": "Staff ID not found", "classes": []})
        
    classes = []
    if mode == 'time':
        today = datetime.now().strftime('%Y-%m-%d')
        classes = get_classes_on_date(staff_id, today)
        if time_from and time_to:
            classes = [c for c in classes if time_from <= c.get('time', '') <= time_to]
    else:
        if from_date and to_date:
            classes = get_classes_for_leave_period(staff_id, from_date, to_date)
            
    # Filter by session if half-day
    if half_day and session_type:
        morning_periods = ["0", "I", "II", "III"]
        afternoon_periods = ["IV", "V", "VI", "VII"]
        
        if session_type == 'morning':
            classes = [c for c in classes if str(c.get('period')) in morning_periods]
        elif session_type == 'afternoon':
            classes = [c for c in classes if str(c.get('period')) in afternoon_periods]
            
    # Get faculty list for assignments
    faculty = list(users.find({
        "role": "lecturer",
        "_id": {"$ne": ObjectId(current_user.id)}
    }, {"name": 1, "staff_id": 1}).sort("name", 1))
    
    for f in faculty:
        f['_id'] = str(f['_id'])
        
    # Get existing live assignments for these dates (Ignore rejected ones)
    live_allocs = list(leave_class_allocations.find({
        "assigned_by": str(current_user.id),
        "status": {"$in": ["pending", "accepted", "approved", "finalized"]}
    }))
    
    # Map allocations to classes by a unique key (subject+date+time) for easy frontend syncing
    alloc_map = {}
    for a in live_allocs:
        c = a.get('class_details', {})
        # Normalize: Trim spaces and ignore case for robust matching
        sub = str(c.get('subject', '')).strip().upper()
        key = f"{c.get('date')}_{c.get('time')}_{sub}".replace(' ', '_')
        alloc_map[key] = {
            "status": a.get('status'),
            "faculty_id": a.get('assigned_to'),
            "allocation_id": str(a.get('_id'))
        }

    return jsonify({
        "success": True,
        "classes": classes,
        "faculty": faculty,
        "live_allocations": alloc_map
    })


@app.route('/lecturer/api/faculty-timetable/<staff_id>')
@login_required
def api_faculty_timetable(staff_id):
    """Get faculty timetable for a specific date"""
    date_str = request.args.get('date')
    if not date_str:
        return jsonify({"success": False, "message": "Date required"}), 400
    
    classes = get_classes_on_date(staff_id, date_str)
    faculty = users.find_one({"staff_id": staff_id})
    
    return jsonify({
        "success": True,
        "faculty_name": faculty.get('name') if faculty else 'Unknown',
        "classes": classes,
        "date": date_str,
        "day": get_day_name_from_date(date_str)
    })


@app.route('/lecturer/my-assignments')
@login_required
@lecturer_required
def my_class_assignments():
    """Show pending class assignments for the current faculty member"""
    # Get unread/pending notifications
    raw = list(faculty_notifications.find({
        "recipient_id": str(current_user.id),
        "status": {"$in": ["unread", "pending"]}
    }).sort("created_at", -1))
    
    # Get accepted/approved assignments history
    accepted_assignments = list(leave_class_allocations.find({
        "assigned_to": str(current_user.id),
        "status": {"$in": ["accepted", "approved"]}
    }).sort("created_at", -1))
    
    # CLASS-BASED PURGE: Create a set of classes the user has already accepted/approved
    confirmed_set = set()
    for a in accepted_assignments:
        c = a.get('class_details', {})
        sub = str(c.get('subject', '')).strip().upper()
        conf_key = f"{c.get('date')}_{c.get('time')}_{sub}"
        confirmed_set.add(conf_key)

    # GHOST HUNTER: Verify each notification has a real allocation record and isn't a duplicate
    notifications_raw = []
    for n in raw:
        alloc_id = n.get('allocation_id')
        c = n.get('class_details', {})
        sub = str(c.get('subject', '')).strip().upper()
        notif_key = f"{c.get('date')}_{c.get('time')}_{sub}"
        
        # 1. Check if they already have an official assignment for this exact class
        if notif_key in confirmed_set:
            faculty_notifications.delete_one({"_id": n['_id']})
            continue

        if alloc_id:
            exists = leave_class_allocations.find_one({"_id": ObjectId(alloc_id)})
            if exists:
                # 2. AUTO-CLOSURE: If this specific assignment is already processed (Accepted/Rejected/Approved/Finalized)
                if exists.get('status') in ['approved', 'finalized', 'accepted', 'rejected']:
                    faculty_notifications.delete_one({"_id": n['_id']})
                else:
                    notifications_raw.append(n)
            else:
                # 3. BREAKING THE GHOST: Assignment is gone
                faculty_notifications.delete_one({"_id": n['_id']})
        else:
            notifications_raw.append(n)


    # Convert notifications to JSON-serializable format
    notifications = []
    for n in notifications_raw:
        notifications.append({
            'id': str(n['_id']),
            'allocation_id': n.get('allocation_id'),
            'leave_id': n.get('leave_id'),
            'recipient_id': n.get('recipient_id'),
            'sender_id': n.get('sender_id'),
            'sender_name': n.get('sender_name'),
            'message': n.get('message'),
            'class_details': n.get('class_details', {}),
            'status': n.get('status'),
            'created_at': n.get('created_at')
        })
    
    # Get all related allocations
    allocation_ids = [n.get('allocation_id') for n in notifications if n.get('allocation_id')]
    allocations = []
    for alloc_id in allocation_ids:
        alloc = leave_class_allocations.find_one({"_id": ObjectId(alloc_id)})
        if alloc:
            allocations.append({
                'id': str(alloc['_id']),
                'leave_id': alloc.get('leave_id'),
                'assigned_by': alloc.get('assigned_by'),
                'assigned_to': alloc.get('assigned_to'),
                'class_details': alloc.get('class_details', {}),
                'status': alloc.get('status'),
                'created_at': alloc.get('created_at')
            })
    
    return render_template(
        'lecturer/my_assignments.html',
        notifications=notifications,
        allocations=allocations,
        accepted_assignments=accepted_assignments
    )


@app.route('/lecturer/assignment/<allocation_id>/<action>', methods=['POST'])
@login_required
@lecturer_required
def respond_to_assignment(allocation_id, action):
    """Accept or reject a class assignment"""
    if action not in ['accept', 'reject']:
        return jsonify({"success": False, "message": "Invalid action"}), 400
    
    allocation = leave_class_allocations.find_one({"_id": ObjectId(allocation_id)})
    if not allocation:
        return jsonify({"success": False, "message": "Assignment not found"}), 404
    
    # Verify this assignment is for the current user
    if allocation.get('assigned_to') != current_user.id:
        return jsonify({"success": False, "message": "Not authorized"}), 403
    
    new_status = 'accepted' if action == 'accept' else 'rejected'
    leave_class_allocations.update_one(
        {"_id": ObjectId(allocation_id)},
        {"$set": {"status": new_status, "responded_at": datetime.now()}}
    )
    
    # Update notification status (mark all notifications for this allocation as read)
    faculty_notifications.update_many(
        {"allocation_id": allocation_id, "recipient_id": current_user.id},
        {"$set": {"status": "read", "response": action}}
    )
    
    # Notify the leave applicant
    recipient_id = None
    leave_id = allocation.get('leave_id')
    if leave_id:
        leave_doc = leaves.find_one({"_id": ObjectId(leave_id)})
        if leave_doc:
            recipient_id = leave_doc.get('lecturer_id')
    
    # If no leave_id (draft mode), use assigned_by
    if not recipient_id:
        recipient_id = allocation.get('assigned_by')

    if recipient_id:
        socketio.emit('assignment_response', {
            'recipient_id': str(recipient_id),
            'allocation_id': allocation_id,
            'action': action,
            'message': f"{current_user.name} has {action}ed your class assignment"
        })
    
    # If rejected, update leave to indicate class allocation failed
    if action == 'reject':
        leaves.update_one(
            {"_id": ObjectId(allocation.get('leave_id'))},
            {"$set": {"class_allocation_status": "needs_reassignment"}}
        )
    
    return jsonify({
        "success": True,
        "message": f"Assignment {action}ed successfully"
    })


# Admin routes for managing class allocations
@app.route('/admin/leave/<leave_id>/class-allocation-sheet')
@login_required
@admin_required
def admin_class_allocation_sheet(leave_id):
    """Show detailed formal sheet view of class allocations for a leave request"""
    leave_doc = leaves.find_one({"_id": ObjectId(leave_id)})
    if not leave_doc:
        flash("Leave request not found.", "danger")
        return redirect(url_for('admin_leaves'))
    
    # Get all allocations for this leave
    allocations_raw = list(leave_class_allocations.find({"leave_id": leave_id}))
    
    # Convert allocations to JSON-serializable format
    allocations = []
    for alloc in allocations_raw:
        assigned_to = users.find_one({"_id": ObjectId(alloc.get('assigned_to', ''))}) if alloc.get('assigned_to') else None
        assigned_by = users.find_one({"_id": ObjectId(alloc.get('assigned_by', ''))}) if alloc.get('assigned_by') else None
        
        allocations.append({
            'id': str(alloc['_id']),
            'leave_id': alloc.get('leave_id'),
            'assigned_to': alloc.get('assigned_to'),
            'assigned_by': alloc.get('assigned_by'),
            'assigned_to_name': assigned_to.get('name') if assigned_to else 'Unknown',
            'assigned_by_name': assigned_by.get('name') if assigned_by else 'Unknown',
            'class_details': alloc.get('class_details', {}),
            'status': alloc.get('status'),
            'created_at': alloc.get('created_at').isoformat() if alloc.get('created_at') else None,
            'responded_at': alloc.get('responded_at').isoformat() if alloc.get('responded_at') else None
        })
    
    # Fresh Lecturer Details (for Dept/Designation)
    lecturer = users.find_one({"_id": ObjectId(leave_doc.get('lecturer_id', ''))})
    staff_id = lecturer.get('staff_id') if lecturer else None
    
    # Calculate Total Days
    total_days = 0
    try:
        from datetime import datetime
        f_str = leave_doc.get('from_date', '').split(' ')[0]
        t_str = leave_doc.get('to_date', '').split(' ')[0]
        f_dt = datetime.strptime(f_str, '%Y-%m-%d')
        t_dt = datetime.strptime(t_str, '%Y-%m-%d')
        total_days = (t_dt - f_dt).days + 1
        if leave_doc.get('half_day'):
            total_days = 0.5
    except:
        total_days = "N/A"

    # Get Leave Stats for the Credits section (CL, EL, RH)
    all_stats = get_all_leave_stats(str(leave_doc.get('lecturer_id')))
    leave_stats_map = {
        'CL': next((s['left'] for s in all_stats if s['type'] == 'Casual Leave'), 0),
        'EL': next((s['left'] for s in all_stats if s['type'] == 'Earned Leave'), 0),
        'RH': next((s['left'] for s in all_stats if s['type'] == 'Restricted Holiday'), 0)
    }
    
    original_classes = get_classes_for_leave_period(staff_id, f_str, t_str) if staff_id else []
    
    # Load original timetable backup
    original_timetable = None
    backup_doc = timetable_history.find_one({
        "staff_id": staff_id,
        "reason": "leave_assignment"
    })
    if backup_doc:
        original_timetable = backup_doc.get('original_data')
    
    return render_template(
        'admin/leave_application_sheet.html',
        leave=leave_doc,
        lecturer=lecturer,
        allocations=allocations,
        leave_stats=leave_stats_map,
        total_days=total_days,
        original_classes=original_classes,
        original_timetable=original_timetable,
        applicant_name=leave_doc.get('lecturer_name')
    )


@app.route('/admin/leave/<leave_id>/finalize-allocation', methods=['POST'])
@login_required
@admin_required
def admin_finalize_allocation(leave_id):
    """Finalize class allocations and update timetables"""
    leave_doc = leaves.find_one({"_id": ObjectId(leave_id)})
    if not leave_doc:
        return jsonify({"success": False, "message": "Leave not found"}), 404
    
    # Get all accepted allocations
    allocations = list(leave_class_allocations.find({
        "leave_id": leave_id,
        "status": "accepted"
    }))
    
    if not allocations:
        return jsonify({"success": False, "message": "No accepted class allocations found"}), 400
    
    # Get applicant's staff_id
    applicant = users.find_one({"_id": ObjectId(leave_doc.get('lecturer_id'))})
    applicant_staff_id = applicant.get('staff_id') if applicant else None
    
    # Save original timetable backup before making changes
    if applicant_staff_id:
        original_timetable = load_faculty_timetable(applicant_staff_id)
        if original_timetable:
            save_timetable_backup(applicant_staff_id, original_timetable, "leave_assignment")
    
    # Update timetables for each allocation
    updated_count = 0
    for alloc in allocations:
        assigned_to_id = alloc.get('assigned_to')
        class_details = alloc.get('class_details', {})
        
        assigned_faculty = users.find_one({"_id": ObjectId(assigned_to_id)})
        assigned_staff_id = assigned_faculty.get('staff_id') if assigned_faculty else None
        
        if assigned_staff_id:
            # Add class to assigned faculty's timetable
            success = add_class_to_timetable(assigned_staff_id, class_details)
            if success:
                updated_count += 1
                # Remove class from applicant's timetable
                if applicant_staff_id:
                    remove_class_from_timetable(applicant_staff_id, class_details)
    
    # Update leave status and allocation status
    leaves.update_one(
        {"_id": ObjectId(leave_id)},
        {"$set": {
            "class_allocation_status": "completed",
            "class_allocation_finalized_at": datetime.now(),
            "status": "Approved"  # Auto-approve after allocation
        }}
    )
    
    # Update all allocations to approved
    leave_class_allocations.update_many(
        {"leave_id": leave_id, "status": "accepted"},
        {"$set": {"status": "approved", "approved_at": datetime.now()}}
    )
    
    return jsonify({
        "success": True,
        "message": f"Class allocation finalized. {updated_count} classes assigned.",
        "updated_count": updated_count
    })


def add_class_to_timetable(staff_id, class_details):
    """Add a class to a faculty's timetable"""
    try:
        json_dir = os.path.join(os.path.dirname(__file__), "static", "json_timetables")
        json_path = os.path.join(json_dir, f"{staff_id}.json")
        
        if not os.path.exists(json_path):
            return False
        
        with open(json_path, 'r', encoding='utf-8') as f:
            timetable_data = json.load(f)
        
        day_name = class_details.get('day', '').upper()
        
        # Find the day and add the slot
        if isinstance(timetable_data, dict):
            for day_data in timetable_data.get('timetable', []):
                if day_data.get('day', '').upper() == day_name:
                    new_slot = {
                        'time': class_details.get('time'),
                        'subject': class_details.get('subject'),
                        'room': class_details.get('room', ''),
                        'is_assigned': True,
                        'original_faculty': class_details.get('original_faculty')
                    }
                    if 'slots' not in day_data:
                        day_data['slots'] = []
                    day_data['slots'].append(new_slot)
                    break
        elif isinstance(timetable_data, list):
            for day_data in timetable_data:
                if day_data.get('day', '').upper() == day_name:
                    new_slot = {
                        'time': class_details.get('time'),
                        'subject': class_details.get('subject'),
                        'room': class_details.get('room', ''),
                        'is_assigned': True,
                        'original_faculty': class_details.get('original_faculty')
                    }
                    if 'slots' not in day_data:
                        day_data['slots'] = []
                    day_data['slots'].append(new_slot)
                    break
        
        # Save updated timetable
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(timetable_data, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"Error adding class to timetable: {e}")
        return False


def remove_class_from_timetable(staff_id, class_details):
    """Remove a class from a faculty's timetable (mark as on-leave)"""
    try:
        json_dir = os.path.join(os.path.dirname(__file__), "static", "json_timetables")
        json_path = os.path.join(json_dir, f"{staff_id}.json")
        
        if not os.path.exists(json_path):
            return False
        
        with open(json_path, 'r', encoding='utf-8') as f:
            timetable_data = json.load(f)
        
        day_name = class_details.get('day', '').upper()
        time_slot = class_details.get('time')
        
        # Find and mark the slot as on-leave
        if isinstance(timetable_data, dict):
            for day_data in timetable_data.get('timetable', []):
                if day_data.get('day', '').upper() == day_name:
                    for slot in day_data.get('slots', []):
                        if slot.get('time') == time_slot:
                            slot['on_leave'] = True
                            slot['assigned_to'] = class_details.get('assigned_to_name')
                            break
                    break
        elif isinstance(timetable_data, list):
            for day_data in timetable_data:
                if day_data.get('day', '').upper() == day_name:
                    for slot in day_data.get('slots', []):
                        if slot.get('time') == time_slot:
                            slot['on_leave'] = True
                            slot['assigned_to'] = class_details.get('assigned_to_name')
                            break
                    break
        
        # Save updated timetable
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(timetable_data, f, indent=2, ensure_ascii=False)
        
        return True
    except Exception as e:
        print(f"Error removing class from timetable: {e}")
        return False


@app.route('/lecturer/api/notifications')
@login_required
@lecturer_required
def api_get_notifications():
    """Get unread notifications for current user"""
    notifications = list(faculty_notifications.find({
        "recipient_id": current_user.id,
        "status": "unread"
    }).sort("created_at", -1))
    
    # Convert ObjectIds to strings for JSON serialization
    for n in notifications:
        n['_id'] = str(n['_id'])
        if 'leave_id' in n:
            n['leave_id'] = str(n['leave_id'])
    
    return jsonify({
        "success": True,
        "notifications": notifications,
        "count": len(notifications)
    })



@app.route('/lecturer/api/cancel-substitution/<allocation_id>', methods=['POST'])
@login_required
@lecturer_required
def cancel_substitution(allocation_id):
    """Cancel a pending substitution request sent by the lecturer"""
    try:
        from bson import ObjectId
        # Delete the allocation record
        leave_class_allocations.delete_one({
            "_id": ObjectId(allocation_id), 
            "assigned_by": str(current_user.id),
            "status": {"$in": ["pending", "Pending", "pending_faculty"]}
        })
        # Delete the notification
        faculty_notifications.delete_one({"allocation_id": allocation_id})
        return jsonify({"success": True})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)})


if __name__ == '__main__':
    init_db()
    # On some Windows setups (especially with newer Python), the watchdog reloader can throw WinError 10038.
    # Disabling the reloader keeps dev runs stable; restart the server manually after code changes.
    socketio.run(app, debug=True, port=8000, use_reloader=False, allow_unsafe_werkzeug=True)

