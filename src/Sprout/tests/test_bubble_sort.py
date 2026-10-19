"""Behavior checks for the R01 reference fixture, not Agent coding acceptance."""

from __future__ import annotations

import random

from Sprout.tests.fixtures import bubble_sort as module
from Sprout.tests.fixtures.bubble_sort import bubble_sort


def test_builtin_self_check_passes() -> None:
    # The module ships its own checks; running them here keeps the two in step.
    module._self_check()


def test_empty_and_single_element_inputs() -> None:
    assert bubble_sort([]) == []
    assert bubble_sort([42]) == [42]


def test_sorts_unsorted_input() -> None:
    assert bubble_sort([5, 1, 4, 2, 8]) == [1, 2, 4, 5, 8]


def test_already_sorted_and_reverse_sorted_input() -> None:
    assert bubble_sort([1, 2, 3]) == [1, 2, 3]
    assert bubble_sort([3, 2, 1]) == [1, 2, 3]


def test_duplicates_are_preserved() -> None:
    assert bubble_sort([2, 2, 1]) == [1, 2, 2]


def test_floats_and_strings() -> None:
    assert bubble_sort([1.5, -0.5, 3.0]) == [-0.5, 1.5, 3.0]
    assert bubble_sort(["b", "a", "c"]) == ["a", "b", "c"]


def test_reverse_sorts_descending() -> None:
    assert bubble_sort([5, 1, 4], reverse=True) == [5, 4, 1]


def test_key_sorts_by_mapped_value_and_returns_originals() -> None:
    words = ["banana", "kiwi", "apple"]
    assert bubble_sort(words, key=len) == ["kiwi", "apple", "banana"]


def test_key_keeps_stability_for_equal_keys() -> None:
    stable = [(1, "first"), (0, "zero"), (1, "second"), (0, "zed")]
    assert bubble_sort(stable, key=lambda pair: pair[0]) == [
        (0, "zero"),
        (0, "zed"),
        (1, "first"),
        (1, "second"),
    ]


def test_sorts_in_place_and_returns_the_same_object() -> None:
    original = [3, 1, 2]
    result = bubble_sort(original)
    assert result is original
    assert original == [1, 2, 3]


def test_matches_the_standard_library_on_a_fixed_sample() -> None:
    random.seed(20240924)
    sample = [random.randint(-100, 100) for _ in range(200)]
    assert bubble_sort(list(sample)) == sorted(sample)
    assert bubble_sort(list(sample), reverse=True) == sorted(sample, reverse=True)
