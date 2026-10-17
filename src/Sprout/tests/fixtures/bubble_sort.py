"""R01 reference fixture, newly authored from the existing behavior tests."""

from collections.abc import Callable
from typing import Any


def bubble_sort[T](
    values: list[T],
    *,
    key: Callable[[T], Any] | None = None,
    reverse: bool = False,
) -> list[T]:
    """Stable, in-place bubble sort; return the original list."""
    keys = [key(value) if key is not None else value for value in values]
    for end in range(len(values) - 1, 0, -1):
        swapped = False
        for index in range(end):
            left, right = keys[index], keys[index + 1]
            if (right < left) if not reverse else (left < right):
                values[index], values[index + 1] = values[index + 1], values[index]
                keys[index], keys[index + 1] = right, left
                swapped = True
        if not swapped:
            break
    return values


def _self_check() -> None:
    values = [3, 1, 2, 1]
    assert bubble_sort(values) is values
    assert values == [1, 1, 2, 3]
    assert bubble_sort([1, 3, 2], reverse=True) == [3, 2, 1]
