# shadowscribe-client

The desktop half of [影书 ShadowScribe](https://github.com/yw1103/ShadowScribe):
a `ss` command line and an MCP server that let any AI session start already
briefed on what happened in the physical world.

`httpx` is the only hard dependency. Installing this on a laptop must never pull
in a machine-learning stack.

## Install

> Not on PyPI yet. Install from the repository — the setup script tries PyPI,
> then a local checkout, then GitHub, so you do not have to pick.

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
pip install ".[mcp]"                                                     # from client/
pip install "shadowscribe-client[mcp] @ git+https://github.com/yw1103/ShadowScribe.git#subdirectory=client"
ss login --endpoint http://<server>:18080 --token <SS_TOKEN>
ss doctor
```

## Use

```bash
ss setup                      # ONCE: register MCP + write the static instruction
                              # after this you never run a ShadowScribe command again
ss doctor                     # self-check the whole chain

ss brief                      # print the reality context card (manual peek)
ss brief --copy               # ...and copy it to the clipboard
ss commitments                # what did I promise, and to whom?
ss commitments --done <id>    # tick one off
ss search 登录页               # search memory + raw transcripts
ss timeline --day 2026-01-08
ss upload meeting.m4a --hint "与老王在会议室"
ss mcp                        # stdio MCP server (what `ss setup` registers)
ss inject --auto              # SNAPSHOT into editor rule files — for clients
                              # without MCP; it expires, so MCP is preferred
```

## MCP registration

Cursor / Claude Desktop / Claude Code:

```json
{
  "mcpServers": {
    "shadowscribe": { "command": "ss", "args": ["mcp"] }
  }
}
```

Exposed tools: `get_reality_context`, `list_open_commitments`, `search_reality`,
`get_timeline`, `pending_work_summary`, plus the `shadowscribe://brief` and
`shadowscribe://commitments` resources.

## Configuration

Resolution order: CLI flags → environment → `~/.shadowscribe/config.json` →
`./.shadowscribe.json`.

| Variable | Meaning |
|---|---|
| `SS_ENDPOINT` | Server base URL, e.g. `http://47.102.212.49:18080` |
| `SS_TOKEN` | Bearer token issued by the server |
| `SS_CONFIG` | Override the config file path |
