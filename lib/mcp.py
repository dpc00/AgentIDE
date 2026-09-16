"""MCP server layer on top of JSON-RPC: initialize / tools/list / tools/call.

First slice has zero registered tools — the point is proving a real agent
CLI can complete the handshake and report itself connected. Tools get added
once that's verified end to end.
"""

import json

from . import jsonrpc
from .jsonrpc import DEFERRED, Dispatcher

PROTOCOL_VERSION = "2025-03-26"


class ToolError(Exception):
    """Tool-level failure, reported back as an isError content block."""


def wrap_content(result):
    if isinstance(result, str):
        texts = [result]
    elif isinstance(result, (list, tuple)) and result and all(isinstance(x, str) for x in result):
        texts = list(result)
    else:
        texts = [json.dumps(result, ensure_ascii=False)]
    return {"content": [{"type": "text", "text": t} for t in texts]}


def tool_text_response(msg_id, payload):
    """Build a full JSON-RPC response for a tool result resolved out of
    band (a deferred tool like openDiff, once the user acts)."""
    return jsonrpc.response(msg_id, wrap_content(payload))


class MCPServer:
    def __init__(self, server_name="Sublime Text (AgentIDE)", version="0.0.1", logger=None):
        self._server_name = server_name
        self._version = version
        self._log = logger or (lambda msg: None)
        self._tools = {}
        self._handlers = {}

        self.dispatcher = Dispatcher()
        self.dispatcher.register("initialize", self._initialize)
        self.dispatcher.register("notifications/initialized", lambda p, c: None)
        self.dispatcher.register("tools/list", self._tools_list)
        self.dispatcher.register("tools/call", self._tools_call)

    def register_tool(self, name, description, input_schema, handler):
        self._tools[name] = {
            "name": name,
            "description": description,
            "inputSchema": input_schema,
        }
        self._handlers[name] = handler

    def handle_text(self, text, client_id=None):
        try:
            message = jsonrpc.parse(text)
        except ValueError:
            return json.dumps(
                jsonrpc.error_response(None, jsonrpc.PARSE_ERROR, "parse error"),
                ensure_ascii=False,
            )
        self._log("<- #{} {}".format(client_id, message.get("method") or "response"))
        resp = self.dispatcher.dispatch(message, {"client_id": client_id})
        if resp is None:
            return None
        return json.dumps(resp, ensure_ascii=False)

    def _initialize(self, params, ctx):
        self._log("initialize: {}".format(json.dumps(params or {}, ensure_ascii=False)))
        return {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {"listChanged": True}},
            "serverInfo": {"name": self._server_name, "version": self._version},
        }

    def _tools_list(self, params, ctx):
        return {"tools": list(self._tools.values())}

    def _tools_call(self, params, ctx):
        params = params or {}
        name = params.get("name")
        handler = self._handlers.get(name)
        if handler is None:
            raise ValueError("unknown tool: {}".format(name))
        arguments = params.get("arguments") or {}
        try:
            result = handler(arguments, ctx)
        except ToolError as exc:
            wrapped = wrap_content(str(exc))
            wrapped["isError"] = True
            return wrapped
        if result is DEFERRED:
            return DEFERRED
        if isinstance(result, dict) and "content" in result:
            return result  # handler already built its own content blocks
        return wrap_content(result)
