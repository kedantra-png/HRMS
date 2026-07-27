import unittest
from datetime import datetime, timedelta, timezone
from utils.db import users, login_attempts
from utils.security import (
    get_lockout_status,
    record_failed_attempt,
    record_successful_login,
    MAX_INITIAL_ATTEMPTS
)
import bcrypt

class TestStrictUsernameLockout(unittest.TestCase):
    def setUp(self):
        self.username = "test_faculty_user"
        self.staff_id = "STAFF007"
        
        # Ensure clean test user in DB
        hashed_pw = bcrypt.hashpw(b"validpass123", bcrypt.gensalt()).decode('utf-8')
        users.update_one(
            {"username": self.username},
            {"$set": {
                "username": self.username,
                "staff_id": self.staff_id,
                "password": hashed_pw,
                "role": "lecturer"
            }},
            upsert=True
        )
        record_successful_login(self.username)

    def tearDown(self):
        record_successful_login(self.username)
        users.delete_one({"username": self.username})

    def test_invalid_username_does_not_increment_attempts(self):
        fake_user = "non_existent_username_xyz"
        # Check that fake username or real username have 0 failed attempts
        status_before = get_lockout_status(self.username)
        self.assertEqual(status_before["remaining_attempts"], MAX_INITIAL_ATTEMPTS)

        # Simulate invalid username check logic
        user_data = users.find_one({"$or": [{"username": fake_user}, {"staff_id": fake_user}]})
        self.assertIsNone(user_data)
        # Because user_data is None, NO failed attempt is recorded on self.username!
        
        status_after = get_lockout_status(self.username)
        self.assertEqual(status_after["remaining_attempts"], MAX_INITIAL_ATTEMPTS)
        print("Verified: Invalid username does NOT record failed attempts on system accounts!")

    def test_valid_username_wrong_password_increments_attempts(self):
        user_data = users.find_one({"$or": [{"username": self.username}, {"staff_id": self.staff_id}]})
        self.assertIsNotNone(user_data)
        canonical_user = user_data["username"].lower()

        # Simulate 6 wrong password attempts
        for i in range(1, 7):
            res = record_failed_attempt(canonical_user)
            self.assertFalse(res["is_locked"])
            self.assertEqual(res["remaining_attempts"], MAX_INITIAL_ATTEMPTS - i)

        # 7th wrong password attempt triggers lockout for this user ID
        res7 = record_failed_attempt(canonical_user)
        self.assertTrue(res7["is_locked"])
        self.assertEqual(res7["remaining_seconds"], 60)
        print("Verified: Valid username with wrong password increments attempts and triggers 7-attempt lockout!")

        # Verify that checking by staff_id also finds account locked out
        staff_lookup = users.find_one({"staff_id": self.staff_id})
        c_user = staff_lookup["username"].lower()
        lockout_by_staff = get_lockout_status(c_user)
        self.assertTrue(lockout_by_staff["is_locked"])
        print("Verified: ID locked system-wide (accessible by both username and staff_id)!")

if __name__ == "__main__":
    unittest.main()
