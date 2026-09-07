"""원본 프로젝트를 바꾸지 않고 구조 검사와 실제 배치 검사를 비교합니다."""
import asyncio
import copy
import tempfile
from renderer import render
from storyboard import SAMPLE_SOURCE, demo_story, validate_story


async def main():
    project = {"topic": "배치 검사", "audience": "학생", "card_count": 4,
               "sources": [{"id": "s1", "title": "수업용 가상 자료", "text": SAMPLE_SOURCE}]}
    original = demo_story(project)
    too_long = copy.deepcopy(original)
    too_long["cards"][0]["headline"] = "가" * 46
    try:
        validate_story(too_long, project)
    except ValueError as error:
        print("[구조 검사에서 거부]", error)
    else:
        raise AssertionError("46자 제목은 구조 검사에서 거부되어야 합니다.")

    dense = copy.deepcopy(original)
    dense["cards"][0]["headline"] = "가" * 42
    dense["cards"][0]["body"] = "가" * 240
    validate_story(dense, project)
    print("[구조 검사 통과] 다음은 실제 브라우저 배치를 검사합니다.")
    try:
        with tempfile.TemporaryDirectory(prefix="card-layout-") as directory:
            await render(dense, directory)
    except ValueError as error:
        print("[배치 검사에서 거부]", error)
    else:
        print("[배치 검사 통과] 현재 글꼴·배치에서는 영역 안에 들어갔습니다.")
    print("실제 모델을 호출하지 않았으며 기존 프로젝트 원고를 변경하지 않았습니다.")


if __name__ == "__main__":
    asyncio.run(main())
