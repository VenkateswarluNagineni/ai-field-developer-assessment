# Instructions for coding agents

Read CONTRIBUTING.md before changing files.

- Use plain language in documentation and explanations.
- Keep each task in its own folder.
- Use clear names and small functions. Avoid extra frameworks and layers.
- Work on the task requested by the user. Do not implement future tasks without being asked.
- Preserve Task 1's validation, JSON-RPC error codes, and stdout/stderr separation.
- Keep the official MCP SDK. The transport adapter exists to meet the assessment's error-code rules.
- Run the affected task's tests after changing its code.
- Update README status only after verification.
- Never commit secrets, machine-specific paths, virtual environments, or database files.
- Use branches starting with codex/ for changes after the initial repository setup.
