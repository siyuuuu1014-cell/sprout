"""Validate the exam itself before spending any model calls."""

import copy
import importlib.util
import sys

import pytest

from Sprout.strategy.acceptance import AcceptanceSpec
from Sprout.tests.cart_acceptance_fixture import SOURCE_FILES, acceptance_spec, regression_spec

REFERENCE = {
    "cart.py": '''def subtotal(items):
    if not isinstance(items, list):
        raise ValueError("invalid cart")
    total = 0
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("invalid item")
        price, quantity = item.get("price_cents"), item.get("quantity")
        if type(price) is not int or price < 0 or type(quantity) is not int or quantity <= 0:
            raise ValueError("invalid item")
        total += price * quantity
    return total
''',
    "discounts.py": '''def discount_cents(subtotal_cents):
    return 1500 if subtotal_cents >= 10000 else 0
''',
    "checkout.py": '''import cart
import discounts


def quote(items):
    try:
        amount = cart.subtotal(items)
    except ValueError:
        return {"error": "invalid_item"}
    discount = discounts.discount_cents(amount)
    return {"subtotal_cents": amount, "discount_cents": discount,
            "total_cents": amount - discount}
''',
}


def load(root, monkeypatch, *, reference=False):
    root.mkdir()
    sources = {**SOURCE_FILES, **(REFERENCE if reference else {})}
    for name, content in sources.items():
        (root / name).write_text(content, encoding="utf-8")
    modules = {}
    for name in ("cart", "discounts", "checkout"):
        spec = importlib.util.spec_from_file_location(name, root / (name + ".py"))
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        modules[name] = module
    return modules


def observations(modules, payload):
    spec = AcceptanceSpec.from_payload(payload)
    results = {}
    for case in spec.cases:
        module, name = case.target.split(":")
        try:
            actual = getattr(modules[module], name)(*copy.deepcopy(case.args), **case.kwargs)
            results[case.id] = actual == case.expected_output
        except Exception:
            results[case.id] = False
    return results


def test_defective_baseline_fails_each_required_module(tmp_path, monkeypatch):
    modules = load(tmp_path / "baseline", monkeypatch)
    acceptance = observations(modules, acceptance_spec())
    regression = observations(modules, regression_spec())
    assert not any(acceptance.values())
    assert not regression["subtotal_quantity"]
    assert not regression["discount_exact"]
    assert not regression["above"]
    assert not regression["missing_quantity"]
    assert regression["empty"]  # A preserved behavior, not an all-red invented oracle.


@pytest.mark.parametrize("payload", [acceptance_spec(), regression_spec()])
def test_reference_satisfies_all_frozen_examples(tmp_path, monkeypatch, payload):
    modules = load(tmp_path / "reference", monkeypatch, reference=True)
    assert all(observations(modules, payload).values())


def test_repeat_calls_are_independent_and_do_not_mutate_inputs(tmp_path, monkeypatch):
    modules = load(tmp_path / "reference", monkeypatch, reference=True)
    items = [{"price_cents": 2500, "quantity": 4}]
    before = copy.deepcopy(items)
    first = modules["checkout"].quote(items)
    modules["checkout"].quote([])
    assert modules["checkout"].quote(items) == first
    assert items == before


def test_reference_really_composes_modules(tmp_path, monkeypatch):
    modules = load(tmp_path / "reference", monkeypatch, reference=True)
    calls = []
    monkeypatch.setattr(modules["cart"], "subtotal", lambda items: calls.append("cart") or 12000)
    monkeypatch.setattr(modules["discounts"], "discount_cents",
                        lambda amount: calls.append(("discounts", amount)) or 1500)
    assert modules["checkout"].quote([])["total_cents"] == 10500
    assert calls == ["cart", ("discounts", 12000)]


def test_oracle_factories_do_not_share_mutable_state():
    first = acceptance_spec()
    first["cases"][0]["expected_output"]["total_cents"] = -1
    assert acceptance_spec()["cases"][0]["expected_output"]["total_cents"] == 3600
