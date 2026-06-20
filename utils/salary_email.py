"""SMTP email helpers for salary slips."""
import os
import re
import smtplib
import ssl
from datetime import datetime
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# Gmail: port 587 (STARTTLS) is often blocked on college networks; 465 (SSL) usually works.
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 465
DEFAULT_SMTP_USE_TLS = False  # False => SMTP_SSL on port 465
DEFAULT_COLLEGE_NAME = "Dr. B. B. Hegde First Grade College"
SMTP_CONNECT_TIMEOUT = 45


def is_valid_email(email: str) -> bool:
    email = (email or "").strip()
    if not email:
        return False
    lowered = email.lower()
    if lowered in ("no-email", "n/a", "na", "-", "none"):
        return False
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def _normalize_smtp_password(raw: str) -> str:
    raw = (raw or "").strip().strip('"').strip("'")
    if " " in raw:
        raw = raw.replace(" ", "")
    return raw


def _smtp_password_from_env() -> str:
    return _normalize_smtp_password(os.getenv("SMTP_PASSWORD") or "")


def _payroll_smtp_from_db() -> dict:
    try:
        from utils.db import system_settings
        return system_settings.find_one({"_id": "payroll_smtp"}) or {}
    except Exception:
        return {}


def get_payroll_smtp_for_admin() -> dict:
    """Return settings for admin form (password masked if stored)."""
    doc = _payroll_smtp_from_db()
    env_user = (os.getenv("SMTP_USER") or "").strip()
    user = (doc.get("smtp_user") or env_user).strip()
    has_db_password = bool(doc.get("smtp_password"))
    has_env_password = bool(_smtp_password_from_env())
    has_password = has_db_password or has_env_password
    if doc.get("smtp_user") or has_db_password:
        source = "ui"
    elif env_user or has_env_password:
        source = "env"
    else:
        source = "none"
    active = _smtp_settings() if smtp_configured() else {}
    return {
        "smtp_user": user,
        "has_password": has_password,
        "password_saved_in_ui": has_db_password,
        "source": source,
        "smtp_host": active.get("host") or DEFAULT_SMTP_HOST,
        "smtp_port": active.get("port") or DEFAULT_SMTP_PORT,
    }


def resolve_smtp_credentials(smtp_user: str | None = None, smtp_password: str | None = None) -> dict:
    """Merge form values with saved/.env settings for test or send."""
    base = _smtp_settings()
    user = (smtp_user or base.get("user") or "").strip()
    if smtp_password and str(smtp_password).strip():
        password = _normalize_smtp_password(smtp_password)
    else:
        password = base.get("password") or ""
    return {**base, "user": user, "password": password}


def test_smtp_login(smtp_user: str | None = None, smtp_password: str | None = None) -> dict:
    """Connect and login only (no email sent). Returns {ok, message}."""
    s = resolve_smtp_credentials(smtp_user, smtp_password)
    user, password = s.get("user"), s.get("password")
    if not user:
        return {"ok": False, "message": "Enter the sender Gmail address."}
    if not password:
        return {
            "ok": False,
            "message": "Enter the Gmail App Password (16 characters), or save it using Save sender login.",
        }

    profiles = _smtp_connect_profiles(s)
    last_error = None
    for profile in profiles:
        h, p, tls = profile["host"], profile["port"], profile["use_tls"]
        mode = f"{h}:{p} ({'STARTTLS' if tls else 'SSL'})"
        try:
            server = _open_smtp_server(h, p, tls)
            server.login(user, password)
            server.quit()
            return {
                "ok": True,
                "message": f"Login successful for {user} via {mode}. You can send salary slips.",
            }
        except smtplib.SMTPAuthenticationError:
            return {
                "ok": False,
                "message": (
                    "Gmail rejected the password. Use a Google App Password (not your normal Gmail password). "
                    "Create one at https://myaccount.google.com/apppasswords (2-Step Verification required)."
                ),
            }
        except OSError as e:
            last_error = f"{mode}: {e}"
            continue
        except smtplib.SMTPException as e:
            last_error = f"{mode}: {e}"
            continue
        except Exception as e:
            return {"ok": False, "message": f"Unexpected error: {e}"}

    return {
        "ok": False,
        "message": f"Could not connect to Gmail. Last error: {last_error or 'unknown'}",
    }


def clear_payroll_smtp() -> None:
    """Remove UI-saved sender login so .env SMTP_USER / SMTP_PASSWORD are used."""
    from utils.db import system_settings

    system_settings.delete_one({"_id": "payroll_smtp"})


def _looks_like_gmail_app_password(password: str) -> bool:
    """Google App Passwords are 16 letters (spaces optional)."""
    return len(_normalize_smtp_password(password)) == 16


def save_payroll_smtp(smtp_user: str, smtp_password: str | None = None) -> None:
    """Persist payroll SMTP login in system_settings (password optional — keep existing if blank)."""
    from utils.db import system_settings

    smtp_user = (smtp_user or "").strip()
    payload = {
        "smtp_user": smtp_user,
        "updated_at": datetime.now(),
    }
    if smtp_password and str(smtp_password).strip():
        payload["smtp_password"] = _normalize_smtp_password(smtp_password)

    system_settings.update_one(
        {"_id": "payroll_smtp"},
        {"$set": payload},
        upsert=True,
    )


def _env_use_tls() -> bool:
    raw = os.getenv("SMTP_USE_TLS")
    if raw is None:
        return DEFAULT_SMTP_USE_TLS
    return raw.strip().lower() not in ("0", "false", "no")


