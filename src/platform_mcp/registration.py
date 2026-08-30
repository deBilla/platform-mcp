"""One place where every tool is attached to the server.

Registering through here guarantees two things no individual tool has to
remember: each call is instrumented for the audit log, and each tool carries
accurate MCP annotations. Nothing in this package mutates GCP, so
``readOnlyHint`` is true everywhere and ``destructiveHint`` is false -- these
are advisory hints for clients, while the real guarantee stays the viewer-only
identity the server impersonates.
"""

from __future__ import annotations

from typing import Callable

from mcp.types import ToolAnnotations

from .observability import instrument


def register_tool(mcp, fn: Callable, *, open_world: bool = True) -> None:
    """Attach one read-only tool, instrumented and annotated.

    Args:
        open_world: True when the tool reaches out to GCP; False for tools that
            only report this server's own configuration.
    """
    annotations = ToolAnnotations(
        title=fn.__name__.replace("_", " ").title(),
        readOnlyHint=True,
        destructiveHint=False,
        idempotentHint=True,
        openWorldHint=open_world,
    )
    mcp.tool(annotations=annotations)(instrument(fn))
