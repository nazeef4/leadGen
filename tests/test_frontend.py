"""Guards on the browser assets.

The SPA has no build step, so a syntax error in one file silently breaks the
whole UI at load time. These tests catch that in CI, plus a few wiring bugs
that only show up when the DOM is exercised.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parent.parent / "leadgen" / "static"
NODE = shutil.which("node")

JS_FILES = sorted(p.name for p in STATIC.glob("*.js"))


def test_static_assets_present():
    for name in ("index.html", "styles.css", "api.js", "views.js", "app.js", "favicon.svg"):
        assert (STATIC / name).exists(), f"missing static asset {name}"
        assert (STATIC / name).stat().st_size > 0, f"empty static asset {name}"


@pytest.mark.parametrize("filename", JS_FILES)
@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_javascript_parses(filename):
    """A parse error in any script file blanks the entire app."""
    result = subprocess.run(
        [NODE, "--check", str(STATIC / filename)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{filename} failed to parse:\n{result.stderr}"


def test_no_await_lost_in_foreach_callbacks():
    """
    `.forEach(async () => ...)` parses but never awaits, and a bare `await`
    inside a forEach callback does not parse at all. Use `for..of` for
    sequential async work.
    """
    for path in STATIC.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"\.forEach\(\s*async", source), (
            f"{path.name} uses .forEach(async ...) — the promises are never awaited; use for..of"
        )


def test_targeting_view_uses_explicit_element_references():
    """
    The geo picker used to resolve its dropdowns with a document query before
    the card was mounted, so selecting a country threw on an undefined element.
    """
    source = (STATIC / "views.js").read_text(encoding="utf-8")
    assert "$$('#app select')[1]" not in source, "geo picker must not use a document query"
    for ident in ("countrySel", "stateSel", "citySel"):
        assert re.search(rf"const {ident} = h\('select'", source), f"{ident} must be created explicitly"


def test_no_leftover_placeholder_helpers():
    source = (STATIC / "views.js").read_text(encoding="utf-8")
    for stale in ("cityBox", "cityHost"):
        assert stale not in source, f"{stale} is dead code from the old geo picker"


ROOT = Path(__file__).resolve().parent.parent
HARNESS = Path(__file__).resolve().parent / "js" / "ui.test.js"


def _jsdom_available() -> bool:
    """True if node can resolve jsdom from the repo or a NODE_PATH."""
    if NODE is None:
        return False
    env = dict(os.environ)
    env.setdefault("NODE_PATH", os.environ.get("LEADGEN_NODE_PATH", "/tmp/node_modules"))
    probe = subprocess.run(
        [NODE, "-e", "require('jsdom'); process.exit(0)"],
        capture_output=True, text=True, env=env,
    )
    return probe.returncode == 0


@pytest.mark.skipif(NODE is None, reason="node is not installed")
@pytest.mark.skipif(not _jsdom_available(), reason="jsdom is not installed (npm install jsdom)")
def test_spa_renders_every_screen_under_jsdom():
    """
    Real DOM test: mounts index.html in jsdom, loads the three shipped scripts
    against API responses captured from a live server, renders all eight screens
    and drives the geo picker, bulk select, preview and send-plan buttons.
    """
    fixtures = Path(os.environ.get("LEADGEN_UI_FIXTURES", "/tmp/fixtures"))
    if not fixtures.exists():
        pytest.skip("no captured API fixtures; run tests/js/capture_fixtures.sh against a live server")

    env = dict(os.environ)
    env.setdefault("NODE_PATH", os.environ.get("LEADGEN_NODE_PATH", "/tmp/node_modules"))
    result = subprocess.run(
        [NODE, str(HARNESS), str(fixtures)],
        capture_output=True,
        text=True,
        timeout=180,
        cwd=str(ROOT),
        env=env,
    )
    assert result.returncode == 0, f"DOM checks failed:\n{result.stdout}\n{result.stderr}"
    assert "all DOM checks passed" in result.stdout, result.stdout


def test_api_helper_has_no_localhost_urls():
    """The app is served by the same origin it runs on; hard-coded hosts break it."""
    for path in STATIC.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        assert "localhost" not in source, f"{path.name} hard-codes localhost"
        assert "127.0.0.1" not in source, f"{path.name} hard-codes 127.0.0.1"
