from flask import Flask, render_template, request, redirect, url_for, flash, session, send_file, jsonify
from flask_socketio import SocketIO, emit
from io import BytesIO
from flask_bcrypt import Bcrypt
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from utils.db import users, leaves, salaries, timetable, init_db, db
from bson.objectid import ObjectId
import os

app = Flask(__name__)
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
from datetime import datetime
from utils.timetable_processor import log_event
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

    return render_template(
        'admin/leave_requests.html',
        leaves=filtered_leaves,
        q=q,
        month=month,
        lecturers=all_lecturers,
    )

@app.route('/admin/leaves/api/set_allocation/<id>', methods=['POST'])
@login_required
@admin_required
def api_set_leave_allocation(id):
    allocated = request.json.get('leaves_per_month', 1)
    try:
        allocated = float(allocated)
    except:
        allocated = 1
    users.update_one({"_id": ObjectId(id)}, {"$set": {"leaves_per_month": allocated}})
    return jsonify({"success": True})


@app.route('/admin/leaves/delete-all', methods=['POST'])
@login_required
@admin_required
def admin_leaves_delete_all():
    result = leaves.delete_many({})
    count = result.deleted_count
    flash(f"Deleted {count} leave record(s).", "success")
    return redirect(url_for('admin_leaves'))


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
        from utils.timetable_processor import extract_from_image, match_and_save
        
        # Single image processing is synchronous for simplicity or we could background it
        data = extract_from_image(img_bytes)
        if "error" in data:
            return redirect(url_for('admin_timetables', error=f"AI Error: {data['error']}"))
        
        match_and_save(data, db, socketio)
        return redirect(url_for('admin_timetables', message=f"Processed image for faculty: {data.get('faculty', 'Unknown')}"))
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

    leaves.update_one(
        {"_id": ObjectId(id)},
        {"$set": {"status": status, "reviewed_at": datetime.now()}},
    )
    
    if leave_doc:
        leaves_left = calculate_leaves_left(leave_doc['lecturer_id'])
        socketio.emit('leave_status_update', {
            'id': id,
            'status': status,
            'lecturer_id': leave_doc['lecturer_id'],
            'leaves_left': leaves_left
        })
        
    flash(f"Leave {status.lower()} successfully!", "success")
    return redirect(request.referrer or url_for('admin_leaves'))

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
    
    leaves_left = calculate_leaves_left(leave_doc['lecturer_id'])
    socketio.emit('leave_status_update', {
        'id': id,
        'status': status,
        'lecturer_id': leave_doc['lecturer_id'],
        'leaves_left': leaves_left
    })
    
    return jsonify({"success": True})

