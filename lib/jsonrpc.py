"""Minimal JSON-RPC 2.0 helpers: enough to speak MCP over one connection."""

import json

PARSE_ERROR = -32700
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602

DEFERRED = object()  # sentinel: handler will respond later, not now


def parse(text):
    try:
        return json.loads(text)
    except (ValueError, TypeError) as exc:
        raise ValueError(str(exc))


def response(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def error_response(msg_id, code, message):
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def notification(method, params=None):
    msg = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        msg["params"] = params
    return msg


class Dispatcher:
    """Routes an incoming JSON-RPC message to a registered method handler.

    Handlers take ``(params, ctx)`` and return a result value, or the
    ``DEFERRED`` sentinel to answer later out-of-band. A message with no
    ``id`` is a notification: any return value is discarded.
    """

    def __init__(self):
        self._methods = {}

    def register(self, name, handler):
        self._methods[name] = handler

    def dispatch(self, message, ctx=None):
        ctx = ctx or {}
        method = message.get("method")
        msg_id = message.get("id")
        handler = self._methods.get(method)
        if handler is None:
            if msg_id is None:
                return None
            return error_response(msg_id, METHOD_NOT_FOUND, "method not found: {}".format(method))
        try:
            result = handler(message.get("params"), ctx)
        except Exception as exc:  # noqa: BLE001 - never let a bad handler kill the reader loop
            if msg_id is None:
                return None
            return error_response(msg_id, INVALID_PARAMS, str(exc))
        if msg_id is None or result is DEFERRED:
            return None
        return response(msg_id, result)
