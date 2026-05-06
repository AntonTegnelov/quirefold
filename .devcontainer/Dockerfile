FROM mcr.microsoft.com/devcontainers/python:1-3.12-bookworm

ARG PANDOC_VERSION=3.1.11
ARG TYPST_VERSION=0.13.0
ARG MMDC_VERSION=10.9.1
ARG NODE_MAJOR=20

ENV DEBIAN_FRONTEND=noninteractive \
    PUPPETEER_SKIP_DOWNLOAD=true \
    PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium

# The MS devcontainer base image ships a Yarn apt source whose signing key
# has rotated. We don't use Yarn — drop the source so apt-get update succeeds.
RUN rm -f /etc/apt/sources.list.d/yarn.list /usr/share/keyrings/yarn-archive-keyring.gpg

RUN apt-get update && apt-get install -y --no-install-recommends \
        openssh-server \
        sudo \
        ca-certificates \
        curl \
        xz-utils \
        poppler-utils \
        chromium \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

RUN curl -fsSL "https://github.com/jgm/pandoc/releases/download/${PANDOC_VERSION}/pandoc-${PANDOC_VERSION}-1-amd64.deb" \
        -o /tmp/pandoc.deb \
    && dpkg -i /tmp/pandoc.deb \
    && rm /tmp/pandoc.deb

RUN curl -fsSL "https://github.com/typst/typst/releases/download/v${TYPST_VERSION}/typst-x86_64-unknown-linux-musl.tar.xz" \
        -o /tmp/typst.tar.xz \
    && tar -xJf /tmp/typst.tar.xz -C /usr/local/bin --strip-components=1 \
        --wildcards '*/typst' \
    && chmod +x /usr/local/bin/typst \
    && rm /tmp/typst.tar.xz

RUN curl -fsSL "https://deb.nodesource.com/setup_${NODE_MAJOR}.x" | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && rm -rf /var/lib/apt/lists/* \
    && npm install -g "@mermaid-js/mermaid-cli@${MMDC_VERSION}"

RUN pip install --no-cache-dir \
        "typst==${TYPST_VERSION}" \
        pypdf \
        pdf2image

# run_pipeline.sh hardcodes PUPPETEER_CACHE_DIR=/home/claude/.cache/puppeteer.
# Pre-create that directory with vscode ownership so mmdc can write to it.
RUN install -d -m 755 -o vscode -g vscode /home/claude/.cache/puppeteer

RUN mkdir -p /var/run/sshd \
    && sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication no/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PubkeyAuthentication.*/PubkeyAuthentication yes/' /etc/ssh/sshd_config \
    && sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin no/' /etc/ssh/sshd_config

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN sed -i 's/\r$//' /usr/local/bin/entrypoint.sh \
    && chmod +x /usr/local/bin/entrypoint.sh

# Dockerfile ENV does not propagate into sshd-spawned login shells.
# Persist runtime vars into /etc/environment so PAM picks them up at login.
RUN printf '%s\n' \
        'PUPPETEER_SKIP_DOWNLOAD=true' \
        'PUPPETEER_EXECUTABLE_PATH=/usr/bin/chromium' \
        >> /etc/environment

EXPOSE 22

CMD ["/usr/local/bin/entrypoint.sh"]
