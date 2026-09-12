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

## MCP

**The MCP server runs on the ShadowScribe server, not here.** This package is CLI
only — `httpx` plus a console script. Point the editor straight at the server:

```json
{
  "mcpServers": {
    "shadowscribe": {
      "url": "http://<server>:18080/mcp",
      "headers": { "Authorization": "Bearer <SS_TOKEN>" }
    }
  }
}
```

`ss setup` writes that for you (merging into any servers you already have) and
also drops a small static instruction telling the agent *when* to call the tools.

Exposed tools: `get_reality_context`, `list_open_commitments`, `search_reality`,
`get_timeline`, `search_memory`, `causal_directory`.

> An earlier version shipped a stdio MCP server inside this package which proxied
> back to the server over HTTP. It was a redundant hop that forced a `pip install`
> onto a machine that only needed a URL, and it is gone.

## Configuration

Resolution order: CLI flags → environment → `~/.shadowscribe/config.json` →
`./.shadowscribe.json`.

| Variable | Meaning |
|---|---|
| `SS_ENDPOINT` | Server base URL, e.g. `http://47.102.212.49:18080` |
| `SS_TOKEN` | Bearer token issued by the server |
| `SS_CONFIG` | Override the config file path |
