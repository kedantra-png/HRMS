import unittest
from datetime import datetime, timedelta, timezone
from utils.db import login_attempts
from utils.security import (
    get_lockout_status,
    record_failed_attempt,
    record_successful_login,
    LOCKOUT_DURATIONS,
    MAX_INITIAL_ATTEMPTS,
    MAX_SUBSEQUENT_ATTEMPTS
)

class TestAccountLockout(unittest.TestCase):
    def setUp(self):
        self.username = "test_lockout_user"
        record_successful_login(self.username)

    def tearDown(self):
        record_successful_login(self.username)

    def test_progressive_lockout_flow(self):
        print("\n--- Testing Initial State ---")
        status = get_lockout_status(self.username)
        self.assertFalse(status["is_locked"])
        self.assertEqual(status["remaining_attempts"], MAX_INITIAL_ATTEMPTS)

        print("--- Testing Initial 7 Failed Attempts ---")
        for i in range(1, 7):
            res = record_failed_attempt(self.username)
            self.assertFalse(res["is_locked"])
            self.assertEqual(res["remaining_attempts"], MAX_INITIAL_ATTEMPTS - i)

        # 7th Attempt -> Triggers 1 minute (60s) lockout
        res7 = record_failed_attempt(self.username)
        self.assertTrue(res7["is_locked"])
        self.assertEqual(res7["lockout_stage"], 1)
        self.assertEqual(res7["remaining_seconds"], 60)
        self.assertIn("1 minute", res7["formatted_time"])
        print(f"Tier 1 Lockout triggered: {res7['formatted_time']}")

        # Attempt while locked out
        status_locked = get_lockout_status(self.username)
        self.assertTrue(status_locked["is_locked"])
        self.assertGreater(status_locked["remaining_seconds"], 0)

        # Simulate Tier 1 lockout expiration (travel time forward)
        print("--- Simulating Tier 1 Expiration ---")
        past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=70)
        login_attempts.update_one({"username": self.username}, {"$set": {"lockout_until": past_time}})

        status_expired1 = get_lockout_status(self.username)
        self.assertFalse(status_expired1["is_locked"])
        self.assertEqual(status_expired1["remaining_attempts"], MAX_SUBSEQUENT_ATTEMPTS)
        print(f"Tier 1 Expired. Remaining attempts for next lockout: {status_expired1['remaining_attempts']}")

        # Tier 2: 2 failed attempts -> 5 minutes (300s) lockout
        res_t2_1 = record_failed_attempt(self.username)
        self.assertFalse(res_t2_1["is_locked"])
        self.assertEqual(res_t2_1["remaining_attempts"], 1)

        res_t2_2 = record_failed_attempt(self.username)
        self.assertTrue(res_t2_2["is_locked"])
        self.assertEqual(res_t2_2["lockout_stage"], 2)
        self.assertEqual(res_t2_2["remaining_seconds"], 300)
        self.assertIn("5 minutes", res_t2_2["formatted_time"])
        print(f"Tier 2 Lockout triggered: {res_t2_2['formatted_time']}")

        # Simulate Tier 2 lockout expiration
        print("--- Simulating Tier 2 Expiration ---")
        past_time = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(seconds=310)
        login_attempts.update_one({"username": self.username}, {"$set": {"lockout_until": past_time}})

        # Tier 3: 2 failed attempts -> 15 minutes (900s) lockout
        res_t3_1 = record_failed_attempt(self.username)
        res_t3_2 = record_failed_attempt(self.username)
        self.assertTrue(res_t3_2["is_locked"])
        self.assertEqual(res_t3_2["lockout_stage"], 3)
        self.assertEqual(res_t3_2["remaining_seconds"], 900)
        self.assertIn("15 minutes", res_t3_2["formatted_time"])
        print(f"Tier 3 Lockout triggered: {res_t3_2['formatted_time']}")

        # Successful login resets everything
        print("--- Testing Reset on Successful Login ---")
        record_successful_login(self.username)
        final_status = get_lockout_status(self.username)
        self.assertFalse(final_status["is_locked"])
        self.assertEqual(final_status["lockout_stage"], 0)
        self.assertEqual(final_status["remaining_attempts"], MAX_INITIAL_ATTEMPTS)
        print("Reset verified cleanly!")

if __name__ == "__main__":
    unittest.main()
