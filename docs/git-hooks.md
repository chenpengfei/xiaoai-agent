# Git Hooks

This repository runs shareable Git hooks through Husky in `.husky/`.
The Husky files are the single source of truth for hook behavior.

Enable them once per clone:

```sh
npm install
```

## Commit Message

Commits must follow Conventional Commits:

```text
feat(scope): add local voice trigger
fix: handle reconnect timeout
docs: update validation runbook
chore!: drop deprecated runtime flag
```

Allowed types are `feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, and `revert`.

## Pre-Commit Guards

The `pre-commit` hook blocks common repository pollution:

- ignored files staged by mistake
- `.DS_Store`, `__pycache__`, and Python bytecode
- generated audio, logs, firmware images, and firmware inspection output
- environment files other than `.env.example`
- key/certificate-like files
- files larger than 10 MiB unless staged as Git LFS pointers
- obvious secret patterns in staged content

## Pre-Push Guards

The `pre-push` hook checks:

- no tracked file is currently ignored by `.gitignore`
- submodule metadata is valid
- submodules are clean
- `voice-gateway` tests pass

Emergency bypasses are available but should be rare:

```sh
SKIP_GIT_GUARDS=1 git commit ...
SKIP_SUBMODULE_CHECK=1 git push
SKIP_TESTS=1 git push
```
