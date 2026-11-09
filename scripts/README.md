# scripts/
Developer scripts. Must not contain secrets, call paid services or use bash-only syntax
(they run in PowerShell and on Linux).

- `tasks.py`: the task runner, exposed as `uv run tasks <name>` (standard library only).
- `check_licences.py`: fails on forbidden models, model files or packages (invariant 9).
