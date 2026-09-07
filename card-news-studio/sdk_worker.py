"""One SDK invocation. Parent owns process lifetime and preserves the session ID."""
import asyncio
import json
import os
import sys
from pathlib import Path
from claude_agent_sdk import ClaudeAgentOptions, ClaudeSDKClient, ResultMessage


def send(**event):
    print(json.dumps(event, ensure_ascii=False), flush=True)


async def main():
    request_path = Path(sys.argv[1])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    options = ClaudeAgentOptions(cwd=str(request_path.parent), tools=[], setting_sources=[], mcp_servers={}, strict_mcp_config=True, max_turns=3, resume=request["session_id"], model=os.environ.get("STUDIO_CLAUDE_MODEL"))
    async with ClaudeSDKClient(options=options) as client:
        await client.query(request["prompt"])
        async for message in client.receive_response():
            if isinstance(message, ResultMessage):
                if message.is_error:
                    raise RuntimeError(message.result or message.subtype)
                send(type="result", sessionID=message.session_id, text=message.result or "")
            else:
                send(type=type(message).__name__)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as exc:
        send(type="error", message=str(exc))
        sys.exit(1)