def calculate_leaves_left(lecturer_id):
    user_doc = users.find_one({"_id": ObjectId(lecturer_id)})
    total_leaves = user_doc.get("leaves_per_month", 20) if user_doc else 20
    approved_leaves = list(leaves.find({"lecturer_id": lecturer_id, "status": "Approved"}))
    used_days = 0
    for l in approved_leaves:
        mode = l.get('mode', 'full')
        if mode == 'time':
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
                # Treat message as leave type if it resembles known options
                msg_lower = message.lower()
                known = {
                    "casual": "Casual Leave",
                    "medical": "Medical Leave",
                    "earned": "Earned Leave",
                    "short": "Short Leave",
                }
                for k, v in known.items():
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
                return _chatbot_make_reply(
                    "Choose a leave type.",
                    actions=[
                        {"type": "set_leave_type", "label": "Casual Leave", "value": "Casual Leave"},
                        {"type": "set_leave_type", "label": "Medical Leave", "value": "Medical Leave"},
                        {"type": "set_leave_type", "label": "Earned Leave", "value": "Earned Leave"},
                        {"type": "set_leave_type", "label": "Short Leave", "value": "Short Leave"},
                        {"type": "cancel_flow", "label": "Cancel"},
                    ],
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
        return _chatbot_make_reply(
            "Got the dates. Now choose a leave type.",
            actions=[
                {"type": "set_leave_type", "label": "Casual Leave", "value": "Casual Leave"},
                {"type": "set_leave_type", "label": "Medical Leave", "value": "Medical Leave"},
                {"type": "set_leave_type", "label": "Earned Leave", "value": "Earned Leave"},
                {"type": "set_leave_type", "label": "Short Leave", "value": "Short Leave"},
                {"type": "cancel_flow", "label": "Cancel"},
            ],
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

        leave_data = {
            "lecturer_id": current_user.id,
            "lecturer_name": current_user.name,
            "type": request.form.get('type'),
            "from_date": from_date,
            "to_date": to_date,
            "reason": request.form.get('reason'),
            "status": "Pending",
            "created_at": datetime.now(),
            "mode": leave_mode
        }
        res = leaves.insert_one(leave_data)
        
        socketio.emit('new_leave_request', {
            "id": str(res.inserted_id),
            "lecturer_name": current_user.name,
            "type": request.form.get('type'),
            "from_date": from_date,
            "to_date": to_date,
            "status": "Pending"
        })
        
        flash("Leave application submitted successfully!", "success")
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
    """Show the logged-in lecturer's own timetable image (uploaded by admin)."""
    tt_doc = timetable.find_one({"lecturer_id": current_user.id})
    image_url = None
    if tt_doc and tt_doc.get("image_path"):
        image_path = (tt_doc.get("image_path") or "").replace("\\", "/")
        image_url = url_for("static", filename=image_path)
    return render_template(
        'lecturer/timetable.html',
        has_timetable=image_url is not None,
        timetable_image_url=image_url,
        structured=tt_doc.get("structured") if tt_doc else {},
    )


@app.route('/lecturer/timetable/edit', methods=['GET', 'POST'])
@login_required
@lecturer_required
def edit_lecturer_timetable():
    """
    Simple JSON-editor for the structured timetable extracted by AI.
    This lets a lecturer tweak the parsed slots without changing the image.
    """
    tt_doc = timetable.find_one({"lecturer_id": current_user.id})
    if not tt_doc:
        flash("No timetable found to edit. Please contact administration.", "warning")
        return redirect(url_for('lecturer_timetable'))

    import json as _json

    structured = tt_doc.get("structured") or {}
    structured_text = _json.dumps(structured, indent=2, ensure_ascii=False)

    if request.method == 'POST':
        raw = request.form.get("structured_json", "").strip()
        if not raw:
            flash("Timetable JSON cannot be empty.", "danger")
            return redirect(url_for('edit_lecturer_timetable'))
        try:
            data = _json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("Root must be a JSON object.")
        except Exception as exc:
            flash(f"Invalid JSON: {exc}", "danger")
            return render_template(
                'lecturer/edit_timetable.html',
                structured_json=raw,
            )

        timetable.update_one(
            {"_id": tt_doc["_id"]},
            {"$set": {"structured": data}},
        )

        timetable.update_one(
            {"_id": tt_doc["_id"]},
            {"$set": {"structured": data}},
        )

        # Persistence: Sync back to the original JSON file if possible
        # Filenames are typically "BBHCF048.json" matching the username
        json_filename = f"{current_user.username.upper()}.json"
        json_path = os.path.join("f:\\HRMS\\static\\json_timetables", json_filename)
        
        try:
            with open(json_path, "w", encoding="utf-8") as f_json:
                _json.dump(data, f_json, indent=4, ensure_ascii=False)
        except Exception as json_err:
            print(f"Error saving JSON file at {json_path}: {json_err}")

        flash("Timetable updated.", "success")
        return redirect(url_for('lecturer_timetable'))

    return render_template(
        'lecturer/edit_timetable.html',
        structured_json=structured_text,
    )



if __name__ == '__main__':
    init_db()
    # On some Windows setups (especially with newer Python), the watchdog reloader can throw WinError 10038.
    # Disabling the reloader keeps dev runs stable; restart the server manually after code changes.
    socketio.run(app, debug=True, use_reloader=False)
