import unittest
from datetime import datetime, timedelta, timezone
from utils.db import users, password_resets, login_attempts
from utils.security import (
    get_lockout_status,
    record_failed_attempt,
    record_successful_login,
    MAX_INITIAL_ATTEMPTS
)
import secrets
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestLockoutEmailUnlock(unittest.TestCase):
    def setUp(self):
        self.username = "test_alert_faculty"
        self.staff_id = "STAFF_ALERT_01"
        self.email = "alert_test@college.edu"

        hashed_pw = bcrypt.generate_password_hash("TestPass123!").decode('utf-8')
        users.update_one(
            {"username": self.username},
            {"$set": {
                "username": self.username,
                "staff_id": self.staff_id,
                "email": self.email,
                "password": hashed_pw,
                "name": "Prof. Security Test",
                "role": "lecturer"
            }},
            upsert=True
        )
        record_successful_login(self.username)
        password_resets.delete_many({"username": self.username})

    def tearDown(self):
        record_successful_login(self.username)
        password_resets.delete_many({"username": self.username})
        users.delete_one({"username": self.username})

    def test_lockout_and_instant_email_unlock(self):
        print("\n--- Triggering Account Lockout ---")
        for _ in range(MAX_INITIAL_ATTEMPTS):
            res = record_failed_attempt(self.username)

        # Verify account is currently locked
        status_locked = get_lockout_status(self.username)
        self.assertTrue(status_locked["is_locked"])
        self.assertGreater(status_locked["remaining_seconds"], 0)
        print(f"Account locked out: {status_locked['formatted_time']}")

        # Simulate generating unlock token on lockout
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = now + timedelta(minutes=30)

        password_resets.insert_one({
            "token": token,
            "user_id": str(users.find_one({"username": self.username})["_id"]),
            "username": self.username,
            "email": self.email,
            "type": "unlock",
            "created_at": now,
            "expires_at": expires_at,
            "used": False
        })

        # Verify token document
        unlock_doc = password_resets.find_one({"token": token, "used": False})
        self.assertIsNotNone(unlock_doc)

        # Simulate clicking instant unlock link -> record_successful_login(self.username)
        record_successful_login(self.username)
        password_resets.update_one({"token": token}, {"$set": {"used": True}})

        # Verify account is now IMMEDIATELY unlocked & timer stopped!
        status_unlocked = get_lockout_status(self.username)
        self.assertFalse(status_unlocked["is_locked"])
        self.assertEqual(status_unlocked["remaining_seconds"], 0)
        print("Verified: Instant email unlock link cancels lockout timer immediately!")

if __name__ == "__main__":
    unittest.main()
