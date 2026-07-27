import unittest
from utils.db import users
from utils.security import record_successful_login, get_lockout_status
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestStrictPasswordMatch(unittest.TestCase):
    def setUp(self):
        self.username = "bbhcf017"
        record_successful_login(self.username)

    def test_wrong_password_rejected(self):
        user_doc = users.find_one({"username": self.username})
        self.assertIsNotNone(user_doc)

        # Attempt with incorrect password 'bbhcf017'
        wrong_pass = "bbhcf017"
        is_match = bcrypt.check_password_hash(user_doc["password"], wrong_pass)
        
        # Verify sign-in is REJECTED
        self.assertFalse(is_match, "Wrong password must be REJECTED!")
        print("\nVerified: Wrong password 'bbhcf017' was strictly REJECTED!")

    def test_correct_password_accepted(self):
        user_doc = users.find_one({"username": self.username})
        self.assertIsNotNone(user_doc)

        # Attempt with correct password 'bbhcf017123'
        correct_pass = "bbhcf017123"
        is_match = bcrypt.check_password_hash(user_doc["password"], correct_pass)
        
        # Verify sign-in is ACCEPTED
        self.assertTrue(is_match, "Correct password must be ACCEPTED!")
        print("Verified: Correct password 'bbhcf017123' matched 100% and ALLOWED sign-in!")

if __name__ == "__main__":
    unittest.main()
