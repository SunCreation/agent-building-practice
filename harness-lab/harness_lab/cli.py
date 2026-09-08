"""Run: python -m harness_lab.cli --workspace ... --prompt ..."""
# Executing a package file directly loses its package context. Guide the user
# before importing relative modules, including when launched inside this folder.
if __name__ == "__main__" and not __package__:
    import sys
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    print(f"프로젝트 폴더에서 실행하세요: {root}\n"
          "  uv run run.py --help\n"
          "또는: uv run python -m harness_lab.cli --help", file=sys.stderr)
    raise SystemExit(2)

import argparse
import asyncio
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import uuid

import httpx
from openai import AsyncOpenAI

from .agent import Agent, Limits, SessionStore
from .providers import OllamaProvider, OpenAIProvider
from .tools import LocalTools


async def run(args) -> int:
    async def approve(description):
        # A child reads terminal input so cancellation/timeouts never strand a
        # background input() thread or block the event loop.
        print("\n[승인 요청]\n" + description + "\n실행하려면 y: ", end="", flush=True)
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-c",
            "import sys; sys.exit(0 if sys.stdin.readline().strip().lower() == 'y' else 1)",
        )
        try:
            return await process.wait() == 0
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    backend = LocalTools(args.workspace, approve)
    base_url = args.base_url or ("https://api.openai.com/v1" if args.provider == "openai" else "http://localhost:11434")
    model = args.model or ("gpt-4.1-mini" if args.provider == "openai" else "qwen3.5:2b")
    state = args.state_dir.resolve()
    session = SessionStore(state / "sessions", args.session)
    logs = state / "runs"
    logs.mkdir(parents=True, exist_ok=True)
    log_path = logs / (uuid.uuid4().hex + ".jsonl")

    def trace(event):
        print(json.dumps(event, ensure_ascii=False), flush=True)
        with log_path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, ensure_ascii=False) + "\n")

    limits = Limits(max_steps=args.max_steps, max_tool_calls=args.max_tool_calls,
                    total_timeout_seconds=args.timeout, tool_timeout_seconds=args.tool_timeout)
    if args.provider == "openai":
        client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"], base_url=base_url,
                             timeout=args.timeout, max_retries=0)
        provider = OpenAIProvider(client, model, args.max_output_tokens)
    else:
        client = httpx.AsyncClient(base_url=base_url, timeout=args.timeout, trust_env=False)
        provider = OllamaProvider(client, model, args.max_output_tokens)
    try:
        result = await Agent(provider, backend, limits, trace).run(
            args.prompt, session=session,
            settings={"provider": args.provider, "model": model, "base_url": base_url.rstrip("/"),
                      "workspace": str(backend.root)},
        )
        print("\n" + result.answer)
        print(json.dumps({"status": result.status, "metrics": asdict(result.metrics)}, ensure_ascii=False))
        print(f"세션: {session.path}\n실행 기록: {log_path}")
        return 0 if result.status == "completed" else 1
    finally:
        if args.provider == "openai":
            await client.close()
        else:
            await client.aclose()


def main():
    parser = argparse.ArgumentParser(description="직접 구현한 범용·코딩 하네스")
    parser.add_argument("--provider", choices=["openai", "ollama"], default="openai")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--session", default="first")
    parser.add_argument("--state-dir", type=Path, default=Path(".harness"))
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--max-steps", type=int, default=12)
    parser.add_argument("--max-tool-calls", type=int, default=30)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--tool-timeout", type=float, default=30)
    parser.add_argument("--max-output-tokens", type=int, default=4096)
    args = parser.parse_args()
    if args.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY 환경 변수를 설정하세요.")
    if min(args.max_steps, args.max_tool_calls, args.timeout, args.tool_timeout, args.max_output_tokens) <= 0:
        parser.error("실행 상한은 양수여야 합니다.")
    try:
        raise SystemExit(asyncio.run(run(args)))
    except KeyboardInterrupt:
        print("\n실행을 중단했습니다. 실제 파일 상태를 확인하세요.", file=sys.stderr)
        raise SystemExit(130)


if __name__ == "__main__":
    main()
