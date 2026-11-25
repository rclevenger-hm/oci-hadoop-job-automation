import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fdk import constants, event_handler, fixtures

FUNCTION_DIR = Path(__file__).resolve().parents[1] / "function"
sys.path.insert(0, str(FUNCTION_DIR))

import submit_hadoop_job


JOB = {
    "jar_path": "/opt/jobs/example.jar",
    "job_class": "com.example.WordCount",
    "input_path": "oci://bucket@namespace/input",
    "output_path": "oci://bucket@namespace/output",
}
BODY = json.dumps(JOB).encode("utf-8")
CALL_ID = "oci-invocation-test"


class OciInvocationTests(unittest.TestCase):
    def invoke(self, body):
        # Enter through the FDK HTTP adapter: it wraps the request in BytesIO,
        # builds the invocation context and serializes the actual wire result.
        request = fixtures.fake_request()
        request.headers[constants.FN_CALL_ID] = CALL_ID
        request.body = body
        invoke = event_handler.event_handle(fixtures.code(submit_hadoop_job.handler))
        response = asyncio.run(invoke(request))
        self.assertEqual(response.status, 200)
        self.assertEqual(response.content_type, "application/json")
        return json.loads(response.body)

    @patch.object(submit_hadoop_job, "submit_hadoop_job")
    def test_fdk_accepts_body_at_limit_and_returns_json(self, submit):
        submit.return_value = 'application_123 submitted\n'
        body = BODY.ljust(submit_hadoop_job.MAX_REQUEST_BYTES, b" ")

        self.assertEqual(self.invoke(body), {
            "message": "Hadoop job submitted successfully",
            "job_status": submit.return_value,
            "request_id": CALL_ID,
        })
        submit.assert_called_once_with(JOB)

    @patch.object(submit_hadoop_job, "submit_hadoop_job")
    def test_fdk_rejects_limit_plus_one_even_after_a_complete_json_object(self, submit):
        limit = submit_hadoop_job.MAX_REQUEST_BYTES
        body = BODY.ljust(limit + 1, b" ")

        self.assertEqual(self.invoke(body), {
            "error": f"request body exceeds {limit} byte limit",
            "request_id": CALL_ID,
        })
        submit.assert_not_called()

    @patch.object(submit_hadoop_job, "submit_hadoop_job")
    def test_fdk_rejects_empty_and_malformed_json_before_submitting(self, submit):
        for body in (b"", b"{"):
            with self.subTest(body=body):
                result = self.invoke(body)
                self.assertIn("invalid JSON request", result["error"])
                self.assertEqual(result["request_id"], CALL_ID)
        submit.assert_not_called()

    @patch.object(submit_hadoop_job, "submit_hadoop_job")
    def test_fdk_keeps_internal_errors_out_of_json_response(self, submit):
        submit.side_effect = RuntimeError("private runtime detail")
        with self.assertLogs(submit_hadoop_job.LOGGER, level="ERROR"):
            self.assertEqual(self.invoke(BODY), {
                "error": "job submission failed",
                "request_id": CALL_ID,
            })


if __name__ == "__main__":
    unittest.main()
