from __future__ import annotations as _annotations

import warnings

import pytest

from mcp_run_python._cli import cli_logic


def test_cli_version(capsys: pytest.CaptureFixture[str]):
    assert cli_logic(['--version']) == 0
    captured = capsys.readouterr()
    assert captured.out.startswith('mcp-run-python ')


def test_cli_example_success():
    assert cli_logic(['--deps', 'numpy', 'example']) == 0


def test_cli_dep_repeatable():
    """Test new --dep repeatable flag"""
    assert cli_logic(['--dep', 'numpy', 'example']) == 0


def test_cli_dep_multiple():
    """Test multiple --dep flags"""
    assert cli_logic(['--dep', 'numpy', '--dep', 'pydantic', 'example']) == 0


def test_cli_dep_and_deps_combined():
    """Test combining --dep and --deps (backwards compat)"""
    assert cli_logic(['--dep', 'numpy', '--deps', 'pydantic', 'example']) == 0


def test_cli_deps_deprecation_warning():
    """Test that --deps emits a deprecation warning"""
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter('always')
        cli_logic(['--deps', 'numpy', 'example'])
        deprecation_warnings = [x for x in w if issubclass(x.category, DeprecationWarning)]
        assert len(deprecation_warnings) >= 1
        assert any('--deps is deprecated' in str(x.message) for x in deprecation_warnings)


def test_cli_example_fail():
    assert cli_logic(['example']) == 1
