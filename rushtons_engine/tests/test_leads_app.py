"""The leads app (leads_app/) must stay in step with the engine: its config is
generated from config.py, and its script must parse."""

import shutil
import subprocess

import pytest

import leads


def test_app_config_is_current():
    # Regenerate with `python leads.py appconfig` after changing a LEAD_* setting.
    assert leads.APP_CONFIG_JS.read_text(encoding="utf-8") == leads.app_config_js()


@pytest.mark.skipif(not shutil.which("node"), reason="needs node on PATH")
def test_app_script_parses():
    app = leads.APP_CONFIG_JS.parent / "app.js"
    r = subprocess.run(["node", "--check", str(app)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
