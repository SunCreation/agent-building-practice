"""완성본을 보존하며 핵심 연결부 두 곳을 직접 구현할 작업본을 만듭니다."""
import argparse
import ast
from pathlib import Path
import shutil


def replace_body(path: Path, function_name: str, message: str) -> None:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(n for n in ast.walk(tree)
                    if isinstance(n, ast.FunctionDef) and n.name == function_name)
    lines = source.splitlines(keepends=True)
    start = function.body[0].lineno - 1
    indent = " " * (function.col_offset + 4)
    lines[start:function.end_lineno] = [
        indent + "# 교안과 완성본을 참고해 이 연결부를 직접 구현하세요.\n",
        indent + f"raise NotImplementedError({message!r})\n",
    ]
    path.write_text("".join(lines), encoding="utf-8")


def prepare(target: Path) -> None:
    source = Path(__file__).resolve().parent
    target = target.resolve()
    if target.exists():
        raise ValueError("대상 폴더가 이미 있습니다. 새 폴더 이름을 지정하세요.")
    target.mkdir(parents=True)
    for name in ("harness.py", "core.py", "tools.py", "README.md", "pyproject.toml",
                 "uv.lock", ".python-version", ".gitignore"):
        shutil.copy2(source / name, target / name)
    for name in ("examples", "tests"):
        shutil.copytree(source / name, target / name,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    shutil.copytree(source / "examples/general", target / "workspace")
    replace_body(target / "tools.py", "execute", "WorkspaceTools.execute를 구현하세요: 인수 파싱 → 등록 함수 실행 → 결과 또는 오류 반환")
    replace_body(target / "core.py", "run_agent", "run_agent를 구현하세요: 모델 호출 → 도구 요청 처리 → 결과 전달 → 반복과 종료")
    (target / "EXERCISE.md").write_text("""# 직접 구현할 작업본

이 폴더는 완성본과 별개입니다. 경로 검사·승인·도구 함수·세션 저장은 제공하고,
핵심 연결부 `WorkspaceTools.execute`와 `core.run_agent`는 비워 두었습니다.
두 함수는 미구현 오류를 내도록 되어 있으며 가짜 성공 응답을 반환하지 않습니다.

1. 원래 완성본에서 대화·도구의 입력과 출력을 관찰합니다.
2. 이 폴더에서 `uv sync --locked`로 실행 환경을 준비합니다.
3. 도구 호출과 결과 반환을 배운 뒤 `WorkspaceTools.execute`를 구현합니다.
4. 반복·오류·종료를 배운 뒤 `core.run_agent`를 구현합니다.
5. `uv run python -m unittest discover -s tests -v`로 검사합니다.
6. 제공된 예제 자료를 복사해 둔 workspace와 새 세션으로 실제 모델을 연결합니다.
   코딩 작업에는 교안 안내에 따라 별도 작업 폴더를 준비합니다.

구현 전 테스트가 실패하는 것은 빈 연결부 때문입니다. 통과시키기 위해 테스트를 지우거나
고정 정답을 반환하지 마세요. 모델 실행 결과와 오프라인 테스트는 구분해 기록합니다.
""", encoding="utf-8")
    print(f"작업본 생성: {target}")
    print("EXERCISE.md를 읽고 두 연결부를 구현하세요. 원래 완성본은 변경하지 않았습니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target", type=Path)
    args = parser.parse_args()
    prepare(args.target)
