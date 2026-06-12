# Self-hosted runner Docker image with LaTeX + legal brief tools pre-installed
FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV RUNNER_VERSION=2.317.0

# Base deps
RUN apt-get update && apt-get install -y \
    curl wget git jq python3 python3-pip \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# Tectonic LaTeX engine
RUN curl --proto '=https' --tlsv1.2 -fsSL \
    https://drop.yannick.io/tectonic.sh | sh
ENV PATH="/root/.local/bin:${PATH}"

# Python deps for Notion/Supabase/MotherDuck
RUN pip3 install duckdb requests supabase notion-client --quiet

# GitHub Actions runner
RUN useradd -m runner
WORKDIR /home/runner
RUN curl -o actions-runner.tar.gz -L \
    "https://github.com/actions/runner/releases/download/v${RUNNER_VERSION}/actions-runner-linux-x64-${RUNNER_VERSION}.tar.gz" \
    && tar xzf actions-runner.tar.gz \
    && rm actions-runner.tar.gz

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

USER runner
ENTRYPOINT ["/entrypoint.sh"]
