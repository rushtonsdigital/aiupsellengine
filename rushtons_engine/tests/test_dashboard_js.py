"""The dashboard's page script must parse: one stray quote blanks the whole
page for the team (happened 2026-10-31 with "The AI's note")."""

import re
import shutil
import subprocess

import pytest

import dashboard


@pytest.mark.skipif(not shutil.which("node"), reason="needs node on PATH")
def test_page_script_parses(tmp_path):
    scripts = [body for attrs, body in
               re.findall(r"<script([^>]*)>(.*?)</script>", dashboard.FRAG_SRC, re.S)
               if "application/json" not in attrs]
    assert scripts
    for i, body in enumerate(scripts):
        f = tmp_path / f"page{i}.js"
        f.write_text(body, encoding="utf-8")
        r = subprocess.run(["node", "--check", str(f)], capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
