"""Install manifests and CI must cover the environment serving the product."""
from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_runtime_requirements_exactly_match_pyproject() -> None:
    dependencies = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["dependencies"]
    requirements = [line.strip() for line in (ROOT / "requirements.txt").read_text().splitlines()
                    if line.strip() and not line.lstrip().startswith("#")]
    assert requirements == dependencies


def test_ci_checks_both_minimum_and_deployed_python() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    python_job = workflow.split("  python:\n", 1)[1].split("  lint:\n", 1)[0]
    versions = re.search(r'python-version: \[([^\]]+)\]', python_job)
    assert versions is not None, "CI needs a Python version matrix"
    assert set(re.findall(r'"(3\.\d+)"', versions[1])) == {"3.12", "3.14"}
    assert 'python-version: ${{ matrix.python-version }}' in python_job
    assert 'synthetic-voice-scores-${{ matrix.python-version }}' in python_job


def test_browser_server_uses_deployed_python() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()
    e2e_job = workflow.split("  e2e:\n", 1)[1]
    assert 'python-version: "3.14"' in e2e_job
