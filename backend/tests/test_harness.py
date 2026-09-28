"""P4a harness runtime tests."""
import pytest

from app.harness import Agent, FunctionTool, Runtime, build_user_message
from app.llm.schema import StrictModel


class Out(StrictModel):
    value: str


class ToolInput(StrictModel):
    url: str


class FakeClient:
    def __init__(self, result=None, error=None):
        self.result = result or {"value": "ok"}
        self.error = error
        self.messages = []

    async def chat_json(self, messages, out_model):
        self.messages.append(messages)
        if self.error:
            raise self.error
        return out_model.model_validate(self.result)


def make_runtime(client):
    return Runtime(client)


async def test_call_json_happy_path():
    client = FakeClient()
    runtime = make_runtime(client)
    runtime.register(Agent(id="a", system_prompt="sp"))
    result = await runtime.call_json("a", "do it", "ctx", Out)
    assert result.value == "ok"
    assert [t.type for t in runtime.traces()] == ["queued", "started", "succeeded"]


async def test_call_error_is_wrapped():
    runtime = make_runtime(FakeClient(error=RuntimeError("boom")))
    runtime.register(Agent(id="a", system_prompt="sp"))
    with pytest.raises(Exception, match="boom"):
        await runtime.call_json("a", "x", "", Out)
    assert [t.type for t in runtime.traces()] == ["queued", "started", "error"]


def test_register_requires_prompt():
    with pytest.raises(ValueError):
        make_runtime(FakeClient()).register(Agent(id="a", system_prompt=""))


async def test_call_unknown_agent():
    with pytest.raises(ValueError):
        await make_runtime(FakeClient()).call_json("nope", "x", "", Out)


def test_build_user_message():
    msg = build_user_message("inst", "ctx")
    assert msg.startswith("TASK\ninst")
    assert "<reference>\nctx\n</reference>" in msg


def test_build_user_message_without_context():
    msg = build_user_message("inst", "")
    assert msg == "TASK\ninst"


async def test_tool_call():
    async def handler(input_dict):
        return {"title": input_dict["url"]}

    runtime = make_runtime(FakeClient())
    runtime.register_tool(FunctionTool("fetch", "d", ToolInput, handler))
    runtime.register(Agent(id="a", system_prompt="sp", tools=["fetch"]))
    result = await runtime.call_tool_json("a", "fetch", {"url": "x"})
    assert result == {"title": "x"}
    assert [t.type for t in runtime.traces()] == ["queued", "started", "succeeded"]


async def test_tool_not_allowed_for_agent():
    async def handler(input_dict):
        return {}

    runtime = make_runtime(FakeClient())
    runtime.register_tool(FunctionTool("fetch", "d", ToolInput, handler))
    runtime.register(Agent(id="a", system_prompt="sp", tools=[]))
    with pytest.raises(ValueError):
        await runtime.call_tool_json("a", "fetch", {"url": "x"})
