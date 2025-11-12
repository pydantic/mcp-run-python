import json
import uuid
from collections.abc import Callable
from typing import Any, cast

from pyodide.webloop import run_sync  # type: ignore[import-untyped]


def _extract_tool_result(elicit_result: Any, tool_name: str) -> Any:
    # Convert JsProxy to Python dict if needed
    if hasattr(elicit_result, 'to_py'):
        elicit_result = elicit_result.to_py()

    # elicit_result is now a dict with 'action' and 'content' fields
    action = elicit_result['action']
    content = elicit_result['content']

    if action == 'accept':
        # Content is always a dict with 'result' key containing JSON string
        return json.loads(content['result'])
    elif action == 'decline':
        error_msg = content.get('error', 'Unknown error')
        raise RuntimeError(f"Tool execution failed for '{tool_name}': {error_msg}")
    else:
        raise RuntimeError(f"MCP protocol error for tool '{tool_name}': unexpected action '{action}'")


def _handle_elicitation_result(result: Any, tool_name: str) -> Any:
    if hasattr(result, 'then'):
        try:
            resolved_result = cast(dict[str, Any], run_sync(result))
            return _extract_tool_result(resolved_result, tool_name)
        except Exception as e:
            raise RuntimeError(f"Error resolving async result for tool '{tool_name}': {e}") from e
    else:
        return _extract_tool_result(result, tool_name)


def _create_tool_call_part(
    tool_name: str, args: tuple[Any, ...], kwargs: dict[str, Any], tool_schema: dict[str, Any]
) -> dict[str, Any]:
    # Schema-aware parameter mapping - tool_schema is required
    if 'properties' not in tool_schema:
        raise ValueError(f"Tool '{tool_name}' schema missing 'properties' field")

    properties = tool_schema['properties']

    # Handle different argument patterns
    if not args:
        tool_args = kwargs.copy()
    elif len(args) == 1 and properties:
        # Single positional arg - map to first parameter or merge if dict
        first_param_name = next(iter(properties))
        first_arg = args[0]

        if isinstance(first_arg, dict):
            tool_args: dict[str, Any] = {**first_arg, **kwargs}
        else:
            tool_args = {first_param_name: first_arg, **kwargs}
    else:
        # Multiple positional args - map to parameters in order
        param_names = list(properties.keys())
        if len(args) > len(param_names):
            raise ValueError(
                f"Tool '{tool_name}' received {len(args)} positional args but only has {len(param_names)} parameters"
            )

        tool_args = dict(zip(param_names, args))
        tool_args.update(kwargs)

    return {
        'tool_name': tool_name,
        'tool_call_id': str(uuid.uuid4()),
        'args': tool_args,
    }


def _create_tool_function(
    tool_name: str, elicitation_callback: Callable[[str], Any], tool_schema: dict[str, Any]
) -> Callable[..., Any]:
    def tool_function(*args: Any, **kwargs: Any) -> Any:
        # Create tool call with schema-aware parameter mapping
        tool_call_data = _create_tool_call_part(tool_name, args, kwargs, tool_schema)

        elicitation_message = json.dumps(tool_call_data)

        try:
            result = elicitation_callback(elicitation_message)
            return _handle_elicitation_result(result, tool_name)
        except Exception as e:
            raise RuntimeError(f"Error calling tool '{tool_name}': {e}") from e

    return tool_function


def inject_tool_functions(
    globals_dict: dict[str, Any],
    available_tools: list[str],
    elicitation_callback: Callable[[str], Any] | None = None,
    tool_schemas: dict[str, dict[str, Any]] | None = None,
) -> None:
    if not available_tools or elicitation_callback is None:
        return

    if not tool_schemas:
        raise ValueError('tool_schemas is required for tool injection')

    for tool_name in available_tools:
        python_name = tool_name.replace('-', '_')
        if python_name in globals_dict:
            continue

        tool_schema = tool_schemas.get(tool_name)

        if not tool_schema:
            raise ValueError(f"Schema missing for tool '{tool_name}' - cannot inject without schema")

        globals_dict[python_name] = _create_tool_function(
            tool_name=tool_name, elicitation_callback=elicitation_callback, tool_schema=tool_schema
        )
