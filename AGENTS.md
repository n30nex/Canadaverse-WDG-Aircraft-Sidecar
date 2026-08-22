# Canadaverse project agent rules

These instructions apply to the whole repository. More specific instructions in a nested `AGENTS.md` take precedence for that subtree.

## Start clean

- Read `README.md`, release notes, workflow files, and relevant docs before editing.
- Run `git status --short --branch` and inspect recent commits. Existing changes belong to their author; do not overwrite, reset, reformat, or bundle them into your work.
- Use a `codex/<task>` branch or an isolated worktree. Rebase or fast-forward from the current default branch before delivery; never force-push shared branches.
- Keep the diff scoped. Avoid speculative refactors, dependency churn, generated noise, and unrelated formatting.

## Keep the repository clean

- Do not commit build directories, caches, virtual environments, `node_modules`, logs, temporary captures, device backups, or downloaded release artifacts unless the repository explicitly tracks a generated deliverable.
- On the shared Windows workstation, keep builds, caches, worktrees, and temporary files on `F:`. Do not consume `C:` for firmware/toolchain output.
- Use existing scripts and pinned tool versions. Do not introduce a dependency when the standard library or an existing dependency covers the task.
- Run `git diff --check` and leave the worktree clean after committing.

## Protect secrets and user data

- Never commit credentials, `.env` files, Wi-Fi passwords, API keys, MQTT credentials, Cloudflare tokens, MeshCore private keys, device identity dumps, live databases, packet captures, or user messages.
- Use synthetic or redacted fixtures and screenshots. Treat radio identities, locations, and private channels as user data.
- Do not weaken authentication, public/private API boundaries, or safe defaults merely to simplify testing.

## Firmware and hardware

- Resolve the exact board, USB identity, build environment, partition/offset, and release artifact before flashing. Never probe or flash unrelated serial devices.
- Preserve bootloaders, SoftDevices, identities, contacts, channels, preferences, and NVS/SPIFFS data unless an explicitly labeled recovery operation requires otherwise.
- Keep board-specific changes guarded. Regression-build an existing target when shared code changes.
- Hardware claims require evidence from the exact build and device. A green compile is not physical qualification.

## Web and services

- Preserve accessibility, keyboard operation, reduced-motion behavior, mobile layouts, privacy boundaries, and existing public routes.
- Before deployment, identify the exact host, service/container, mounted paths, current hashes, and rollback boundary. Never restart or modify unrelated Pi services.
- Verify desktop plus 390 px and 320 px layouts, public links/assets, console output, service health, and restart count after deployment.

## Builds, releases, and completion

- Prefer focused checks first, then the repository's existing CI. Do not invent a second build or release system.
- A release must come from a clean exact commit, with passing required checks, artifact and manifest hashes, accurate filenames/docs, and clear install versus recovery guidance.
- Do not create, move, or replace tags/releases unless the user explicitly requested a release.
- Report what was actually verified. Mark untested hardware, external delivery, or physical behavior as unverified rather than inferring success.
