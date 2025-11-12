"""PydanticAI integration for mcp-run-python tool injection.

This module provides functionality to inject MCP tools from PydanticAI toolsets
into Python code execution environments via elicitation callbacks.
"""

import json
from typing import Any

from mcp.client.session import ClientSession, ElicitationFnT
from mcp.shared.context import RequestContext
from mcp.types import ElicitRequestParams, ElicitResult
from pydantic import ValidationError
from pydantic.type_adapter import TypeAdapter
from pydantic_ai._run_context import AgentDepsT, RunContext
from pydantic_ai._tool_manager import ToolManager
from pydantic_ai.exceptions import ModelRetry, ToolRetryError
from pydantic_ai.messages import ToolCallPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.toolsets.abstract import AbstractToolset
from pydantic_ai.usage import RunUsage

__all__ = ['create_tool_elicitation_callback']

# Protocol constants for tool discovery
TOOL_DISCOVERY_ACTION = 'discover_tools'


def create_tool_elicitation_callback(toolset: AbstractToolset[Any], deps: AgentDepsT | None = None) -> ElicitationFnT:
    """Create an elicitation callback for tool injection into Python execution.

    This function creates a callback that handles tool discovery and execution
    requests from mcp-run-python

    Args:
        toolset: The PydanticAI toolset containing tools to make available
        deps: Optional dependencies for the tool execution context

    Returns:
        An elicitation callback function that handles tool discovery and execution

    Example:
        ```python
        from mcp_run_python.ext import create_tool_elicitation_callback
        from pydantic_ai import Agent
        from pydantic_ai.mcp import MCPServerStdio
        from pydantic_ai.toolsets import CombinedToolset

        # Create combined toolset with your tools
        my_toolset = CombinedToolset([search_toolset, email_toolset])

        # Create elicitation callback
        callback = create_tool_elicitation_callback(toolset=my_toolset)

        # Use with mcp-run-python
        agent = Agent(
            toolsets=[
                MCPServerStdio(
                    command='mcp-run-python',
                    elicitation_callback=callback
                ),
                elicitation_callback=my_toolset,  # Also available directly to agent
            ]
        )
        ```
    """
    tool_call_adapter = TypeAdapter(ToolCallPart)

    # Create shared run context for both tool discovery and execution
    shared_run_context = RunContext[Any](
        deps=deps,
        model=TestModel(),
        usage=RunUsage(),
    )

    async def elicitation_callback(
        context: RequestContext[ClientSession, Any, Any],  # pyright: ignore[reportUnusedParameter]
        params: ElicitRequestParams,
    ) -> ElicitResult:
        """Handle elicitation requests for tool discovery and execution."""
        try:
            # Parse the elicitation message
            try:
                data = json.loads(params.message)
            except json.JSONDecodeError as e:
                return ElicitResult(action='decline', content={'error': f'Invalid JSON: {e}'})

            # Handle tool discovery requests
            if data.get('action') == TOOL_DISCOVERY_ACTION:
                try:
                    # Use shared context for tool discovery
                    available_tools = await toolset.get_tools(shared_run_context)
                    tool_names = list(available_tools.keys())
                    tool_schemas = {
                        tool_name: toolset_tool.tool_def.parameters_json_schema
                        for tool_name, toolset_tool in available_tools.items()
                    }
                    discovery_result = {'tool_names': tool_names, 'tool_schemas': tool_schemas}
                    return ElicitResult(action='accept', content={'data': json.dumps(discovery_result)})
                except Exception as e:
                    return ElicitResult(action='decline', content={'error': f'Tool discovery failed: {e}'})

            # Handle regular tool execution requests
            try:
                tool_call = tool_call_adapter.validate_python(data)
            except ValidationError as e:
                return ElicitResult(action='decline', content={'error': f'Invalid tool call: {e}'})

            # Execute the tool
            tool_manager = await ToolManager(toolset=toolset).for_run_step(ctx=shared_run_context)
            result = await tool_manager.handle_call(call=tool_call)

            # Return result as JSON
            return ElicitResult(action='accept', content={'result': json.dumps(result)})

        except ToolRetryError as e:
            # Handle tool retry requests with structured information
            retry_info = {
                'error': 'Tool retry needed',
                'tool_name': e.tool_retry.tool_name,
                'message': e.tool_retry.content,
                'tool_call_id': e.tool_retry.tool_call_id,
            }
            return ElicitResult(action='decline', content={'retry': json.dumps(retry_info)})
        except ModelRetry as e:
            return ElicitResult(action='decline', content={'error': f'Model retry failed: {e}'})
        except Exception as e:
            return ElicitResult(action='decline', content={'error': f'Unexpected error: {e}'})

    return elicitation_callback
