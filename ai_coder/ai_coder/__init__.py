# Author: <YOUR NAME> (Student ID: <YOUR STUDENT ID>)
# SWE 270P Final Project - AI Coder: a multi-agent code generator built on MCP.
"""AI Coder package.

The system takes a software description plus requirements and produces an
executable Python application together with pytest test cases. Five LLM
agents (analyst, architect, coder, tester, debugger) run as separate MCP
servers; an orchestrator (MCP client) coordinates them, and a workspace MCP
server gives them file and test-running tools.
"""

__version__ = "1.0.0"
