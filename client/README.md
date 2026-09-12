# shadowscribe-client

The desktop half of [影书 ShadowScribe](https://github.com/yw1103/ShadowScribe):
a `ss` command line and an MCP server that let any AI session start already
briefed on what happened in the physical world.

`httpx` is the only hard dependency. Installing this on a laptop must never pull
in a machine-learning stack.

## Install

```bash
pip install "shadowscribe-client[mcp]"
ss login --endpoint http://<server>:18080 --token <SS_TOKEN>
ss status
```

## Use

```bash
ss brief                      # print the reality context card
ss brief --copy               # ...and copy it to the clipboard
ss inject --auto              # write it into Cursor / CLAUDE.md / AGENTS.md
ss commitments                # what did I promise, and to whom?
ss commitments --done <id>    # tick one off
ss search 登录页               # search memory + raw transcripts
ss timeline --day 2026-01-08
ss upload meeting.m4a --hint "与老王在会议室"
ss mcp                        # stdio MCP server
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
