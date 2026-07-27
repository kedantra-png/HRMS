import unittest
from utils.security import is_valid_phone

class TestPhoneNumberValidation(unittest.TestCase):
    def test_valid_10_digit_mobile_numbers(self):
        self.assertTrue(is_valid_phone("9876543210"))
        self.assertTrue(is_valid_phone("8123456789"))
        self.assertTrue(is_valid_phone("7012345678"))
        self.assertTrue(is_valid_phone("6301234567"))
        print("\nVerified: Valid 10-digit mobile numbers accepted!")

    def test_invalid_mobile_numbers(self):
        # Over 10 digits (e.g. 4444444444444444444)
        self.assertFalse(is_valid_phone("4444444444444444444"))
        
        # Less than 10 digits
        self.assertFalse(is_valid_phone("987654321"))
        
        # Does not start with valid Indian mobile prefix (6, 7, 8, 9)
        self.assertFalse(is_valid_phone("1234567890"))
        self.assertFalse(is_valid_phone("0987654321"))
        
        # Non-numeric / letters
        self.assertFalse(is_valid_phone("phone_number"))
        self.assertFalse(is_valid_phone(""))
        print("Verified: Invalid length (>10 or <10) and bad prefix phone numbers rejected!")

if __name__ == "__main__":
    unittest.main()
