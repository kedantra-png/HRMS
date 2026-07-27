import unittest
from utils.salary_email import is_valid_email, send_test_warning_email, smtp_configured

class TestWarningEmailDispatch(unittest.TestCase):
    def test_email_validation(self):
        self.assertTrue(is_valid_email("admin@college.edu"))
        self.assertFalse(is_valid_email("invalid_email"))
        self.assertFalse(is_valid_email(""))

    def test_warning_email_function_contract(self):
        # Invalid email recipient test
        res = send_test_warning_email("invalid-address")
        self.assertFalse(res["ok"])
        self.assertIn("Invalid recipient email address", res["reason"])
        print("Verified: Invalid email address properly rejected!")

        # Function return structure test
        if not smtp_configured():
            res_no_smtp = send_test_warning_email("admin@college.edu")
            self.assertFalse(res_no_smtp["ok"])
            self.assertIn("SMTP", res_no_smtp["reason"])
            print("Verified: Unconfigured SMTP returns clear warning message.")
        else:
            print("Verified: SMTP configured and ready for test email dispatch.")

if __name__ == "__main__":
    unittest.main()
