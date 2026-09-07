"""모델에게 보이는 도구 설명과 실제 함수의 연결. OS sandbox는 아닙니다."""
from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Callable


MAX_FILE_BYTES = 100_000
MAX_RESULT_CHARS = 12_000


def clip(text: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[출력 일부 생략]...\n" + text[-limit // 2:]


def schema(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {
        "name": name, "description": description,
        "parameters": {"type": "object", "properties": properties,
                       "required": required, "additionalProperties": False},
    }}


class WorkspaceTools:
    def __init__(self, workspace: Path, approve: Callable[[str], bool]):
        self.root = workspace.resolve(strict=True)
        if not self.root.is_dir():
            raise ValueError("workspace는 존재하는 폴더여야 합니다.")
        self.approve = approve
        self.registry = {
            "list_files": self.list_files, "read_file": self.read_file,
            "search_files": self.search_files, "write_file": self.write_file,
            "run_tests": self.run_tests,
        }
        string = {"type": "string"}
        self.schemas = [
            schema("list_files", "작업 폴더의 일반 파일 경로를 최대 200개 나열합니다.", {}, []),
            schema("read_file", "UTF-8 텍스트 파일을 읽습니다. 최대 100KB, 결과는 일부 생략될 수 있습니다.", {"path": string}, ["path"]),
            schema("search_files", "텍스트 파일에서 대소문자를 구분하지 않고 문자열을 검색합니다. 최대 50개 일치 행.", {"query": string}, ["query"]),
            schema("write_file", "사람의 승인을 받아 UTF-8 파일 전체를 저장합니다. 먼저 기존 파일을 읽으세요.", {"path": string, "content": string}, ["path", "content"]),
            schema("run_tests", "사람의 승인을 받아 작업 폴더의 Python unittest를 실행합니다. 테스트 코드 자체가 실행됩니다.", {}, []),
        ]

    def safe_path(self, relative: str) -> Path:
        if not isinstance(relative, str) or not relative or len(relative) > 500:
            raise ValueError("유효한 상대 경로가 필요합니다.")
        path = Path(relative)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("절대 경로와 .. 경로는 허용하지 않습니다.")
        # 숨김 파일에는 .env/.git 등이 포함됩니다. 심볼릭 링크도 따라가지 않습니다.
        candidate = self.root
        for part in path.parts:
            if part.startswith("."):
                raise ValueError("숨김 경로는 허용하지 않습니다.")
            candidate = candidate / part
            if candidate.is_symlink():
                raise ValueError("심볼릭 링크는 허용하지 않습니다.")
        if not candidate.resolve().is_relative_to(self.root):
            raise ValueError("작업 폴더 밖의 경로입니다.")
        return candidate

    def files(self):
        count = 0
        for folder, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d != "__pycache__"
                             and not (Path(folder) / d).is_symlink())
            for name in sorted(names):
                candidate = Path(folder) / name
                if name.startswith(".") or candidate.is_symlink() or not candidate.is_file():
                    continue
                yield candidate
                count += 1
                if count >= 200:
                    return

    def list_files(self) -> dict:
        return {"files": [str(p.relative_to(self.root)) for p in self.files()], "limit": 200}

    def read_file(self, path: str) -> dict:
        target = self.safe_path(path)
        if not target.is_file():
            raise ValueError("일반 파일이 아닙니다.")
        if target.stat().st_size > MAX_FILE_BYTES:
            raise ValueError("100KB보다 큰 파일입니다.")
        text = target.read_text(encoding="utf-8")
        return {"path": path, "content": clip(text), "truncated": len(text) > MAX_RESULT_CHARS}

    def search_files(self, query: str) -> dict:
        if not isinstance(query, str) or not query or len(query) > 200:
            raise ValueError("검색어는 1~200자 문자열이어야 합니다.")
        matches = []
        for target in self.files():
            if target.stat().st_size > MAX_FILE_BYTES:
                continue
            try:
                lines = target.read_text(encoding="utf-8").splitlines()
            except (UnicodeError, OSError):
                continue
            for number, line in enumerate(lines, 1):
                if query.casefold() in line.casefold():
                    matches.append({"path": str(target.relative_to(self.root)),
                                    "line": number, "text": line[:300]})
                    if len(matches) >= 50:
                        return {"matches": matches, "truncated": True}
        return {"matches": matches, "truncated": False}

    def write_file(self, path: str, content: str) -> dict:
        target = self.safe_path(path)
        if not isinstance(content, str) or len(content.encode("utf-8")) > MAX_FILE_BYTES:
            raise ValueError("내용은 100KB 이하 문자열이어야 합니다.")
        if target.exists() and not target.is_file():
            raise ValueError("일반 파일만 저장할 수 있습니다.")
        # 승인 전후 두 번 검사: 승인 대기 중 경로가 변경되었을 수 있습니다.
        import difflib
        previous = target.read_text(encoding="utf-8") if target.exists() else ""
        diff = "".join(difflib.unified_diff(previous.splitlines(True), content.splitlines(True),
                                             fromfile=path + " (현재)", tofile=path + " (제안)"))
        if not self.approve(f"파일 저장: {path}\n{clip(diff)}"):
            return {"ok": False, "error": "사용자가 파일 저장을 거절했습니다."}
        target = self.safe_path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return {"path": path, "bytes": len(content.encode("utf-8"))}

    def run_tests(self) -> dict:
        tests = self.safe_path("tests")
        if not tests.is_dir():
            raise ValueError("작업 폴더에 tests 폴더가 없습니다.")
        # unittest가 링크를 통해 외부 Python 파일을 불러오지 않도록 검사합니다.
        for folder, dirs, names in os.walk(self.root, followlinks=False):
            for name in dirs + names:
                if (Path(folder) / name).is_symlink():
                    raise ValueError("테스트 실행 전 작업 폴더의 심볼릭 링크를 제거하세요.")
        command = [sys.executable, "-I", "-m", "unittest", "discover", "-s", "tests", "-v"]
        if not self.approve("Python 테스트 실행: " + " ".join(command)
                            + "\n테스트와 import한 코드는 현재 사용자 권한으로 실행됩니다. 신뢰하는 코드만 승인하세요."):
            return {"ok": False, "error": "사용자가 테스트 실행을 거절했습니다."}
        # API 키를 자식 프로세스에 그대로 전달하지 않습니다. OS 파일 접근까지 격리하지는 않습니다.
        env = {key: value for key, value in os.environ.items()
               if key in {"PATH", "HOME", "USERPROFILE", "SYSTEMROOT", "TMPDIR", "TEMP", "LANG"}}
        process = subprocess.Popen(command, cwd=self.root, env=env, stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT, text=True, encoding="utf-8", errors="replace",
                                   start_new_session=(os.name != "nt"))
        try:
            output, _ = process.communicate(timeout=20)
        except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            output, _ = process.communicate()
            if isinstance(error, KeyboardInterrupt):
                raise
            return {"ok": False, "error": "테스트 실행 20초 제한 초과", "output": clip(output)}
        return {"ok": process.returncode == 0, "exit_code": process.returncode, "output": clip(output)}

    def execute(self, name: str, arguments: str) -> dict:
        """모델의 JSON을 Python 인수로 바꾸되 모든 실패를 도구 결과로 돌려줍니다."""
        try:
            if name not in self.registry:
                raise ValueError(f"등록되지 않은 도구: {name}")
            args = json.loads(arguments)
            if not isinstance(args, dict):
                raise ValueError("인수는 JSON 객체여야 합니다.")
            result = self.registry[name](**args)
            return {"ok": True, **result}
        except (ValueError, TypeError, OSError, UnicodeError) as error:
            return {"ok": False, "error": str(error)}
