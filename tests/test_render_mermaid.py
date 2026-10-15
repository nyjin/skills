import subprocess

import pytest

from conftest import SCRIPTS

SCRIPT = str(SCRIPTS / "render-mermaid.sh")


def run(*args):
    return subprocess.run(["bash", SCRIPT, *args], capture_output=True, text=True, timeout=300)


def test_usage_error_without_args():
    assert run().returncode == 2


@pytest.mark.slow
def test_renders_svg(tmp_path):
    src = tmp_path / "a.mmd"
    src.write_text("flowchart LR\n  주문 --> 결제\n")
    out = tmp_path / "a.svg"
    proc = run(str(src), str(out))
    assert proc.returncode == 0, proc.stderr
    assert "<svg" in out.read_text()


@pytest.mark.slow
def test_invalid_syntax_fails_loudly(tmp_path):
    src = tmp_path / "bad.mmd"
    src.write_text("flowchart LR\n  A -->\n  ][\n")
    out = tmp_path / "bad.svg"
    proc = run(str(src), str(out))
    assert proc.returncode == 1
    assert proc.stderr.strip()
