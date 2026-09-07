"""No benchmark answers or real containers used by these boundary tests."""
import asyncio
from types import SimpleNamespace

from harness_lab.agent import ToolRequest
from harness_lab.harbor_agent import HarborTools, HarnessAgent


class Environment:
    def __init__(self):
        self.calls = []

    async def exec(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(return_code=2, stdout="", stderr="intentional test error")


def test_commands_use_environment_and_preserve_nonzero_exit():
    env = Environment()
    result = asyncio.run(HarborTools(env, 17).execute(ToolRequest("a", "terminal", {"command": "false"})))
    assert env.calls == [{"command": "false", "timeout_sec": 17}]
    assert not result.ok and '"exit_code": 2' in result.output


def test_bad_arguments_never_execute():
    env = Environment()
    for args in ({"command": "echo ok", "cwd": "/"}, '{broken', {"command": 5}):
        result = asyncio.run(HarborTools(env).execute(ToolRequest("a", "terminal", args)))
        assert not result.ok
    assert not env.calls


def test_adapter_instantiates_against_pinned_harbor(tmp_path):
    agent = HarnessAgent(logs_dir=tmp_path, model_name="openai/example", max_steps=7)
    assert agent.limits.max_steps == 7
    assert agent.to_agent_info().model_info.name == "example"
