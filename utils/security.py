from datetime import datetime, timedelta, timezone
import math
import re
from utils.db import login_attempts

SPECIAL_CHAR_PATTERN = r'[!@#$%^&*()_+\-=\[\]{};\':"\\|,.<>\/?]'

def validate_password_policy(password: str) -> tuple[bool, str]:
    """
    Validate password against security policy criteria:
    - Minimum length: 8 characters
    - Maximum length: 16 characters
    - At least 1 uppercase letter (A-Z)
    - At least 1 lowercase letter (a-z)
    - At least 1 number (0-9)
    - At least 1 special character (! @ # $ % ^ & * ( ) _ + - =)
    - No spaces allowed
    Returns (True, "") or (False, "Failure reason").
    """
    if not password:
        return False, "Password is required."
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if len(password) > 16:
        return False, "Password cannot exceed 16 characters."
    if " " in password:
        return False, "Password cannot contain spaces."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least 1 uppercase letter (A-Z)."
    if not re.search(r"[a-z]", password):
        return False, "Password must contain at least 1 lowercase letter (a-z)."
    if not re.search(r"[0-9]", password):
        return False, "Password must contain at least 1 number (0-9)."
    if not re.search(SPECIAL_CHAR_PATTERN, password):
        return False, "Password must contain at least 1 special character (! @ # $ % ^ & * ( ) _ + - =)."
    return True, ""

def is_strong_password(password: str) -> bool:
    """Check if password meets policy criteria."""
    valid, _ = validate_password_policy(password)
    return valid

def is_valid_phone(phone: str) -> bool:
    """Validate 10-digit mobile phone number (starting with 6, 7, 8, 9)."""
    p = (phone or "").strip()
    return bool(re.match(r"^[6-9]\d{9}$", p))

MAX_INITIAL_ATTEMPTS = 7      # First stage allows 7 attempts before 1-min lockout
MAX_SUBSEQUENT_ATTEMPTS = 2    # After lockout, user gets 2 attempts before next tier lockout

# Lockout stage durations in seconds:
# Stage 1: 1 min (60s)
# Stage 2: 5 mins (300s)
# Stage 3: 15 mins (900s)
# Stage 4: 30 mins (1800s)
# Stage 5: 1 hr (3600s)
# Stage 6: 2 hrs (7200s)
# Stage 7: 4 hrs (14400s)
# Stage 8: 8 hrs (28800s)
# Stage 9: 12 hrs (43200s)
# Stage 10+: 24 hrs (86400s)
LOCKOUT_DURATIONS = [
    60,      # Tier 1: 1 min
    300,     # Tier 2: 5 mins
    900,     # Tier 3: 15 mins
    1800,    # Tier 4: 30 mins
    3600,    # Tier 5: 1 hour
    7200,    # Tier 6: 2 hours
    14400,   # Tier 7: 4 hours
    28800,   # Tier 8: 8 hours
    43200,   # Tier 9: 12 hours
    86400    # Tier 10+: 24 hours
]

def _get_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)

def format_duration(seconds: int) -> str:
    """Format seconds into human readable duration string."""
    if seconds <= 0:
        return "0 seconds"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60
    
    parts = []
    if hours > 0:
        parts.append(f"{hours} hour{'s' if hours > 1 else ''}")
    if minutes > 0:
        parts.append(f"{minutes} minute{'s' if minutes > 1 else ''}")
    if secs > 0 and hours == 0:
        parts.append(f"{secs} second{'s' if secs > 1 else ''}")
    return " ".join(parts) if parts else f"{seconds} seconds"

def _normalize_username(username: str) -> str:
    return (username or "").strip().lower()

def get_lockout_status(username: str) -> dict:
    """
    Check if the given username is currently locked out.
    Returns dict:
      {
        "is_locked": bool,
        "remaining_seconds": int,
        "lockout_stage": int,
        "remaining_attempts": int,
        "formatted_time": str
      }
    """
    norm_user = _normalize_username(username)
    if not norm_user:
        return {
            "is_locked": False,
            "remaining_seconds": 0,
            "lockout_stage": 0,
            "remaining_attempts": MAX_INITIAL_ATTEMPTS,
            "formatted_time": ""
        }
        
    doc = login_attempts.find_one({"username": norm_user})
    if not doc:
        return {
            "is_locked": False,
            "remaining_seconds": 0,
            "lockout_stage": 0,
            "remaining_attempts": MAX_INITIAL_ATTEMPTS,
            "formatted_time": ""
        }
        
    now = _get_utc_now()
    lockout_until = doc.get("lockout_until")
    lockout_stage = doc.get("lockout_stage", 0)
    failed_attempts = doc.get("failed_attempts", 0)
    
    if lockout_until and now < lockout_until:
        rem_sec = math.ceil((lockout_until - now).total_seconds())
        return {
            "is_locked": True,
            "remaining_seconds": rem_sec,
            "lockout_stage": lockout_stage,
            "remaining_attempts": 0,
            "formatted_time": format_duration(rem_sec)
        }
    
    # Lockout has expired or not locked
    allowed = MAX_INITIAL_ATTEMPTS if lockout_stage == 0 else MAX_SUBSEQUENT_ATTEMPTS
    remaining_attempts = max(0, allowed - failed_attempts)
    
    return {
        "is_locked": False,
        "remaining_seconds": 0,
        "lockout_stage": lockout_stage,
        "remaining_attempts": remaining_attempts,
        "formatted_time": ""
    }

