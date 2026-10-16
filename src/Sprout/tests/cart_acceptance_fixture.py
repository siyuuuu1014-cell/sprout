"""Independent multi-file exam definition, never Sprout shopping-cart functionality.

SOURCE_FILES is public input. Oracle payloads remain host-side and must not be
written to the model's coding input or presented as model-authored tests.
"""

INSTRUCTION = """Repair the cart, discounts and checkout modules to satisfy this contract.
All money uses integer cents (bool and floats are invalid). Each item is a dict
with price_cents >= 0 and a positive integer quantity (bool is invalid).
The cart is a list; an empty cart is valid. Do not mutate the input.
cart.subtotal(items) validates input and sums price_cents * quantity, raising
ValueError on invalid input. discounts.discount_cents(subtotal_cents) returns
1500 when subtotal_cents >= 10000, otherwise 0; this is a single discount.
checkout.quote(items) must compose these modules and return subtotal_cents,
discount_cents and total_cents. For invalid input return exactly
{"error": "invalid_item"}. Repeated calls must be independent.
Preserve these public APIs and the existing tests. Add tests/test_generated.py
with unit tests for each module and integration tests for quote, including
quantity, discount thresholds, empty cart and invalid input. Run pytest in the
isolated coding container. No network or new dependencies. Do not edit frozen
oracles. Diagnose the implementation from the requirements, not a patch recipe.
"""

SOURCE_FILES = {
    "cart.py": '''def subtotal(items):
    return sum(item["price_cents"] for item in items)
''',
    "discounts.py": '''def discount_cents(subtotal_cents):
    return 1500 if subtotal_cents > 10000 else 0
''',
    "checkout.py": '''from cart import subtotal
from discounts import discount_cents


def quote(items):
    try:
        amount = subtotal(items)
    except (ValueError, TypeError, KeyError):
        return {"error": "invalid_item"}
    return {"subtotal_cents": amount, "discount_cents": 0, "total_cents": amount}
''',
    "test_smoke.py": '''from checkout import quote


def test_empty():
    assert quote([]) == {"subtotal_cents": 0, "discount_cents": 0, "total_cents": 0}


def test_quantity():
    assert quote([{"price_cents": 1200, "quantity": 3}])["total_cents"] == 3600
''',
}

# Supplied only as a host-owned evaluation command, never copied into model inputs.
PROPERTY_PROBE = '''import copy
import sys
sys.path.insert(0, "/workspace")
import cart
import discounts
import checkout

items = [{"price_cents": 2500, "quantity": 4}]
before = copy.deepcopy(items)
first = checkout.quote(items)
checkout.quote([])
assert checkout.quote(items) == first, "repeated calls changed results"
assert items == before, "quote mutated input"
cart.subtotal(items)
assert items == before, "subtotal mutated input"
for invalid in ([{"price_cents": 1, "quantity": True}],
                [{"price_cents": -1, "quantity": 1}], None):
    try:
        cart.subtotal(invalid)
    except ValueError:
        pass
    else:
        raise AssertionError("subtotal must raise ValueError on invalid input")

calls = []
old_subtotal, old_discount = cart.subtotal, discounts.discount_cents
def subtotal_spy(value):
    calls.append("subtotal")
    return 12000
def discount_spy(value):
    calls.append(("discount", value))
    return 1500
# Support both import-module and from-module imports without blessing inline duplication.
for module in (cart, discounts, checkout):
    for key, value in list(vars(module).items()):
        if value is old_subtotal:
            setattr(module, key, subtotal_spy)
        elif value is old_discount:
            setattr(module, key, discount_spy)
assert checkout.quote([]) == {"subtotal_cents": 12000, "discount_cents": 1500,
                              "total_cents": 10500}, "quote did not compose the modules"
assert calls == ["subtotal", ("discount", 12000)], "module call chain mismatch"
print("cart_properties_passed")
'''


def quote_case(identity, items, subtotal, discount, category="normal"):
    return {"id": identity, "target": "checkout:quote", "args": [items],
        "expected_output": {"subtotal_cents": subtotal, "discount_cents": discount,
                            "total_cents": subtotal - discount}, "category": category}


def acceptance_spec():
    return {"cases": [
        quote_case("quantity", [{"price_cents": 1200, "quantity": 3}], 3600, 0),
        quote_case("threshold", [{"price_cents": 2500, "quantity": 4}], 10000, 1500,
                   "boundary"),
        quote_case("mixed", [{"price_cents": 3000, "quantity": 2},
                             {"price_cents": 2000, "quantity": 3}], 12000, 1500),
    ]}


def regression_spec():
    cases = [
        quote_case("empty", [], 0, 0, "boundary"),
        quote_case("below", [{"price_cents": 9999, "quantity": 1}], 9999, 0, "boundary"),
        quote_case("above", [{"price_cents": 10001, "quantity": 1}], 10001, 1500),
        quote_case("once", [{"price_cents": 10000, "quantity": 3}], 30000, 1500),
        quote_case("free", [{"price_cents": 0, "quantity": 2}], 0, 0, "boundary"),
        {"id": "discount_exact", "target": "discounts:discount_cents", "args": [10000],
         "expected_output": 1500, "category": "boundary"},
        {"id": "subtotal_quantity", "target": "cart:subtotal",
         "args": [[{"price_cents": 10, "quantity": 7}]], "expected_output": 70},
    ]
    invalid = {
        "zero_quantity": [{"price_cents": 1, "quantity": 0}],
        "negative_quantity": [{"price_cents": 1, "quantity": -1}],
        "bool_quantity": [{"price_cents": 1, "quantity": True}],
        "float_quantity": [{"price_cents": 1, "quantity": 1.5}],
        "negative_price": [{"price_cents": -1, "quantity": 1}],
        "bool_price": [{"price_cents": True, "quantity": 1}],
        "float_price": [{"price_cents": 0.5, "quantity": 1}],
        "missing_quantity": [{"price_cents": 1}],
        "not_list": {"price_cents": 1, "quantity": 1},
        "not_item": [None],
    }
    cases.extend({"id": identity, "target": "checkout:quote", "args": [items],
                  "expected_output": {"error": "invalid_item"}, "category": "invalid"}
                 for identity, items in invalid.items())
    return {"cases": cases}
