import unittest
from utils.db import users
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestBcryptPasswordHashes(unittest.TestCase):
    def test_passwords_are_bcrypt_hashed(self):
        sample_users = list(users.find({}, {"username": 1, "password": 1, "role": 1}).limit(5))
        self.assertGreater(len(sample_users), 0, "No users found in database!")

        print("\n--- Verifying MongoDB Password Hashes ---")
        for u in sample_users:
            pw_hash = u.get("password", "")
            username = u.get("username", "unknown")
            
            # Check bcrypt hash format: starts with $2a$, $2b$, or $2y$
            is_bcrypt = pw_hash.startswith(("$2a$", "$2b$", "$2y$"))
            self.assertTrue(is_bcrypt, f"User '{username}' password is NOT hashed with bcrypt: {pw_hash}")
            
            print(f"[OK] User '{username}' ({u.get('role')}):")
            print(f"     Stored in MongoDB: {pw_hash[:30]}... (Total length: {len(pw_hash)} chars)")

if __name__ == "__main__":
    unittest.main()
