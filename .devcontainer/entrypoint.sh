#!/usr/bin/env bash
set -euo pipefail

HOST_KEYS_SRC=/tmp/host_authorized_keys
SSH_DIR=/home/vscode/.ssh
AUTH_KEYS=${SSH_DIR}/authorized_keys

if [ -s "${HOST_KEYS_SRC}" ]; then
    install -d -m 700 -o vscode -g vscode "${SSH_DIR}"
    install -m 600 -o vscode -g vscode "${HOST_KEYS_SRC}" "${AUTH_KEYS}"
else
    echo "WARN: ${HOST_KEYS_SRC} is missing or empty — SSH login will fail until authorized_keys is provided." >&2
fi

# Generate host keys on first boot.
ssh-keygen -A

exec /usr/sbin/sshd -D -e