def _smtp_settings() -> dict:
    doc = _payroll_smtp_from_db()
    user = (doc.get("smtp_user") or os.getenv("SMTP_USER") or "").strip()
    password = _normalize_smtp_password(doc.get("smtp_password") or "") or _smtp_password_from_env()
    host = (doc.get("smtp_host") or os.getenv("SMTP_HOST") or DEFAULT_SMTP_HOST).strip()
    port_raw = doc.get("smtp_port") or os.getenv("SMTP_PORT") or str(DEFAULT_SMTP_PORT)
    port = int(str(port_raw).strip() or DEFAULT_SMTP_PORT)
    use_tls = doc.get("smtp_use_tls")
    if use_tls is None:
        use_tls = _env_use_tls()
    else:
        use_tls = bool(use_tls)
    from_addr = (os.getenv("SMTP_FROM") or "").strip()
    if not from_addr and user:
        from_addr = f"HRMS Payroll <{user}>"
    college = (os.getenv("SMTP_COLLEGE_NAME") or DEFAULT_COLLEGE_NAME).strip()
    return {
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "use_tls": use_tls,
        "from_addr": from_addr,
        "college": college,
    }


def _smtp_connect_profiles(settings: dict) -> list[dict]:
    """Build connection attempts (primary + Gmail fallbacks when 587/465 blocked)."""
    host = (settings.get("host") or DEFAULT_SMTP_HOST).lower()
    primary = {
        "host": settings["host"],
        "port": settings["port"],
        "use_tls": settings["use_tls"],
    }
    profiles = [primary]
    if "gmail.com" not in host:
        return profiles

    alt_465 = {"host": settings["host"], "port": 465, "use_tls": False}
    alt_587 = {"host": settings["host"], "port": 587, "use_tls": True}
    for alt in (alt_465, alt_587):
        if alt not in profiles and not any(
            p["port"] == alt["port"] and p["use_tls"] == alt["use_tls"] for p in profiles
        ):
            profiles.append(alt)
    return profiles


def _open_smtp_server(host: str, port: int, use_tls: bool):
    ctx = ssl.create_default_context()
    if use_tls:
        server = smtplib.SMTP(host, port, timeout=SMTP_CONNECT_TIMEOUT)
        server.ehlo()
        server.starttls(context=ctx)
        server.ehlo()
        return server
    return smtplib.SMTP_SSL(host, port, timeout=SMTP_CONNECT_TIMEOUT, context=ctx)


def smtp_configured() -> bool:
    s = _smtp_settings()
    return bool(s["user"] and s["password"])


def smtp_status_message() -> str:
    if smtp_configured():
        s = _smtp_settings()
        return f"Email ready — {s['user']} via {s['host']}"
    return "Email off — set payroll email login on Manage Salary (or .env SMTP_USER / SMTP_PASSWORD)"


def send_salary_slip_email(
    to_email: str,
    staff_name: str,
    staff_id: str,
    month_year: str,
    pdf_bytes: bytes,
    filename: str,
) -> dict:
    """
    Send salary slip PDF. Returns {'ok': True} or {'ok': False, 'reason': '...'}.
    """
    if not smtp_configured():
        return {
            "ok": False,
            "reason": "Email not configured. Set payroll email login on Manage Salary.",
        }

    s = _smtp_settings()
    host, port = s["host"], s["port"]
    user, password = s["user"], s["password"]
    use_tls, from_addr, college = s["use_tls"], s["from_addr"], s["college"]

    subject = f"Salary Slip — {month_year} — {staff_id}"
    body = f"""Dear {staff_name or 'Faculty'},

Please find attached your salary slip for {month_year}.

Staff ID: {staff_id}

This is a computer-generated email from {college} HRMS.
Do not reply to this email.

Regards,
HR / Payroll Office
"""

    msg = MIMEMultipart()
    msg["From"] = from_addr
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    attachment = MIMEApplication(pdf_bytes, _subtype="pdf")
    attachment.add_header("Content-Disposition", "attachment", filename=filename)
    msg.attach(attachment)

    profiles = _smtp_connect_profiles(s)
    last_os_error = None
    last_smtp_error = None

    for profile in profiles:
        h, p, tls = profile["host"], profile["port"], profile["use_tls"]
        mode = f"{h}:{p} ({'STARTTLS' if tls else 'SSL'})"
        try:
            server = _open_smtp_server(h, p, tls)
            server.login(user, password)
            server.sendmail(from_addr, [to_email], msg.as_string())
            server.quit()
            return {"ok": True}
        except smtplib.SMTPAuthenticationError:
            return {
                "ok": False,
                "reason": (
                    "Gmail login failed — use a Google App Password (not your normal password). "
                    "Enable 2-Step Verification, then create an app password at "
                    "https://myaccount.google.com/apppasswords"
                ),
            }
        except smtplib.SMTPRecipientsRefused:
            return {"ok": False, "reason": f"Email rejected by server for address: {to_email}"}
        except smtplib.SMTPException as e:
            last_smtp_error = f"{mode}: {e}"
            continue
        except OSError as e:
            last_os_error = f"{mode}: {e}"
            continue
        except Exception as e:
            return {"ok": False, "reason": f"Unexpected error ({mode}): {e}"}

    if last_os_error:
        return {
            "ok": False,
            "reason": (
                f"Could not connect to mail server. Tried: {', '.join(f'{p['host']}:{p['port']}' for p in profiles)}. "
                f"Last error: {last_os_error}. "
                "If you are on college Wi‑Fi, set SMTP_PORT=465 and SMTP_USE_TLS=false in .env (port 587 is often blocked)."
            ),
        }
    if last_smtp_error:
        return {"ok": False, "reason": f"Mail server error: {last_smtp_error}"}
    return {"ok": False, "reason": "Could not send email — no SMTP connection method succeeded."}
