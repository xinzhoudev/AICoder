# Author: Yuxiao Xiao (Student ID: 62185048)
# SWE 270P Final Project - AI Coder: registry of the five LLM agents.
"""The five LLM agents, one module per role.

| role      | MCP tool               | workspace tools it may use                        |
|-----------|------------------------|---------------------------------------------------|
| analyst   | analyze_requirements   | (none)                                            |
| architect | design_architecture    | (none)                                            |
| coder     | generate_code          | write_files, check_syntax, read_files             |
| tester    | generate_tests         | extract_api, write_files, check_syntax            |
| debugger  | fix_failures           | list_files, read_files, write_files, check_syntax |
"""

from .analyst import AnalystAgent
from .architect import ArchitectAgent
from .base import AgentError, BaseAgent
from .coder import CoderAgent
from .debugger import DebuggerAgent
from .tester import TesterAgent

AGENTS: dict[str, type[BaseAgent]] = {
    cls.role: cls for cls in (AnalystAgent, ArchitectAgent, CoderAgent, TesterAgent, DebuggerAgent)
}

# Allow-list of workspace tools per role (the table above, taken from the classes).
ROLE_TOOLS: dict[str, set[str]] = {role: set(cls.tools) for role, cls in AGENTS.items()}

__all__ = ["AGENTS", "ROLE_TOOLS", "AgentError", "BaseAgent", "AnalystAgent", "ArchitectAgent",
           "CoderAgent", "TesterAgent", "DebuggerAgent"]
