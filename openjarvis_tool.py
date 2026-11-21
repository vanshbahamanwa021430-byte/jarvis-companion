"""Optional OpenJarvis tool. Import to register; only proposes synthetic reads."""
from __future__ import annotations

import os

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

from agent_client import propose


@ToolRegistry.register("jarvis_request_approval")
class JarvisRequestApproval(BaseTool):
    tool_id = "jarvis_request_approval"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name=self.tool_id,
            description="Ask the owner's Jarvis Companion desk to approve a sample read. "
                        "Never grants approval or returns the sample content.",
            category="custom",
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["read_file", "read_calendar"]},
                    "target": {"type": "string", "enum": ["demo-note", "demo-event"]},
                    "reason": {"type": "string", "maxLength": 240},
                },
                "required": ["action", "target", "reason"],
                "additionalProperties": False,
            },
        )

    def execute(self, **params) -> ToolResult:
        try:
            if set(params) != {"action", "target", "reason"}:
                raise ValueError("Invalid proposal.")
            receipt = propose(**params, port=int(os.environ.get("JARVIS_PROPOSAL_PORT", "8765")),
                              key=os.environ.get("JARVIS_PROPOSAL_KEY", ""))
        except (OSError, ValueError, RuntimeError):
            return ToolResult(tool_name=self.tool_id, content="Could not queue a proposal.", success=False)
        return ToolResult(tool_name=self.tool_id, content=f"Pending owner approval: {receipt['id']}.",
                          success=True)
