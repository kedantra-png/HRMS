import unittest
from utils.security import validate_password_policy, is_strong_password

class TestPasswordPolicy(unittest.TestCase):
    def test_policy_criteria_validation(self):
        # 1. Min 8, Max 16 length
        self.assertFalse(validate_password_policy("P1!a")[0])  # < 8 chars
        self.assertFalse(validate_password_policy("VeryLongPasswordThatExceeds16Chars123!")[0])  # > 16 chars

        # 2. Uppercase letter required
        self.assertFalse(validate_password_policy("password123!")[0])

        # 3. Lowercase letter required
        self.assertFalse(validate_password_policy("PASSWORD123!")[0])

        # 4. Number required
        self.assertFalse(validate_password_policy("PasswordWord!")[0])

        # 5. Special character required
        self.assertFalse(validate_password_policy("Password123")[0])

        # 6. No spaces allowed
        self.assertFalse(validate_password_policy("Pass 1234!")[0])

        # 7. Valid compliant password
        valid, msg = validate_password_policy("Pass1234!")
        self.assertTrue(valid)
        self.assertEqual(msg, "")
        print("Verified: Password policy validation rules pass 100%!")

    def test_default_passwords_flagged_as_weak(self):
        self.assertFalse(is_strong_password("admin123"))
        self.assertFalse(is_strong_password("lect123"))
        self.assertFalse(is_strong_password("test123"))
        self.assertTrue(is_strong_password("Hrms#2026!"))
        print("Verified: Default passwords (admin123/lect123) accurately identified as weak!")

if __name__ == "__main__":
    unittest.main()
