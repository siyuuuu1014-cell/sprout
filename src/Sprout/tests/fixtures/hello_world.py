"""R01 reference fixture, newly authored from the existing behavior tests."""

GREETING = "Hello, World!"


def hello(name: str | None = None) -> str:
    cleaned = name.strip() if name is not None else ""
    return f"Hello, {cleaned}!" if cleaned else GREETING


def main() -> None:
    print(hello())


def _self_check() -> None:
    assert hello() == GREETING
    assert hello("   ") == GREETING
    assert hello(" Sprout ") == "Hello, Sprout!"
