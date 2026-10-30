# Contributing

## Code

- Put each task in its own folder.
- Use simple functions and clear names. Add a class only when it helps.
- Add comments that explain why something is needed.
- Add dependencies only when they solve a real problem.
- Keep validation separate from the work a tool performs.
- Never write debug text to an MCP server's stdout. Use stderr.
- Never commit API keys, tokens, `.env` files, customer data, or local database files.

## Changes

1. Create a branch such as `codex/task-2-gateway`.
2. Make one focused change.
3. Add or update tests when behavior changes.
4. Run the tests for the task you changed.
5. Update its README if setup or behavior changed.
6. Open a pull request. Explain what changed and how you tested it.

Keep dependency changes in `pyproject.toml` and commit the updated `uv.lock`.

## Tests

For Task 1:

```sh
cd task-1-mcp-server
uv run --locked pytest -q
```

For Task 3:

```sh
cd task-3-streaming-guardrail
uv run --locked pytest -q
```

GitHub runs the implemented task suites on Windows and Linux with Python 3.11 and 3.14. Do not mark a future task as complete until its code and tests exist.
