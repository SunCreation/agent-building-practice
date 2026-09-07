"""실행 진입점: 설정 → 세션 → 요청 → 루프 → 저장."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import uuid

from openai import OpenAI

from core import ResponseFormatError, SessionStore, Settings, run_agent
from tools import WorkspaceTools


def approve(description: str) -> bool:
    print("\n[승인 요청]\n" + description)
    try:
        return input("실행하려면 y, 그 외 입력은 거절: ").strip().lower() == "y"
    except EOFError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="OpenAI/Ollama 범용·코딩 하네스")
    parser.add_argument("--provider", choices=["openai", "ollama"], default="openai")
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--session", default="first")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--max-steps", type=int, default=8)
    parser.add_argument("--max-tool-calls", type=int, default=20)
    args = parser.parse_args()
    if args.max_steps < 1 or args.max_tool_calls < 1:
        parser.error("실행 상한은 1 이상이어야 합니다.")
    if args.provider == "openai" and not os.getenv("OPENAI_API_KEY"):
        parser.error("OPENAI_API_KEY를 설정하세요. 키 자체를 --prompt에 넣지 마세요.")
    model = args.model or ("gpt-4.1-mini" if args.provider == "openai" else "qwen3.5:2b")
    base_url = (args.base_url or ("https://api.openai.com/v1" if args.provider == "openai"
                                else "http://localhost:11434/v1")).rstrip("/")
    tools = WorkspaceTools(args.workspace, approve)
    settings = Settings(args.provider, model, base_url, str(tools.root))
    data = Path(__file__).resolve().parent / ".harness"
    store = SessionStore(data / "sessions", args.session)
    messages = store.load(settings)
    messages.append({"role": "user", "content": args.prompt})
    # 요청 전에도 저장하여 강제 종료 시 방금 입력한 요청을 남깁니다.
    store.save(settings, messages)
    runs = data / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    log = runs / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl"

    def trace(event: dict) -> None:
        print("[진행] " + json.dumps(event, ensure_ascii=False), flush=True)
        with log.open("a", encoding="utf-8") as file:
            file.write(json.dumps(event, ensure_ascii=False) + "\n")

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"] if args.provider == "openai" else "ollama-local",
                    base_url=base_url, timeout=120.0, max_retries=0)
    try:
        answer = run_agent(client, settings, messages, tools, max_steps=args.max_steps,
                           max_tool_calls=args.max_tool_calls, trace=trace)
        print("\n" + answer)
        return 0
    except KeyboardInterrupt:
        print("\n사용자가 실행을 중단했습니다. 이미 저장된 파일 변경은 자동 복구되지 않습니다.", file=sys.stderr)
        return 130
    except ResponseFormatError as error:
        print(f"응답 형식 오류: {error}", file=sys.stderr)
        trace({"event": "failed", "error_type": type(error).__name__})
        return 1
    except Exception as error:
        # SDK 예외 전문에는 서버 응답이 포함될 수 있어 타입만 출력합니다.
        print(f"실행 실패 ({type(error).__name__}). 모델·주소·네트워크·권한을 확인하세요. 가짜 응답으로 대체하지 않습니다.", file=sys.stderr)
        trace({"event": "failed", "error_type": type(error).__name__})
        return 1
    finally:
        store.save(settings, messages)
        client.close()
        print(f"세션: {store.path}\n기록: {log}")


if __name__ == "__main__":
    raise SystemExit(main())
