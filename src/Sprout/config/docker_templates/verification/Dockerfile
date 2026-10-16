ARG BASE_IMAGE=python@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f
FROM ${BASE_IMAGE}

LABEL sprout.role="independent-verification" sprout.verification.schema="1"
ENV PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 PIP_DISABLE_PIP_VERSION_CHECK=1
COPY requirements.txt /opt/sprout-verification/requirements.txt
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r /opt/sprout-verification/requirements.txt
RUN python -m pip check
USER 65532:65532
WORKDIR /workspace
ENTRYPOINT ["python"]
