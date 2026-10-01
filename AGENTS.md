# AGENTS.md

## Commit conventions

- Commits must be signed off (`Signed-off-by:` trailer) **by the human**. Agents must never add a `Signed-off-by:` trailer on the human's behalf — the DCO sign-off is an attestation only the human can make.
- Agents must include an `Assisted-by:` trailer identifying the agent and model.
- Order trailers as: `Assisted-by:` first, then the human's `Signed-off-by:` last (added by the human).

Format:

```
Assisted-by: AGENT_NAME:MODEL_VERSION
```

- `AGENT_NAME` — the AI tool or framework (e.g. `claude-code`, `opencode`, `codex`, `pi`, …).
- `MODEL_VERSION` — the specific model version used (e.g. `claude-sonnet-4-6`, `gpt-5.5`).

Example:

```
Assisted-by: opencode:gpt-5.5
```

Other commit rules:

- Commit messages must be ASCII only.
- Keep PRs small and focused; don't mix trivial and controversial changes.
- Squash into logical commits (API / docs / CLI / daemon / tests / CI) for non-trivial PRs.
- Maintain a linear git history.

## Build

Requires the `rockcraft` and `lxd` snaps on the host:

```
sudo snap install rockcraft --classic
sudo snap install lxd && sudo lxd init --auto
```

Build the rock:

```
rockcraft pack -v
```

Output: `ceph_<version>_amd64.rock` in the repo root. Load into Docker via:

```
sudo /snap/rockcraft/current/bin/skopeo --insecure-policy copy \
    oci-archive:ceph_<version>_amd64.rock docker-daemon:canonical/ceph:latest
```

## Lint and tests

- **Python lint:** `flake8 .` — the only lint gate in CI (`.github/workflows/build_and_test.yaml`).
- **Integration tests:** CI-only. The `CephadmTest` and `RookTest` jobs in `build_and_test.yaml` build the rock, then deploy it inside an LXD VM and exercise it. Do not try to run these locally; if you need a hands-on cluster, use `python3 test/deploy.py image <image-ref>` per `HACKING.md`.
- There is no unit test target.
