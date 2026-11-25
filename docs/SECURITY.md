# Security and Permissions

Ubermensch runs under the operator's Linux user account as a personal local daemon. Security boundaries focus on limiting what autonomous prompts and LLM tool calls can read, write, or execute without explicit confirmation.

## Trust Boundary

1. **Trusted Core vs. Untrusted Model Inputs**
   - `Execution` context (`runtime/context.py`) uses Python `ContextVar` owned by Core. Models cannot spoof `mode="interactive"` or forge `run_id`/`call_id` via tool arguments.
   - `capabilities/registry.py` strictly validates tool payloads with Pydantic (`extra="forbid"` style contracts) before execution.

2. **Background Capability Restriction**
   - `runtime/policy.py` restricts `mode="background"` jobs to `BACKGROUND_TOOLS` (read tools, memory search, drafting, and writes inside designated artifact/document/report folders).
   - Calling interactive side-effecting tools (sending external messages, running shell commands, deleting files) from background runs raises `PermissionError`.

3. **Filesystem Allowlists and Secret Isolation**
   - `policy.readable()` blocks path components in `SECRET_PARTS` (`.ssh`, `.gnupg`, `.aws`, `.kube`, `.docker`, `.azure`, `.gcloud`, `gh`), files matching `SECRET_FILES` (`.credentials.json`, `.netrc`, `api_token`, `id_ed25519`, etc.), `.env*`, `.session`, and `$DATA_DIR/db` / `$DATA_DIR/secrets`.
   - `policy.writable()` separates interactive write roots (`UBERMENSCH_WRITE_ROOTS`) from narrower background write roots (`UBERMENSCH_BACKGROUND_WRITE_ROOTS`) and rejects protected code/config paths (`config.is_protected()`).

4. **Approval Gates & Shell Sandboxing**
   - Side-effecting tools listed in `RESUMABLE` (`telegram_send`, `email_send`, `run_shell`, `write_file`, `move_path`, `delete_to_recycle_bin`, `calendar_delete`, `suspend_computer`) pause via `POST /api/approval` and compute an `APPROVAL_VERSION`-scoped argument fingerprint so approved payloads cannot be mutated before execution.
   - `UBERMENSCH_SHELL_MODE=confirm` gates host commands and supports `bubblewrap` (`bwrap`) sandboxing with network isolation when executing untrusted shell commands (`system/shell.py`).

5. **Out-of-Repo Secrets**
   - API keys, Telegram `.session` files, SQLite databases, and OAuth tokens live under `DATA_DIR` (`~/Projects/Ubermensch` by default), separated from the git source tree.
