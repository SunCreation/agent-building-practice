# 에이전트 제작 실습 자료

PRD로 자신의 하네스를 만들거나, 기존 SDK·CLI를 연결해 사용자와 함께 동작하는 워크플로를 만듭니다.

- [Harness Design Kit](harness-design-kit/README.md): 하네스 요구사항, 설계 질문, 언어·플랫폼 선택과 검증 시나리오.
- [Python Harness Lab](harness-lab/README.md): Python 하네스 참고 구현. 최신 실습용 채점기는 [Agent Terminal Benchmark](https://github.com/SunCreation/agent-terminal-benchmark)를 사용합니다.
- [Card News Agent](card-news-agent/README.md): 실제 실행 구현. Claude CLI의 웹 조사, 브라우저 후보·독자 선택, Antigravity 생성, PNG·ZIP과 부분 수정. [실제 검증 기록](card-news-agent/VALIDATION.md).
- [Card News Workflow Kit](card-news-workflow-kit/README.md): **Day39 시작 자료**. 주제 조사 → 브라우저 후보 선택 → 독자·후킹 판단 → Antigravity 이미지 생성 → 검수·내보내기를 구현할 전체 PRD와 확인 목록입니다. 완성 앱이 아닌 설계 키트입니다.
- [이전 Card News Studio](card-news-studio/README.md): 자료를 직접 제공하는 이전 예제입니다. 새 Day39의 검색·선택·Antigravity 워크플로 구현본이 아닙니다.

[Day39 설계 키트 ZIP](https://github.com/SunCreation/agent-building-practice/releases/download/v5.0.0/card-news-workflow-kit.zip)을 내려받아 README의 짧은 요청으로 시작하세요. 기존 하네스 자료는 [v4.0.0 릴리스](https://github.com/SunCreation/agent-building-practice/releases/tag/v4.0.0)에서도 받을 수 있습니다.

`assets`의 기존 화면과 카드 이미지는 가상 행사 자료를 사용하는 예제 엔진 출력입니다. 실제 AI 모델 연결 결과와 구분합니다. 인증 정보는 배포물에 포함하지 않습니다.
