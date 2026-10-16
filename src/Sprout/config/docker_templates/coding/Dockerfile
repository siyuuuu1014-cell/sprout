# Explicit local smoke image. Pass an existing immutable local image ID.
# No RUN, COPY, downloads or dependency installation; filesystem layers are shared.
ARG BASE_IMAGE
FROM ${BASE_IMAGE}
LABEL sprout.role="isolated-coding" \
      sprout.coding.schema="1" \
      sprout.coding.purpose="explicit-smoke"
