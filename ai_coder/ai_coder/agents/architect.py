# Author: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: Software Architect agent.
"""Software Architect: spec -> file-by-file design with exact public signatures.

The signatures are the contract between the Coder (implements them) and the
Tester (tests them). Uses no workspace tools.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .. import prompts
from ..parsing import compact_json
from .base import AgentError, BaseAgent


class ArchitectAgent(BaseAgent):
    """Plans the modules and public API of the project."""

    role = "architect"
    max_tokens = 3072

    async def design(self, spec: dict) -> dict:
        """Return {"design": {...}}; raise AgentError if no usable design comes back."""
        try:
            design = await self.ask_json(prompts.ARCHITECT, f"SPEC:\n{compact_json(spec)}", self.max_tokens)
        except ValueError as exc:
            raise AgentError(f"no valid JSON design ({exc})") from exc
        if not isinstance(design, dict) or not design.get("files"):
            raise AgentError("the design lists no files")
        return {"design": design}

    def register(self, mcp: FastMCP) -> None:
        """Expose `design_architecture` as an MCP tool."""

        @mcp.tool()
        async def design_architecture(spec: dict) -> dict:
            """Produce a file-by-file design with exact public signatures."""
            return await self.serve(self.design, spec)
