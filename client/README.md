# shadowscribe-client

The optional desktop half of [影书 ShadowScribe](https://github.com/yw1103/ShadowScribe):
an `ss` command line for peeking at the reality context card and pointing a new
machine at the server.

**The MCP server is not here.** It runs on the ShadowScribe server, beside the
memory it serves, so an editor connects straight to it with one URL. This package
exists only for the things a command line is better at than an editor.

`httpx` is the only hard dependency — installing this on a laptop must never pull
in a machine-learning stack.

## The desktop needs no install

Put this in `~/.cursor/mcp.json` (or a project's `.cursor/mcp.json`) and restart
the editor:

```json
{
  "mcpServers": {
    "shadowscribe": { "url": "http://<server>:18080/mcp" }
  }
}
```

No package, no local process, no proxy, no token.

## Install the CLI (optional)

> Not on PyPI yet. The setup script tries PyPI, then a local checkout, then GitHub,
> so you do not have to pick.

**Windows:** double-click `scripts\setup-client.cmd`, or from PowerShell:

```powershell
..\scripts\setup-client.cmd http://<server>:18080 <SS_TOKEN>
```

**macOS / Linux:**

```bash
../scripts/setup-client.sh --endpoint http://<server>:18080 --token <SS_TOKEN>
```

By hand:

```bash
pip install .                                     # from client/
ss login --endpoint http://<server>:18080 --token <SS_TOKEN>
ss doctor
```

## Use

```bash
ss setup                      # ONCE: write the MCP URL + the static instruction
ss doctor                     # self-check the whole chain
ss login --endpoint URL --token T

ss brief                      # print the reality context card (manual peek)
ss brief --copy               # ...and copy it to the clipboard
ss commitments                # what did I promise, and to whom?
ss commitments --done <id>    # tick one off
ss search 登录页               # search memory + raw transcripts
ss timeline --day 2026-01-08
ss upload meeting.m4a --hint "与老王在会议室"
ss inject --auto              # SNAPSHOT into editor rule files — for clients
                              # without MCP; it expires, so MCP is preferred
```

## MCP

Served by the server. Tools exposed there:

| Tool | Reads | When it gets called |
|---|---|---|
| `get_reality_context` | ShadowScribe's sensory store | short, under-specified instructions (the core one) |
| `list_open_commitments` | sensory store | "what do I still owe" |
| `search_reality` | sensory store + raw transcripts | a specific person / project / event comes up |
| `get_timeline` | sensory store | "what happened yesterday" |
| `search_memory` | causal-memory graph | looking for decision→outcome experience |
| `causal_directory` | causal-memory graph | skim recent decisions cheaply |

> An earlier version shipped a stdio MCP server inside this package which proxied
> back to the server over HTTP. A redundant hop that forced a `pip install` onto a
> machine that only needed a URL. It is gone.

## Configuration

Resolution order: CLI flags → environment → `~/.shadowscribe/config.json` →
`./.shadowscribe.json`.

| Variable | Meaning |
|---|---|
| `SS_ENDPOINT` | Server base URL, e.g. `http://47.102.212.49:18080` |
| `SS_TOKEN` | Bearer token for `/v1/*` (the `/mcp` endpoint does not need it) |
| `SS_CONFIG` | Override the config file path |

## Tests

```bash
pip install -e '.[dev]'
pytest -q          # 76 tests, no server and no network required
```
