"""Behavior checks for the R01 reference fixture, not Agent coding acceptance."""

from __future__ import annotations

from Sprout.tests.fixtures import hello_world as module
from Sprout.tests.fixtures.hello_world import GREETING, hello


def test_builtin_self_check_passes() -> None:
    # The module ships its own checks; running them here keeps the two in step.
    module._self_check()


def test_default_greeting_when_no_name_is_given() -> None:
    assert hello() == "Hello, World!"
    assert hello(None) == "Hello, World!"
    assert GREETING == "Hello, World!"


def test_blank_names_fall_back_to_the_default_greeting() -> None:
    assert hello("") == "Hello, World!"
    assert hello("   ") == "Hello, World!"
    assert hello("\t\n") == "Hello, World!"


def test_name_is_interpolated() -> None:
    assert hello("Sprout") == "Hello, Sprout!"


def test_surrounding_whitespace_is_stripped() -> None:
    assert hello("  Sprout  ") == "Hello, Sprout!"


def test_returns_a_string_instead_of_printing(capsys) -> None:
    result = hello()
    assert isinstance(result, str)
    assert capsys.readouterr().out == ""


def test_main_prints_the_greeting(capsys) -> None:
    module.main()
    assert capsys.readouterr().out == "Hello, World!\n"
