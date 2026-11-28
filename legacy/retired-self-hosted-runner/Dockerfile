# RETIRED ARCHIVE: Historical self-hosted container runner image.
# Active workflows run exclusively on ephemeral GitHub-hosted `ubuntu-latest` runners.
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV RUNNER_VERSION=2.317.0
ENV RUNNER_SHA256=9e883d210df8c6028aff475475a457d380353f9d01877d51cc01a17b2a91161d
ENV TECTONIC_VERSION=0.15.0
ENV TECTONIC_SHA256=875fbbc9ab48560d7776088c608e0beee49197b57ab4a2f6c5385b2c661c842f

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl git jq python3 python3-pip \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Immutable Tectonic release verified by SHA-256 digest (no remote shell installer)
RUN curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/tectonic.tar.gz \
    "https://github.com/tectonic-typesetting/tectonic/releases/download/tectonic%40${TECTONIC_VERSION}/tectonic-${TECTONIC_VERSION}-x86_64-unknown-linux-gnu.tar.gz" \
    && echo "${TECTONIC_SHA256}  /tmp/tectonic.tar.gz" | sha256sum --check --strict - \
    && tar -xzf /tmp/tectonic.tar.gz -C /usr/local/bin tectonic \
    && chmod 0755 /usr/local/bin/tectonic \
    && rm -f /tmp/tectonic.tar.gz

# Pinned Python dependencies
RUN pip3 install --no-cache-dir --disable-pip-version-check \
    duckdb==1.2.1 \
    requests==2.32.3 \
    supabase==2.13.0 \
    notion-client==2.3.0

# Ephemeral GitHub Actions runner binary verified by SHA-256 digest
RUN useradd -m runner
WORKDIR /home/runner
RUN curl --proto '=https' --tlsv1.2 -fsSL -o /tmp/actions-runner.tar.gz \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && echo "${RUNNER_SHA256}  /tmp/actions-runner.tar.gz" | sha256sum --check --strict - \
    && tar -xzf /tmp/actions-runner.tar.gz \
    && rm -f /tmp/actions-runner.tar.gz

COPY entrypoint.sh /entrypoint.sh
RUN chmod 0755 /entrypoint.sh

USER runner
ENTRYPOINT ["/entrypoint.sh"]
