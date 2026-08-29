import re
import shlex

_JOB_CLASS = re.compile(r"^[A-Za-z0-9_.$]+$")
_REQUIRED_FIELDS = ("jar_path", "job_class", "input_path", "output_path")


def validate_job_params(job_params):
    if not isinstance(job_params, dict):
        raise ValueError("job parameters must be an object")

    clean = {}
    for field in _REQUIRED_FIELDS:
        value = job_params.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} is required and must be a non-empty string")
        if "\x00" in value or "\n" in value or "\r" in value:
            raise ValueError(f"{field} contains unsupported control characters")
        clean[field] = value.strip()

    if not _JOB_CLASS.fullmatch(clean["job_class"]):
        raise ValueError("job_class contains unsupported characters")

    return clean


def build_hadoop_command(job_params):
    params = validate_job_params(job_params)
    return "hadoop jar {jar} {job_class} {input_path} {output_path}".format(
        jar=shlex.quote(params["jar_path"]),
        job_class=params["job_class"],
        input_path=shlex.quote(params["input_path"]),
        output_path=shlex.quote(params["output_path"]),
    )
