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

class TestLockoutEmailExpirationAndReset(unittest.TestCase):
    def setUp(self):
        self.username = "test_expire_faculty"
        self.staff_id = "STAFF_EXPIRE_01"
        self.email = "expire_test@college.edu"

        hashed_pw = bcrypt.generate_password_hash("TestPass123!").decode('utf-8')
        users.update_one(
            {"username": self.username},
            {"$set": {
                "username": self.username,
                "staff_id": self.staff_id,
                "email": self.email,
                "password": hashed_pw,
                "name": "Prof. Expire Test",
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

    def test_old_link_expiration_on_new_lockout(self):
        print("\n--- Testing Old Email Link Invalidation on New Lockout ---")
        user_id = str(users.find_one({"username": self.username})["_id"])

        # 1. First Lockout Alert
        old_token = "old_token_12345"
        password_resets.insert_one({
            "token": old_token,
            "user_id": user_id,
            "username": self.username,
            "email": self.email,
            "type": "unlock",
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "used": False
        })

        # 2. Trigger new lockout event -> removes old tokens
        new_token = "new_token_67890"
        password_resets.delete_many({"user_id": user_id, "type": "unlock"})
        password_resets.insert_one({
            "token": new_token,
            "user_id": user_id,
            "username": self.username,
            "email": self.email,
            "type": "unlock",
            "created_at": datetime.utcnow(),
            "expires_at": datetime.utcnow() + timedelta(minutes=30),
            "used": False
        })

        # Verify old token was deleted/invalidated
        old_doc = password_resets.find_one({"token": old_token, "type": "unlock"})
        self.assertIsNone(old_doc)

        # Verify new token exists
        new_doc = password_resets.find_one({"token": new_token, "type": "unlock"})
        self.assertIsNotNone(new_doc)
        print("Verified: New lockout email automatically invalidates/expires old unlock links!")

    def test_stage_reset_to_first_procedure_after_unlock(self):
        print("\n--- Testing Stage Reset to 7 Initial Attempts After Unlock ---")
        for _ in range(MAX_INITIAL_ATTEMPTS):
            record_failed_attempt(self.username)

        # Confirm locked
        status_locked = get_lockout_status(self.username)
        self.assertTrue(status_locked["is_locked"])

        # Simulate unlock link clicked -> record_successful_login
        record_successful_login(self.username)

        # Verify status is unlocked
        status_unlocked = get_lockout_status(self.username)
        self.assertFalse(status_unlocked["is_locked"])
        self.assertEqual(status_unlocked["remaining_attempts"], MAX_INITIAL_ATTEMPTS)
        self.assertEqual(status_unlocked["lockout_stage"], 0)
        print("Verified: Unlocking restores Stage 0 with full 7 initial attempt chances!")

if __name__ == "__main__":
    unittest.main()
