# Independent verification image

This image is only for Python/pytest acceptance observations. It is not the
Sprout runtime image and contains no business dependencies or project checkout.

The base image is pinned by digest in `Dockerfile`. The five Linux pytest
dependencies are pinned by version and wheel SHA256 from the repository
`uv.lock`. Builds use networking; verification containers do not. A missing
dependency must block verification, never trigger installation at test time.

Build explicitly with user authorization:

```powershell
docker build -t sprout-verification:pytest-9.1.1 src/Sprout/config/docker_templates/verification
docker image inspect sprout-verification:pytest-9.1.1 --format '{{.Id}}'
```

Use the returned immutable image ID, not the mutable tag, for validation.
Image identity is machine/build-specific; do not assume a rebuild has the same ID.

The opt-in integration test only uses generated sample files:

```powershell
$env:SPROUT_VERIFICATION_TEST_IMAGE = 'sha256:<actual-local-image-id>'
uv run pytest -q -p no:cacheprovider --basetemp=.workbuddy/test-runs/verification-image-manual src/Sprout/tests/test_docker_verification_integration.py
```

It checks non-root execution, read-only input/observer, no outbound connection,
temporary writes and structured pytest collection. Only its owned, labeled,
stopped container is removed. Unknown/running containers are retained for review;
test stdout/stderr and container inspection are retained under the basetemp.

The observer can emit a nonce-bound JSON line through stdout (`output='-'`), so
no writable host evidence directory is mounted. Reports remain untrusted
observations because tests and the observer share a process.

**Current boundary:** code replay inspects the explicit local image through the
existing authorization broker before model execution. Unavailable, unapproved or
incompatible images park the new replay task. Completed code runs can export
inputs and produce Docker observations; there is no host verification fallback.

The dedicated export excludes Git/runtime/cache directories and rejects
default/custom-classified private/data files and links. It is sealed by a file
manifest. This is not comprehensive content-level secret detection.

Execution claims live in the existing metadata database (`verification_executions`).
Each task/command slot has one non-expiring owner and a frozen request fingerprint.
Known completed observations are cached; active/uncertain results cannot be
replayed. Cancellation, timeout, missing/mismatched report or failed result commit
requires review. The container name is recorded before launch. Production
containers are retained for review, not automatically stopped/deleted; a client
timeout does not prove the container stopped. Do not manually clear the claim.

`measurement_status` distinguishes a completed coding task from a waiting or
observed independent measurement. Reports remain `trusted_execution=false` and
`validation_committed=false`. Automatic candidate promotion, trusted validation
handoff, approval resume and human evidence reconciliation are not implemented.
Generic agent execution/evaluation still uses its existing architecture; only
independent post-run measurement has moved to Docker.