def record_failed_attempt(username: str) -> dict:
    """
    Record a failed password attempt for username.
    Escalates lockout stage if attempt threshold is reached.
    Returns dict:
      {
        "is_locked": bool,
        "remaining_seconds": int,
        "lockout_stage": int,
        "remaining_attempts": int,
        "formatted_time": str,
        "just_locked": bool
      }
    """
    norm_user = _normalize_username(username)
    if not norm_user:
        return {
            "is_locked": False,
            "remaining_seconds": 0,
            "lockout_stage": 0,
            "remaining_attempts": MAX_INITIAL_ATTEMPTS,
            "formatted_time": "",
            "just_locked": False
        }
        
    now = _get_utc_now()
    doc = login_attempts.find_one({"username": norm_user})
    
    if not doc:
        doc = {
            "username": norm_user,
            "failed_attempts": 0,
            "lockout_stage": 0,
            "lockout_until": None,
            "last_attempt_at": now
        }
        
    lockout_until = doc.get("lockout_until")
    lockout_stage = doc.get("lockout_stage", 0)
    failed_attempts = doc.get("failed_attempts", 0)
    
    # If currently locked out, do not increment attempt, just return current lockout status
    if lockout_until and now < lockout_until:
        rem_sec = math.ceil((lockout_until - now).total_seconds())
        return {
            "is_locked": True,
            "remaining_seconds": rem_sec,
            "lockout_stage": lockout_stage,
            "remaining_attempts": 0,
            "formatted_time": format_duration(rem_sec),
            "just_locked": False
        }
        
    # If lockout expired, reset failed_attempts for current stage
    if lockout_until and now >= lockout_until:
        failed_attempts = 0
        
    failed_attempts += 1
    allowed = MAX_INITIAL_ATTEMPTS if lockout_stage == 0 else MAX_SUBSEQUENT_ATTEMPTS
    
    if failed_attempts >= allowed:
        # Escalate lockout stage
        new_stage = lockout_stage + 1
        duration_idx = min(new_stage - 1, len(LOCKOUT_DURATIONS) - 1)
        duration_sec = LOCKOUT_DURATIONS[duration_idx]
        new_lockout_until = now + timedelta(seconds=duration_sec)
        
        login_attempts.update_one(
            {"username": norm_user},
            {
                "$set": {
                    "username": norm_user,
                    "failed_attempts": 0,
                    "lockout_stage": new_stage,
                    "lockout_until": new_lockout_until,
                    "last_attempt_at": now
                }
            },
            upsert=True
        )
        
        return {
            "is_locked": True,
            "remaining_seconds": duration_sec,
            "lockout_stage": new_stage,
            "remaining_attempts": 0,
            "formatted_time": format_duration(duration_sec),
            "just_locked": True
        }
    else:
        # Record incremented failed attempt
        login_attempts.update_one(
            {"username": norm_user},
            {
                "$set": {
                    "username": norm_user,
                    "failed_attempts": failed_attempts,
                    "lockout_stage": lockout_stage,
                    "lockout_until": None,
                    "last_attempt_at": now
                }
            },
            upsert=True
        )
        
        remaining = allowed - failed_attempts
        return {
            "is_locked": False,
            "remaining_seconds": 0,
            "lockout_stage": lockout_stage,
            "remaining_attempts": remaining,
            "formatted_time": "",
            "just_locked": False
        }

def record_successful_login(username: str):
    """Reset security attempt metrics on successful login or email unlock."""
    norm_user = _normalize_username(username)
    if norm_user:
        login_attempts.delete_many({
            "$or": [
                {"username": norm_user},
                {"username": (username or "").strip()},
                {"username": (username or "").strip().upper()}
            ]
        })
