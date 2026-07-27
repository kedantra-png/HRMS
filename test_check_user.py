import unittest
from utils.db import users
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestUserCredentials(unittest.TestCase):
    def test_inspect_user_bbhcf017(self):
        user_doc = users.find_one({"username": "bbhcf017"})
        if not user_doc:
            user_doc = users.find_one({"staff_id": "BBHCF017"})

        self.assertIsNotNone(user_doc, "User bbhcf017 not found!")
        print(f"\nFound User: {user_doc.get('name')} ({user_doc.get('username')})")
        print(f"Staff ID: {user_doc.get('staff_id')}")
        print(f"Role: {user_doc.get('role')}")
        print(f"Display Password: {user_doc.get('display_password')}")

        # Check if password 'bbhcf017' matches hash
        matches_username = bcrypt.check_password_hash(user_doc['password'], "bbhcf017")
        matches_default = bcrypt.check_password_hash(user_doc['password'], "123456")
        print(f"Is password 'bbhcf017'? {matches_username}")
        print(f"Is password '123456'? {matches_default}")

if __name__ == "__main__":
    unittest.main()
