"""Every third-party module the package imports must be a declared dependency.

`cryptography` was imported by security.py but missing from pyproject.toml, so
a fresh `pip install .` failed at import time. This catches that class of drift
between what the code imports and what the metadata promises.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path

import pytest

# tomllib is stdlib from 3.11; the package supports 3.10, so skip rather than
# fail collection there.
tomllib = pytest.importorskip("tomllib", reason="tomllib is stdlib from Python 3.11")

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "leadgen"

# Module name -> distribution name, where they differ.
MODULE_TO_DIST = {
    "bs4": "beautifulsoup4",
    "dotenv": "python-dotenv",
    "dns": "dnspython",
    "yaml": "pyyaml",
    "cv2": "opencv-python",
    "sklearn": "scikit-learn",
    "PIL": "pillow",
    "webview": "pywebview",  # optional desktop extra
}

# Declared with an extra, e.g. pydantic[email].
DIST_BASE = {"pydantic-settings": "pydantic-settings", "uvicorn": "uvicorn"}


def _declared_distributions() -> set[str]:
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = set(data["project"]["dependencies"])
    for extra in (data["project"].get("optional-dependencies") or {}).values():
        deps.update(extra)
    normalised = set()
    for requirement in deps:
        name = requirement
        for sep in ("[", ">=", "==", "<=", "~=", ">", "<", ";", " "):
            name = name.split(sep)[0]
        normalised.add(name.strip().lower().replace("_", "-"))
    return normalised


def _imported_modules() -> set[str]:
    modules: set[str] = set()
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0])
            elif (
                isinstance(node, ast.ImportFrom) and node.level == 0 and node.module
            ):
                modules.add(node.module.split(".")[0])
    return modules


def test_every_third_party_import_is_declared():
    declared = _declared_distributions()
    stdlib = set(sys.stdlib_module_names)
    own = {"leadgen"}

    missing = {}
    for module in sorted(_imported_modules()):
        if module in stdlib or module in own:
            continue
        distribution = MODULE_TO_DIST.get(module, module).lower().replace("_", "-")
        if distribution not in declared:
            missing[module] = distribution

    assert not missing, (
        "these imported packages are not declared in pyproject.toml: "
        + ", ".join(f"{m} -> {d}" for m, d in missing.items())
    )


def test_requirements_txt_matches_pyproject():
    """The two dependency lists must not drift apart."""
    pyproject = _declared_distributions()

    requirements = set()
    for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        name = line
        for sep in ("[", ">=", "==", "<=", "~=", ">", "<", ";", " "):
            name = name.split(sep)[0]
        requirements.add(name.strip().lower().replace("_", "-"))

    assert requirements <= pyproject, (
        f"requirements.txt declares packages missing from pyproject.toml: "
        f"{sorted(requirements - pyproject)}"
    )


def test_cryptography_is_declared():
    """Regression: security.py encrypts credentials with Fernet."""
    assert "cryptography" in _declared_distributions()
    assert (PACKAGE / "security.py").read_text(encoding="utf-8").count("cryptography.fernet") >= 1


def test_no_dev_dependency_in_runtime_deps():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    runtime = " ".join(data["project"]["dependencies"]).lower()
    for dev_only in ("pytest", "ruff", "jsdom"):
        assert dev_only not in runtime, f"{dev_only} must stay in the dev extra"


def test_console_script_points_at_a_real_callable():
    data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    scripts = data["project"]["scripts"]
    assert "leadgen" in scripts, "the leadgen console script must be declared"

    module_path, _, attr = scripts["leadgen"].partition(":")
    module_file = PACKAGE.parent / module_path.replace(".", "/")
    if not module_file.is_dir():
        module_file = module_file.with_suffix(".py")
    assert module_file.exists(), f"console script target {module_path} does not exist"
    assert attr in module_file.read_text(encoding="utf-8"), (
        f"console script entry {attr} is not defined in {module_path}"
    )


def test_readme_test_count_is_accurate():
    """
    The advertised test count drifted twice in one session. Rather than trust a
    hand-edited number, assert the README only mentions counts that are true.
    """
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    claimed = {int(m) for m in re.findall(r"#\s*(\d+)\s+tests", readme)}
    claimed |= {int(m) for m in re.findall(r"tests/\s+(\d+)\s+tests", readme)}
    assert claimed, "README no longer advertises a test count; update this test"

    collected = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "--collect-only"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    match = re.search(r"(\d+) tests collected", collected.stdout)
    assert match, f"could not determine the collected test count:\n{collected.stdout[-500:]}"
    actual = int(match.group(1))

    assert claimed == {actual}, (
        f"README advertises {sorted(claimed)} tests but pytest collects {actual}"
    )


def test_env_example_documents_every_setting():
    """
    .env.example is the user's only map of the configuration surface. Six real
    settings had silently gone undocumented, so assert it stays in sync with
    Settings. Commented-out entries count as documentation.
    """
    from leadgen.config import Settings

    env_keys = {f"LEADGEN_{name}".upper() for name in Settings.model_fields}
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(re.findall(r"(LEADGEN_[A-Z0-9_]+)\s*=", text))

    assert not (env_keys - documented), (
        f"settings missing from .env.example: {sorted(env_keys - documented)}"
    )
    assert not (documented - env_keys), (
        f".env.example documents keys that are not settings: {sorted(documented - env_keys)}"
    )


def test_google_places_key_comes_from_settings():
    """
    The Places key used to be read straight out of os.environ, bypassing
    Settings, so it could not be configured or tested like any other knob.
    """
    from leadgen.config import Settings
    from leadgen.services.scrapers.google_places import GooglePlacesScraper

    source = (PACKAGE / "services" / "scrapers" / "google_places.py").read_text(encoding="utf-8")
    assert "self.settings.google_maps_api_key" in source
    assert "google_maps_api_key" in Settings.model_fields

    scraper = GooglePlacesScraper(Settings(google_maps_api_key="test-key"))
    assert scraper.available is True
    assert scraper.api_key == "test-key"

    empty = GooglePlacesScraper(Settings(google_maps_api_key=""))
    assert empty.available is False
    assert empty.search("anything", limit=5) == []
    assert "not configured" in empty.last_error
