import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from app.main import Job, JobRequest, run_job


class CancellationTests(unittest.TestCase):
    def test_running_job_can_be_cancelled_without_writing_outputs(self):
        started = threading.Event()

        def slow_sso_to_token(_cookie, **_kwargs):
            started.set()
            time.sleep(0.2)
            return {"access_token": "opaque-token", "refresh_token": "refresh", "expires_in": 600}

        request = JobRequest(
            sso_text="cookie-a\ncookie-b\ncookie-c",
            target_cliproxy=True,
            target_grok=False,
            retries=1,
            account_retries=1,
            concurrency=1,
        )
        with tempfile.TemporaryDirectory() as folder:
            job = Job("cancellation-test", request, Path(folder))
            worker = threading.Thread(target=run_job, args=(job,))
            with patch("app.main.sso_to_token", side_effect=slow_sso_to_token):
                worker.start()
                self.assertTrue(started.wait(timeout=1))
                self.assertTrue(job.request_cancel())
                worker.join(timeout=1)

            self.assertFalse(worker.is_alive())
            self.assertEqual(job.status, "cancelled")
            self.assertEqual(job.files, [])


if __name__ == "__main__":
    unittest.main()
