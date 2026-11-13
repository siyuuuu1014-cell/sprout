# Foreground Coding images

The local foreground Coding workflow selects a Linux image with
`sprout.role=isolated-coding` and `sprout.coding.schema=1`. It inspects local
images, records an immutable image ID, and runs project tests and frozen checks
in isolated Docker containers. The image needs Python, pytest and any project
dependencies. Build or provision those dependencies before the workflow runs.

## Prepare a local smoke image

From the Sprout repository root, build the repository's pinned pytest image,
then use its local ID as the base of the Coding template. The first build may
need network access. The Coding template adds labels without installing
dependencies.

```powershell
docker build -t sprout-verification:pytest-9.1.1 src/Sprout/config/docker_templates/verification
if ($LASTEXITCODE -ne 0) { throw 'Verification base image build failed' }
$baseImage = docker image inspect sprout-verification:pytest-9.1.1 --format '{{.Id}}'
if ($LASTEXITCODE -ne 0) { throw 'Verification base image inspection failed' }
docker build --build-arg "BASE_IMAGE=$baseImage" -t sprout-coding:smoke-v1 src/Sprout/config/docker_templates/coding
if ($LASTEXITCODE -ne 0) { throw 'Coding image build failed' }
docker image inspect sprout-coding:smoke-v1 --format '{{.Id}} {{json .Config.Labels}}'
```

For a project that needs more packages, provision a compatible base with those
packages installed and pass its immutable local image ID to the same template.
With several compatible images available, set `SPROUT_FILESYSTEM_CODING_IMAGE`
to the exact `sha256:...` ID of the desired image. When unset, the CLI discovers
eligible local images. A missing Engine or image blocks preparation. Coding runs
use a local image and do not install project dependencies or pull an image
automatically.

The [dedicated verification image](../verification/README.md) and
[storage services](../README.md) have their own entry points. See the
[developer handoff](../../../../../docs/DEVELOPER_HANDOFF.md) for the CLI workflow
and evidence checklist.
