---
name: mcp-server-building
description: Build a Model Context Protocol server that models call correctly - the client/server split, tools vs resources vs prompts, tool schemas and descriptions that get picked, stdio vs streamable HTTP transports, auth for remote servers, error handling, and testing tools. Use when exposing an API, database, or internal service to an AI client, when someone mentions "MCP server", "Model Context Protocol", "mcp server", "tools/list", "stdio transport", "streamable HTTP", or "expose our API to the agent".
compatibility: MCP is a fast-moving spec. Read the current spec and SDK docs; verify method names and transport details before shipping. Official SDKs exist for TypeScript and Python.
metadata:
  version: "1.0"
---

# MCP Server Building

MCP standardises how an AI application (the **client**) discovers and calls
capabilities exposed by a **server** you write. You are building the server: a
small, well-described surface over an existing system, safe for a model to call.

The spec moves quickly. Treat this as the shape and the reasoning; verify exact
method names, transport details, and SDK APIs against the current spec and SDK
docs before shipping.

## The model

- **Host / client**: the app the user talks to (Claude Desktop, an IDE, an
  agent runtime). It owns the LLM, the tool-selection logic, and user consent.
- **Server**: your process. It exposes tools, resources, and prompts, and does
  the real work (DB queries, API calls, file reads).
- **One session, many calls.** The client holds a connection; you answer
  requests on it. There is no global "the model" — you serve requests the client
  forwards.
- **JSON-RPC 2.0** over a transport, with lifecycle methods the client calls
  first (initialise → `notifications/initialized` → normal traffic). Handle
  `tools\/list` and `tools\/call` for tools; `resources/list` + `resources/read`
  for resources; `prompts/list` + `prompts/get` for prompts.

## Tools vs resources vs prompts

| | Controlled by | Purpose | Model invokes? |
|---|---|---|---|
| **Tool** | The model chooses | An *action* with side effects or computation: `search_orders`, `create_ticket` | Yes |
| **Resource** | The application/user chooses | *Context* the model can read: a file, a schema, a log, a config | Indirectly, via a tool or by reference |
| **Prompt** | The user explicitly | A reusable message template the user invokes, often with args | No — user-invoked |

The rule: **tools are the model's verbs; resources are the world's nouns;
prompts are the user's shortcuts.** A read-only lookup that the model should
choose on its own is a tool. A document you want the client to offer the user
("this repo's CONTRIBUTING.md") is a resource. Do not shoehorn a resource into a
tool with a `resource_name` parameter, and do not put a side-effecting action in
a prompt.

## Tool schemas that models call reliably

The description is the model's only view of your tool. Design for that.

- **Name is a verb phrase in the domain's language**: `search_orders`,
  `create_ticket`, `get_schema`. No near-duplicates (`list` / `list_files` /
  `fetch_files` guarantee mis-selection).
- **Description says what it does, when to use it, and when *not* to.** Include
  a trigger word the user would type. This is the retrieval key.
- **Inputs**: a JSON Schema. Prefer **flat objects**; deeply nested required
  objects break tool-call validity. Enums for closed sets. Required only what is
  truly required. Document units and formats (`limit: integer, max 100`).
- **Return a small, useful object.** Include the fields a model needs to answer,
  plus counts and a `next_cursor` when paging. A 10k-line dump is a bug. Prefer
  structured content (text + structured data) over a prose blob.
- **Errors are tool results, not crashes.** Return `{ "error": "...", "hint":
  "..." }` as a *successful* call so the model can recover and try again. Only
  protocol-level failures are JSON-RPC errors.
- **Keep the tool set small and cohesive.** A dozen focused tools beat fifty
  overlapping ones; beyond ~20 the client often needs to filter or the model
  gets worse at selection. Group by domain and expose per-domain sets.
- **Side effects**: mark them in the description, require idempotency keys for
  anything retriable, and expect the model to call a destructive tool when the
  user said something adjacent. Gate them in your handler, not in the prompt.

A tool definition:

```ts
// One tool. Name and description are the model's entire UI — write them last.
{
  name: "search_orders",
  description:
    "Search a customer's orders by status, date range, or free text. Use when the " +
    "user asks where an order is, wants a list of orders, or asks about returns. " +
    "Read-only. Returns at most 20 orders; page with next_cursor.",
  inputSchema: {
    type: "object",
    properties: {
      customer_id: { type: "string", description: "Customer id, e.g. cus_123. Required unless query is given." },
      status: { type: "string", enum: ["pending", "paid", "shipped", "refunded"] },
      query: { type: "string", description: "Free text, e.g. product name or last 4 of card." },
      limit: { type: "integer", minimum: 1, maximum: 20, default: 10 }
    },
    required: ["customer_id"]
  }
}
```

## Skeleton (TypeScript; Python SDK mirrors it)

Use an official SDK if one exists for your language — it handles the lifecycle,
transport, and schema plumbing. The shape below is what the SDK is doing for
you; read it so you know where your code actually goes.

