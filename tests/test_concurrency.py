import threading
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import Job, JobRequest, run_sso_job


class SsoConcurrencyTests(unittest.TestCase):
    def test_accounts_are_processed_in_parallel(self):
        active = 0
        max_active = 0
        lock = threading.Lock()

        def fake_sso_to_token(cookie, **_kwargs):
            nonlocal active, max_active
            with lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.08)
            with lock:
                active -= 1
            return {"access_token": f"opaque-{cookie}", "refresh_token": "refresh", "expires_in": 600}

        request = JobRequest(
            sso_text="cookie-a\ncookie-b\ncookie-c\ncookie-d",
            target_cliproxy=True,
            target_grok=False,
            delay=1,
            max_delay=30,
            retries=1,
            account_retries=1,
            concurrency=4,
        )
        with tempfile.TemporaryDirectory() as folder:
            job = Job("concurrency-test", request, Path(folder))
            with patch("app.main.sso_to_token", side_effect=fake_sso_to_token):
                run_sso_job(job)

        self.assertGreaterEqual(max_active, 2)
        self.assertEqual(job.success, 4)
        self.assertEqual(len(job.files), 4)


if __name__ == "__main__":
    unittest.main()
