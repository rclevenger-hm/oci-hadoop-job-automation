import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "function"))

from job_command import build_hadoop_command, validate_job_params


class JobCommandTests(unittest.TestCase):
    def test_builds_expected_command(self):
        command = build_hadoop_command(
            {
                "jar_path": "/opt/jobs/example job.jar",
                "job_class": "com.example.WordCount",
                "input_path": "oci://bucket/input data",
                "output_path": "oci://bucket/output data",
            }
        )
        self.assertIn("'/opt/jobs/example job.jar'", command)
        self.assertIn("com.example.WordCount", command)
        self.assertIn("'oci://bucket/input data'", command)

    def test_quotes_shell_metacharacters_in_paths(self):
        command = build_hadoop_command(
            {
                "jar_path": "/tmp/job.jar; touch /tmp/pwned",
                "job_class": "Example",
                "input_path": "$(whoami)",
                "output_path": "out && false",
            }
        )
        self.assertIn("'/tmp/job.jar; touch /tmp/pwned'", command)
        self.assertIn("'$(whoami)'", command)
        self.assertIn("'out && false'", command)

    def test_rejects_invalid_job_class(self):
        with self.assertRaises(ValueError):
            validate_job_params(
                {
                    "jar_path": "/tmp/job.jar",
                    "job_class": "Example; rm -rf /",
                    "input_path": "input",
                    "output_path": "output",
                }
            )

    def test_rejects_missing_fields(self):
        with self.assertRaises(ValueError):
            validate_job_params({"jar_path": "/tmp/job.jar"})

    def test_rejects_control_characters(self):
        with self.assertRaises(ValueError):
            validate_job_params(
                {
                    "jar_path": "/tmp/job.jar\nmalicious",
                    "job_class": "Example",
                    "input_path": "input",
                    "output_path": "output",
                }
            )


if __name__ == "__main__":
    unittest.main()
