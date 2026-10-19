# R01 reference fixtures

`bubble_sort.py` and `hello_world.py` were newly authored for R01 on 2026-10-16,
from the contracts asserted by the pre-existing 18 tests in `test_bubble_sort.py`
and `test_hello_world.py`. The original root-level implementations were absent
from the working tree and reachable Git history at baseline `32e5f734`.

These are first-party, versioned test examples, not recovered original files,
production functionality, Agent output, or an independent coding benchmark.
Passing their self-checks/tests proves only these reference examples satisfy
their declared behavior. It does not validate Sprout's code-generation ability.

The tests import this package explicitly, without root-module lookup or global
`sys.path` changes. Missing fixtures must fail collection, never be skipped.
