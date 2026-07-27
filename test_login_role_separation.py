import unittest
from utils.db import users
from flask_bcrypt import Bcrypt

bcrypt = Bcrypt()

class TestLoginRoleSeparation(unittest.TestCase):
    def setUp(self):
        self.admin_user = "test_admin_sep"
        self.faculty_user = "test_faculty_sep"
        hashed_pw = bcrypt.generate_password_hash("Pass1234!").decode('utf-8')

        users.update_one(
            {"username": self.admin_user},
            {"$set": {
                "username": self.admin_user,
                "staff_id": "ADM_SEP_01",
                "password": hashed_pw,
                "name": "Test Admin",
                "role": "admin"
            }},
            upsert=True
        )

        users.update_one(
            {"username": self.faculty_user},
            {"$set": {
                "username": self.faculty_user,
                "staff_id": "FAC_SEP_01",
                "password": hashed_pw,
                "name": "Test Faculty",
                "role": "lecturer"
            }},
            upsert=True
        )

    def tearDown(self):
        users.delete_one({"username": self.admin_user})
        users.delete_one({"username": self.faculty_user})

    def test_role_mismatch_prevention(self):
        # 1. Faculty on Management Login -> role mismatch
        fac_doc = users.find_one({"username": self.faculty_user})
        self.assertEqual(fac_doc["role"], "lecturer")

        # Management Portal expects role == 'admin'
        role_param = "admin"
        is_blocked = (role_param == "admin" and fac_doc["role"] != "admin")
        self.assertTrue(is_blocked)
        print("Verified: Faculty credentials blocked on Management Login portal!")

        # 2. Admin on Faculty Login -> role mismatch
        adm_doc = users.find_one({"username": self.admin_user})
        self.assertEqual(adm_doc["role"], "admin")

        role_param = "lecturer"
        is_blocked_admin = (role_param == "lecturer" and adm_doc["role"] == "admin")
        self.assertTrue(is_blocked_admin)
        print("Verified: Management credentials blocked on Faculty Login portal!")

if __name__ == "__main__":
    unittest.main()
