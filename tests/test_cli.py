from __future__ import annotations as _annotations

import pytest

from mcp_run_python._cli import cli_logic


def test_cli_version(capsys: pytest.CaptureFixture[str]):
    assert cli_logic(['--version']) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith('mcp-run-python ')


def test_cli_example_success():
    assert cli_logic(['--deps', 'numpy', 'example']) == 0


def test_cli_example_fail():
    assert cli_logic(['example']) == 1
