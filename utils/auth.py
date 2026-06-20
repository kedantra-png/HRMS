import os
import secrets
from functools import wraps
from flask import redirect, url_for, flash, session, request, jsonify
from flask_login import current_user


def salary_access_password() -> str:
    return (os.getenv("SALARY_ACCESS_PASSWORD") or "chethan").strip()


def is_salary_unlocked() -> bool:
    return bool(session.get("salary_unlocked"))


def verify_salary_password(password: str) -> bool:
    return secrets.compare_digest((password or ""), salary_access_password())

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'admin':
            flash("You do not have permission to access این page.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def lecturer_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.role != 'lecturer':
            flash("Lecturer access required.", "danger")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function


def salary_access_required(f):
    """Extra password gate for payroll / Manage Salary (after admin login)."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_salary_unlocked():
            session["salary_unlock_next"] = request.full_path or request.path
            if request.headers.get("X-Requested-With") == "XMLHttpRequest":
                return jsonify({"ok": False, "message": "Salary access locked"}), 403
            flash("Enter the salary access password to continue.", "info")
            return redirect(url_for("admin_salary_unlock"))
        return f(*args, **kwargs)
    return decorated_function
