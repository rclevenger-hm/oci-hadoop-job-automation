import os
import pathlib
import shlex
import shutil
import subprocess
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

    @unittest.skipUnless(shutil.which("sh"), "Remote command requires a POSIX shell")
    def test_preserves_java_class_names_through_remote_shell(self):
        job_classes = (
            "com.example.WordCount",
            "com.example.Outer$Inner",
            "com.example.Outer$$Lambda",
            "com.example.Outer$1",
            "com.example.$Inner$Nested",
        )
        environments = (
            {key: value for key, value in os.environ.items() if key not in ("Inner", "Nested")},
            {**os.environ, "Inner": "Expanded Class", "Nested": "Wrong"},
        )
        for job_class, environment in ((c, e) for c in job_classes for e in environments):
            with self.subTest(job_class=job_class, inner=environment.get("Inner")):
                params = {
                    "jar_path": "/opt/jobs/example job.jar",
                    "job_class": job_class,
                    "input_path": "oci://bucket/input data",
                    "output_path": "oci://bucket/output data",
                }
                command = build_hadoop_command(params)
                # The fake Hadoop command captures argv after real shell expansion.
                # Parsing with shlex.split alone would miss variable/PID expansion.
                completed = subprocess.run(
                    [shutil.which("sh"), "-c",
                     'hadoop() { printf "%s\\0" "$@"; }; ' + command,
                     "test-shell", "ExpandedPositional"],
                    env=environment,
                    capture_output=True,
                    check=True,
                    timeout=5,
                )
                expected = ["jar", params["jar_path"], job_class,
                            params["input_path"], params["output_path"]]
                self.assertEqual(completed.stdout.decode().split("\0")[:-1], expected)
                self.assertEqual(shlex.split(command), ["hadoop", *expected])

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

    def test_rejects_excessively_long_fields(self):
        with self.assertRaisesRegex(ValueError, "input_path exceeds 4096 character limit"):
            validate_job_params(
                {
                    "jar_path": "/tmp/job.jar",
                    "job_class": "Example",
                    "input_path": "x" * 4097,
                    "output_path": "output",
                }
            )


if __name__ == "__main__":
    unittest.main()
