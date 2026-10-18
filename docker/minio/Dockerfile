# MinIO server and client, built from upstream source at pinned releases.
#
# MinIO stopped publishing community images in October 2025 (quay.io/minio/* now needs a login,
# Docker Hub's are gone) and archived both repositories. These are their final releases, built the
# way their Makefiles build them (static, -trimpath, version stamped from the tag's commit), so
# `minio --version` reports the release. Licence: AGPL-3.0, unmodified source (LICENSES.md).
#
# One image serves both compose services: `minio` (the server) and `minio-init` (the one-shot
# bucket and credential setup, docker/minio-init/create-buckets.sh). Build context: docker/.

FROM golang:1.24-bookworm AS build
ENV CGO_ENABLED=0 GOTOOLCHAIN=local
# MINIO_RELEASE / MC_RELEASE=RELEASE is how upstream stamps a release build (else "DEVELOPMENT")
ARG MINIO_TAG=RELEASE.2025-10-15T17-29-55Z
ARG MC_TAG=RELEASE.2025-08-13T08-35-41Z
WORKDIR /src
RUN git clone --quiet --depth 1 --branch "$MINIO_TAG" https://github.com/minio/minio.git minio \
 && cd minio && export MINIO_RELEASE=RELEASE && go build -tags kqueue -trimpath -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/minio
RUN git clone --quiet --depth 1 --branch "$MC_TAG" https://github.com/minio/mc.git mc \
 && cd mc && export MC_RELEASE=RELEASE && go build -tags kqueue -trimpath -ldflags "$(go run buildscripts/gen-ldflags.go)" -o /out/mc

FROM debian:12-slim
COPY --from=build /out/minio /out/mc /usr/local/bin/
COPY minio-init/create-buckets.sh /create-buckets.sh
EXPOSE 9000 9001
ENTRYPOINT ["minio"]
