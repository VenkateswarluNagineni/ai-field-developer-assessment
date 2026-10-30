# AI Field Developer Assessment

Four Python tasks about MCP servers and AI gateways. Each task has its own code, tests, and setup steps.

| Task | What it does | Status |
| --- | --- | --- |
| [1. MCP server](task-1-mcp-server/) | Checks customer and refund inputs. Uses stdio to talk to an MCP client. | Implemented; 44 tests passed locally |
| [2. MCP security gateway](task-2-mcp-gateway/) | Checks the user's role before allowing admin tools. | Pending |
| [3. Streaming guardrail](task-3-streaming-guardrail/) | Removes personal information while the response streams. | Implemented; 104 tests passed locally |
| [4. Model router](task-4-model-router/) | Limits token use and switches providers when needed. Uses SQLite. | Pending |

## Start Task 1

Install Python 3.11 or newer and [uv](https://docs.astral.sh/uv/getting-started/installation/). Then:

```sh
git clone https://github.com/VenkateswarluNagineni/ai-field-developer-assessment.git
cd ai-field-developer-assessment/task-1-mcp-server
uv sync --locked
uv run --locked server.py
```

The server waits for an MCP client. It writes logs to stderr. It writes only JSON-RPC messages to stdout.

Run the tests:

```sh
uv run --locked pytest -q
```

Task 1 uses sample customers and simulated refunds. It does not transfer money.

## How we work

- Finish and test one task at a time.
- Keep code small and names clear.
- Explain the logic in plain language.
- Test the requirements and important failure cases.
- Keep credentials, local files, and generated data out of Git.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the repository rules and each task's README for its current scope and setup.
