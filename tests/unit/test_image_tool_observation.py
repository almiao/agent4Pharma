import asyncio
import base64
import json

from mcp.types import CallToolResult, ImageContent, TextContent

from agent4pharma.entrypoints.mcp.observed_tools import observed_tool


def test_image_tool_observation_keeps_metadata_not_binary():
    class Sink:
        def __init__(self):
            self.events = []

        def append(self, event):
            self.events.append(event)

    sink = Sink()

    def inspect() -> CallToolResult:
        return CallToolResult(
            content=[
                TextContent(type="text", text="p2b8"),
                ImageContent(type="image", data=base64.b64encode(b"png").decode(), mimeType="image/png"),
            ]
        )

    result = asyncio.run(observed_tool(inspect, sink, "task", "publishing")())
    assert len(result.content) == 2
    assert [e.status for e in sink.events] == ["started", "succeeded"]
    payload = json.loads(sink.events[-1].output_json)
    image = payload["content"][1]
    assert image["type"] == "image" and image["mimeType"] == "image/png"
    assert image["data"] == {"omitted": True, "characters": 4}
    assert payload["isError"] is False
    assert "cG5n" not in sink.events[-1].output_json
