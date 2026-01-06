# Minimal Stargz Evaluation - based on actual benchmark setup

FROM debian:bullseye-slim

RUN apt-get update -y && apt-get install -y \
    ca-certificates \
    curl \
    wget \
    git \
    build-essential \
    libseccomp-dev \
    pkg-config \
    fuse3 \
    python3 \
    && rm -rf /var/lib/apt/lists/*

# Install Go
RUN wget -O go.tar.gz https://golang.org/dl/go1.24.0.linux-amd64.tar.gz && \
    tar -C /usr/local -xzf go.tar.gz && rm go.tar.gz
ENV PATH="/usr/local/go/bin:${PATH}"

# Build containerd and runc with exact stargz versions
ARG CONTAINERD_VERSION=v2.1.5
ARG RUNC_VERSION=v1.3.3
ARG NERDCTL_VERSION=2.2.0

RUN cd /tmp && \
    git clone -b ${CONTAINERD_VERSION} --depth 1 https://github.com/containerd/containerd && \
    cd containerd && make && make install PREFIX=/usr/local

RUN cd /tmp && \
    git clone -b ${RUNC_VERSION} --depth 1 https://github.com/opencontainers/runc && \
    cd runc && make && make install PREFIX=/usr/local

# Clone and build stargz-snapshotter
RUN cd /tmp && \
    git clone https://github.com/containerd/stargz-snapshotter.git && \
    cd stargz-snapshotter && \
    make containerd-stargz-grpc && \
    make ctr-remote && \
    cp out/containerd-stargz-grpc /usr/local/bin/ && \
    cp out/ctr-remote /usr/local/bin/

RUN wget -O /tmp/nerdctl.tar.gz \
      https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-amd64.tar.gz && \
    tar -C /usr/local/bin -xzf /tmp/nerdctl.tar.gz && \
    rm /tmp/nerdctl.tar.gz

# Copy configuration files
RUN mkdir -p /etc/containerd /etc/containerd-stargz-grpc
COPY containerd-config-eval.toml /etc/containerd/config.toml
COPY stargz-config.toml /etc/containerd-stargz-grpc/config.toml

# Copy startup script
COPY start.sh /start.sh
COPY prepopulate-registry.sh /prepopulate-registry.sh
RUN chmod +x /prepopulate-registry.sh
RUN chmod +x /start.sh

# Copy evaluation script
COPY *.py /
RUN chmod +x /eval.py

RUN mkdir -p /tmp && chmod 1777 /tmp

WORKDIR /workspace
CMD ["/start.sh", "python3", "/eval.py"]