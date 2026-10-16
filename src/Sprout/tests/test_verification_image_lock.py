import tomllib
from pathlib import Path


def test_verification_dependencies_match_repository_wheel_hashes():
    root = Path(__file__).resolve().parents[3]
    packages = {
        item["name"]: item
        for item in tomllib.loads((root / "uv.lock").read_text(encoding="utf-8"))["package"]
    }
    template = root / "src/Sprout/config/docker_templates/verification"
    selected = set()
    for line in (template / "requirements.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        requirement, hash_option = line.split()
        name, version = requirement.split("==")
        selected.add(name)
        assert packages[name]["version"] == version
        assert hash_option.removeprefix("--hash=") in {
            wheel["hash"] for wheel in packages[name]["wheels"]
        }
    assert selected == {"pytest", "iniconfig", "packaging", "pluggy", "pygments"}
    dockerfile = (template / "Dockerfile").read_text(encoding="utf-8")
    assert "--require-hashes" in dockerfile and "--only-binary=:all:" in dockerfile
    assert "@sha256:" in dockerfile