```ts
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema } from "@modelcontextprotocol/sdk/types.js";

const server = new Server({ name: "orders-mcp", version: "1.0.0" },
  { capabilities: { tools: {} } });

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools: [SEARCH_ORDERS] }));

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  try {
    const result = await runSearchOrders(req.params.arguments); // your code
    return { content: [{ type: "text", text: JSON.stringify(result) }] };
  } catch (e) {
    // Recoverable: return a tool result the model can act on, not a crash.
    return { content: [{ type: "text",
      text: JSON.stringify({ error: String(e), hint: "Check customer_id format (cus_…)." }) }] };
  }
});

await server.connect(new StdioServerTransport()); // reads stdin/writes stdout
```

**Critical stdio rule: stdout is the protocol channel.** Any stray
`console.log` / `print` corrupts the stream and the client disconnects. Log to
**stderr** (or a file) only. This is the single most common way a stdio MCP
server "works locally and breaks in the client".

## Transport

- **stdio** — the client spawns your process and talks over stdin/stdout. Best
  for local tools (filesystem, a dev database, a CLI). No auth needed (trust is
  the process boundary). Args/config come from env or a config file, not
  `argv` you invent.
- **Streamable HTTP** — client connects to an HTTPS endpoint; the server can
  also push notifications. Best for shared/remote servers, and it is what needs
  **auth**: OAuth 2.1 (authorization-code + PKCE) is the spec's model for
  remote HTTP servers. Verify the current auth spec — it has changed. At
  minimum: authenticate the request, authorise per tool/tenant, bind the token
  to a subject, and never let a tool act on another tenant's data.

**Local-first default: stdio.** Reach for remote HTTP when the server must be
shared, run on another machine, or scale independently. Don't build a remote
server you can't authenticate.

## Resources and prompts (when you have them)

- **Resources** are addressed by URI (`file://…`, `db://schema/orders`). Keep
  them small and static-ish; use a *resource template* (a URI with variables)
  for parameterised reads. The client — not the model — decides when to read
  one, so resources are a poor fit for "the model should look this up now".
- **Prompts** are user-invoked templates with typed arguments. Keep them few and
  clearly named. They are for reusable workflows ("/summarise-this-incident"),
  not for hiding a tool behind a slash command.
- **Do not duplicate the same capability as both a tool and a resource** with
  different behaviour; it doubles the surface to test and reason about.

## Errors, timeouts, and limits

- Return protocol errors (`isError` on the result, or a JSON-RPC error) only for
  genuine protocol faults. Application errors are tool results.
- **Bound every tool**: per-call timeout, max result size, and a page size. A
  tool that can stream a million rows will hang the client.
- **Never let a tool call the model.** That is a server that calls itself back
  (or loops); it is a client concern, and a reliable source of runaway cost.
- Handle `notifications/cancelled` if your client sends it: stop work and
  release resources.

## Testing tools

- **Protocol tests**: `initialize` handshake, `tools\/list` returns valid
  schemas, unknown tool returns a clean error, malformed arguments are
  validated, cancel is handled. A tiny in-process client is enough; the official
  SDKs ship test helpers.
- **Tool tests**: for each tool, a table of (arguments, expected shape) including
  the empty case, the bad-argument case, the authz-denied case, and the
  timeout case. Assert the *shape* of the result, not an exact string.
- **Selection test**: give a model the tool list and a user utterance and check
  it picks the right tool. This is where descriptions earn their keep; keep it
  in your eval set (`skills/ai/llm-evaluation/SKILL.md`).
- **Client smoke test**: actually point a real client at the server and make a
  real call. A large fraction of "the server is fine" bugs are transport or
  stdout noise that only shows up with a real client.
- Test with **no network / the dependency down**: what does the tool return? A
  useful error, or a hang?

## Gotchas

- **`console.log` to stdout breaks stdio servers.** Log to stderr.
- **A tool that raises an unhandled exception kills the request.** Wrap handlers;
  return an error result.
- **A 10k-token tool result silently kills the rest of the context.** Cap it and
  page it.
- **Descriptive but overlapping tool names get mis-selected.** Rename, don't
  add a fourth synonym.
- **`required` on an optional field makes the model guess.** Required means
  required.
- **Auth is not the client's job on a remote server.** A streamable HTTP server
  with no auth is an open, billed, arbitrary-action API.
- **Idempotency for write tools.** Clients retry; a non-idempotent tool becomes
  duplicate charges/emails.
- **The model picks from `description` alone.** A good handler and a terse
  description is a broken tool.
- **Name collisions across servers matter** — two servers offering `search` makes
  selection ambiguous. Namespace names for the domain.

## Checklist

- [ ] Official SDK used; lifecycle handled
- [ ] Tool names/params in domain language; enums for closed sets; flat inputs
- [ ] Each description says what/when/when-not, and flags side effects
- [ ] Results small, structured, paged, with counts
- [ ] Errors returned as recoverable results; only protocol errors as JSON-RPC errors
- [ ] stdio: stdout reserved for protocol, logs to stderr
- [ ] Remote (if any): OAuth/PKCE, per-tool authz, tenant isolation
- [ ] Per-tool timeout, size cap, cancel handling
- [ ] Protocol + tool + selection + client smoke tests pass
- [ ] Dependency-down behaviour tested
