"""Immutable coding boundary configuration; not permission to run or publish."""

from Sprout.execution.docker_verification import validate_image


def coding_profile(image):
    if not isinstance(image, str):
        raise ValueError("Pinned coding image string required")
    validate_image(image)
    return {"schema_version": 1, "image": image, "protocol": "node-json-v1",
            "auxiliary_checks": "explicit_adapter_required"}


def verify_coding_profile(profile):
    if (not isinstance(profile, dict) or type(profile.get("schema_version")) is not int
            or profile != coding_profile(profile.get("image", ""))):
        raise ValueError("Coding profile missing or unsupported")
    return coding_profile(profile["image"])
