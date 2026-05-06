# quirefold dev container

A Linux dev environment with all of quirefold's system tooling preinstalled
(`pandoc`, `typst`, `mmdc`, `chromium`, `poppler-utils`, plus the Python
packages from `run_pipeline.sh`). It runs an SSH server so any IDE or the
`claude` CLI can connect to it remotely.

## One-time setup

1. Install Docker Desktop and make sure it's running.
2. Make sure `%USERPROFILE%\.ssh\authorized_keys` exists on your Windows host
   and contains your public key (`ssh-keygen -t ed25519` then append the
   generated `.pub` to that file). The container bind-mounts this file
   read-only and copies it into `vscode`'s home on each start.

## Lifecycle

From the project root (`E:\Programmering\Code\quirefold`):

```powershell
# build & start in the background
docker compose -f .devcontainer/docker-compose.yml up -d --build

# tail logs
docker logs -f quirefold

# stop
docker compose -f .devcontainer/docker-compose.yml down
```

The container restarts automatically (`restart: unless-stopped`), so it'll
come back up after a Docker Desktop restart.

## Connecting

The container listens on host port **2223**, user **`vscode`**.

| Client                          | How to connect                                                                  |
| ------------------------------- | ------------------------------------------------------------------------------- |
| `ssh`                           | `ssh -p 2223 vscode@localhost`                                                  |
| Claude Code CLI                 | `ssh -p 2223 vscode@localhost`, then run `claude` inside                        |
| VS Code Remote-SSH              | Add host `vscode@localhost`, port `2223`                                        |
| VS Code Dev Containers ext.     | Open the project, "Dev Containers: Reopen in Container"                         |
| Cursor                          | Same as VS Code Remote-SSH                                                      |
| JetBrains Gateway               | New SSH connection → `vscode@localhost:2223`, project at `/workspaces/quirefold` |

The first SSH connection will prompt to trust the new host key — that's
expected. If you rebuild the container with `--build`, host keys are
regenerated and you'll need to clear the old line from
`~/.ssh/known_hosts` (`ssh-keygen -R '[localhost]:2223'`).

## Running the pipeline

Once connected:

```bash
cd /workspaces/quirefold
./run_pipeline.sh input/source.md outputs/output.pdf
```

The project directory is bind-mounted, so files you edit on Windows show up
immediately inside the container and vice versa. The output PDF can be
written under the project tree to make it visible from Windows, or under
`/tmp/` for a throwaway location.

## Pinned tool versions

Set as build args in the Dockerfile — bump there to upgrade:

| Tool        | Version |
| ----------- | ------- |
| pandoc      | 3.1.11  |
| typst CLI   | 0.13.0  |
| mermaid-cli | 10.9.1  |
| Node.js     | 20 LTS  |
| Python      | 3.12    |
