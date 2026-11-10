# Security policy

## Supported versions

Only the latest release on [PyPI](https://pypi.org/project/homebox-mcp/) gets
security fixes. Upgrade before you report: `uvx homebox-mcp@latest`.

## Report a vulnerability

Report it privately through GitHub:
**[Report a vulnerability](https://github.com/dgahagan/homebox-mcp/security/advisories/new)**.
Do not open a public issue for a security problem.

Include:

- the `homebox-mcp` version and the Homebox version,
- the tool call or input that triggers the problem,
- what happened, and what you expected.

Remove your API key, your Homebox URL and any inventory data you do not want
to share.

This is a one-maintainer project. Expect a first reply within a week.

## What counts

The server holds a Homebox API key and can read, change and delete inventory.
These are in scope:

- a way to delete or overwrite data without the `confirm` argument matching,
- a write that lands on a different item than the identifier names,
- a tool that writes a file outside the path it was given, or reads a local
  file the caller did not name,
- the API key appearing in a tool result, an error message or a log line,
- a dependency vulnerability that this server's use of the package exposes.

These are out of scope:

- vulnerabilities in Homebox itself. Report those to
  [sysadminsmedia/homebox](https://github.com/sysadminsmedia/homebox/security).
- what a model chooses to do with the tools. The server cannot stop a client
  that calls `delete_item` with a correct `confirm`. Use your MCP client's
  permission prompts for the destructive tools.

## How the server limits damage

See the [Security section of the README](README.md#security) for the confirm
gates, exact-match writes and API key handling.
