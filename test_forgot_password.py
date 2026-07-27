import unittest
from datetime import datetime, timedelta, timezone
from utils.db import users, password_resets
from utils.security import record_successful_login, record_failed_attempt, get_lockout_status
import secrets
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestForgotPasswordFlow(unittest.TestCase):
    def setUp(self):
        self.username = "reset_test_faculty"
        self.staff_id = "STAFF_RESET_99"
        self.email = "testfaculty@college.edu"
        self.initial_pass = "OldPass123"

        hashed_pw = bcrypt.generate_password_hash(self.initial_pass).decode('utf-8')
        users.update_one(
            {"username": self.username},
            {"$set": {
                "username": self.username,
                "staff_id": self.staff_id,
                "email": self.email,
                "password": hashed_pw,
                "name": "Dr. Reset Test",
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

    def test_token_generation_and_password_reset(self):
        # 1. Lookup user
        user_data = users.find_one({"username": self.username})
        self.assertIsNotNone(user_data)
        self.assertEqual(user_data["email"], self.email)

        # 2. Simulate /forgot-password token creation
        token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        expires_at = now + timedelta(minutes=30)

        password_resets.insert_one({
            "token": token,
            "user_id": str(user_data["_id"]),
            "username": self.username,
            "email": self.email,
            "created_at": now,
            "expires_at": expires_at,
            "used": False
        })

        # 3. Verify token lookup
        reset_doc = password_resets.find_one({"token": token, "used": False})
        self.assertIsNotNone(reset_doc)
        self.assertFalse(reset_doc["used"])

        # 4. Perform password reset
        new_pass = "BrandNewSecret456!"
        hashed_new_pw = bcrypt.generate_password_hash(new_pass).decode('utf-8')

        # Update DB & mark token used
        users.update_one({"_id": user_data["_id"]}, {"$set": {"password": hashed_new_pw}})
        password_resets.update_one({"token": token}, {"$set": {"used": True}})

        # 5. Verify updated user password
        updated_user = users.find_one({"_id": user_data["_id"]})
        self.assertTrue(bcrypt.check_password_hash(updated_user["password"], new_pass))
        self.assertFalse(bcrypt.check_password_hash(updated_user["password"], self.initial_pass))
        print("Verified: Password successfully updated in DB!")

        # 6. Verify token cannot be reused
        used_doc = password_resets.find_one({"token": token, "used": False})
        self.assertIsNone(used_doc)
        print("Verified: Used token is rejected for reuse!")

if __name__ == "__main__":
    unittest.main()
