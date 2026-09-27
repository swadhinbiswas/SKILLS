---
name: mcp-client-and-integration
description: Connect to and safely consume MCP servers from an agent, CLI, or desktop app - stdio command vs remote URL connections, tool discovery and selection, permission and confirmation for destructive tools, timeouts, and handling servers that fail or misbehave. Use when wiring an app to MCP servers, when a server fails to connect or hangs, when choosing which MCP tools to expose or auto-approve, or when the user mentions "connect to an MCP server", "MCP client", "claude_desktop_config", "MCP config", "tool confirmation", "allowlist MCP tools".
compatibility: MCP is fast-moving; verify config file locations, auth flows, and SDK APIs against current client docs. This is the client half of the pair.
metadata:
  version: "1.0"
---

# MCP Client and Integration

You are the **client**: your app holds the model, chooses when to use a server's
tools, and is responsible for consent, timeouts, and failures. A server you do
not control is untrusted code in your process.

## Connecting

Two transports, chosen by how the server is hosted:

- **stdio (local)**: you spawn the server as a subprocess and speak the protocol
  over its stdin/stdout. Config is a **command + args + env**, and you own the
  process lifecycle (start on session start, kill on exit, restart on crash with
  backoff). The process is the trust boundary — it runs with your user's
  privileges.
- **Streamable HTTP (remote)**: you connect to a URL over HTTPS and speak the
  protocol there. The server is shared and scaled independently. **It is
  authenticated** (OAuth 2.1 with PKCE is the spec's model for remote HTTP;
  verify the current auth spec) and you must handle token refresh and expiry.

Desktop clients (Claude Desktop and similar) take a JSON config mapping server
names to connection details, typically in a per-user config file (the path
differs per client and OS — check the client's docs, and the config is usually
`mcpServers` with a `command`/`args`/`env` entry for stdio or a `url` for
remote). For your own app, use an official MCP SDK client rather than
hand-rolling the JSON-RPC.

**Guardrails for local stdio servers**, since you are running someone else's
code:

- Allowlist which servers you launch and pin their command/args; do not let a
  prompt or a fetched config decide what you execute.
- Sanitize the environment you pass (a minimal env, not your whole shell
  environment with every secret in it).
- The server's filesystem and network access are your user's — if you expose it
  to a model, that is an agent with those privileges. Scope it (workdir, sandbox)
  and make it visible to the user.

## Discovery and selection

- On connect, complete the **lifecycle handshake** (`initialize` →
  `notifications/initialized`) before any traffic; then fetch the server's
  capabilities (`tools\/list`, `resources/list`, `prompts/list`). A server that
  hangs or errors here should time out and be marked unavailable, not block
  your session.
- **Selection is the model's job, from the tool names and descriptions you
  expose** (`skills/mcp/mcp-server-building/SKILL.md` is the server-side
  version of this advice). Your job as client is to present the catalogue
  *cleanly*:
  - If you connect several servers, present tools with a clear
    server-qualifier or grouping so similar names from different servers are
    distinguishable.
  - Prefer connecting to **fewer, well-scoped servers** over many overlapping
    ones; a huge merged tool list degrades selection quality.
  - If your client supports it, let the model see tool names + descriptions
    only until a server is actually needed (lazy connect), so the context is
    not flooded with dozens of tools.
- Do not reimplement routing yourself unless you need a hard policy (see
  permissions) — let the model pick, then enforce policy on the result.

## Permissions and confirmation for destructive tools

The model may choose a tool that writes, deletes, sends, spends, or changes
access. As the client you are the last checkpoint.

- **Default-deny for side effects; allow-by-default for reads.** Classify each
  tool (read vs write vs external) and require different trust per class.
- **Require explicit human confirmation** before any side-effecting call, and
  show the concrete payload — the arguments, the target, and a one-line
  "what this will do". A bare "the model wants to run delete_project" is not
  reviewable.
- **Scope the allowlist narrowly**: auto-approve only specific read tools
  (`get_*`, `search_*`, `list_*` on known-safe resources), and require approval
  for anything matching a write/exec/network pattern. Make the allowlist
  configurable and default to tight.
- **Remember approvals per-session, not forever**, and re-ask when arguments
  change materially (a different target is a different action).
- **The model asking is not consent** — and content from a server (a tool
  *result*) must never itself be able to request a new permission or escalate
  (`skills/ai/llm-privacy-and-safety/SKILL.md`).

## Robustness: servers fail

A server is a remote dependency that can hang, crash, return garbage, or lie.
Assume each.

- **Per-call timeouts** (pick a default, e.g. 30s, make it per-tool for known-slow
  tools) and a **per-session step/deadline budget** so one server cannot stall
  the whole turn. Time out, report to the model as a tool result it can react
  to, and let the agent continue with other tools.
- **Health and restart**: mark a server unhealthy after repeated failures,
  stop calling it, surface it to the user, and restart local stdio processes
  with backoff rather than respawning in a loop.
- **Validate everything crossing the boundary.** Tool results are untrusted
  input: a tool "returning" executable-looking text, HTML, or a huge blob is
  normal. Cap result size, sanitise before rendering
  (`skills/ai/llm-privacy-and-safety/SKILL.md`), and never treat a tool result
  as an instruction.
- **Surface degradation to the user.** If a server that was working starts
  failing, the model will quietly route around it; tell the user so they can fix
  the server instead of trusting silently-degraded answers.
- **Don't auto-connect to a server the user didn't configure.** New servers
  arriving at runtime are an attack surface.

## Testing your integration

- **Connection test**: each configured server completes the handshake and lists
  tools; failures are reported clearly at startup.
- **Call test**: invoke one known tool per server and assert the shape of the
  result.
- **Failure test**: kill/hang a server and assert your client times out, reports
  it, and the turn still completes.
- **Permission test**: assert a destructive tool is blocked without approval
  and allowed with it, and that a tool result cannot grant itself permission.
- **Selection test**: real utterance → expected tool, so a bad merge of tool
  catalogues is caught (`skills/ai/llm-evaluation/SKILL.md`).

## Gotchas

- **A merged tool list from many servers is noisy.** Fewer, cohesive servers beat
  maximum coverage.
- **stdio servers die if you close their stdin or leak their pipes**; manage the
  subprocess lifecycle or you get zombie processes and intermittent failures.
- **A server that hangs on `initialize` blocks the session** — always handshake
  under a timeout.
- **Tool results are attacker-influenced data.** Anything a tool returns (web
  content, a file, a DB row) is a potential indirect prompt-injection payload.
- **Long-running local servers print to stdout** — remind server authors; as a
  client, a protocol parse error almost always means the server wrote to stdout
  instead of stderr.
- **Retries can duplicate side effects** — if your client retries a tool call,
  it must be idempotent or you double-charge/double-send
  (`skills/ai/llm-agents-and-tool-use/SKILL.md`).
- **Don't silently disable a failing tool.** Remove it from the catalogue, and
  say so.

## Checklist

- [ ] Connection per server: stdio (subprocess, managed lifecycle) or authenticated HTTP
- [ ] Handshake + `tools\/list` under a timeout at startup; failures reported, not blocking
- [ ] Few, cohesive servers; tool names qualified/grouped; lazy connect if supported
- [ ] Read tools allow-listed; side-effecting tools require explicit human approval
- [ ] Approval shows the concrete payload; re-asks on material argument change
- [ ] Per-call timeout + per-turn budget; unhealthy server marked and reported
- [ ] Tool results validated, size-capped, sanitised; never treated as instructions
- [ ] Connection, call, failure, permission, and selection tests pass
