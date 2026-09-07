# 내 에이전트 하네스 설계 키트
완성 소스를 복제하는 자료가 아니라, 제품 명세를 채우고 AI와 구현하는 출발점입니다. 하네스는 모델 호출, 도구 실행, 권한, 상태와 종료를 관리하는 프로그램입니다.

## 시작
압축을 풀고 이 README.md가 있는 폴더를 Claude Code 또는 OpenCode에서 엽니다. 다음처럼 요청합니다.

> 이 PRD를 읽고 같이 구체화하자. 결정이 필요한 것부터 물어봐 줘.

첫 응답에서 AI가 이미 정해진 목표와 미결정 사항을 구분하고 질문하는지 봅니다. 바로 코드를 만들거나 임의로 언어를 정하면 다음처럼 요청합니다.

> AGENTS.md와 PRD.md를 읽고, 내가 결정할 부분부터 질문해 줘.

답변은 짧게 해도 됩니다. AI가 문서를 고친 뒤 중요한 선택이 내 답과 맞는지 확인합니다. PRD 자체가 질문을 강제하는 기능은 아닙니다. 프로젝트 지침과 시작 요청으로 이 협업 방식을 안내합니다.

## 문서 역할
| 파일 | 읽거나 채우는 내용 |
|---|---|
| PRD.md | 사용자 문제, 제품 요구사항, 범위와 미결정 사항 |
| DECISIONS.md | 내가 선택한 내용과 이유, 미룬 사항 |
| INTERFACES.md | 입력·출력·오류·상태의 약속 |
| ACCEPTANCE.md | 완성 여부를 판정할 시나리오와 실제 증거 |
| IMPLEMENTATION_PLAN.md | 가장 작은 첫 연결부터 기능을 쌓는 순서 |
| AGENTS.md / CLAUDE.md | AI가 질문하고 합의를 기록하는 프로젝트 지침 |
| examples/spec-example.md | 모호한 문장을 검증 가능한 요구로 바꾸는 예 |

D01~D07의 최초 범위를 정하면 “합의한 명세로 첫 작업부터 구현해 줘”라고 요청합니다. 이후 범용 자료 작업과 코드 수정·테스트를 모두 검증합니다. D08/D09는 나중으로 미뤄도 됩니다. 파일 이름은 바꾸어도 되지만 지침의 참조도 함께 고쳐야 합니다.

## 지침을 읽는 방식
OpenCode는 프로젝트 AGENTS.md를 읽습니다. Claude Code는 CLAUDE.md에서 AGENTS.md를 가져오도록 구성했습니다. 제공 지침을 새로 생성할 필요는 없습니다. 전역 설정이나 도구 버전에 따라 동작이 달라질 수 있으므로 실제 첫 응답도 확인합니다.

- [OpenCode 프로젝트 규칙](https://opencode.ai/docs/rules/)
- [Claude Code 메모리와 파일 가져오기](https://code.claude.com/docs/en/memory)

[기존 Python 구현](https://github.com/SunCreation/agent-building-practice/tree/main/agent-harness-practice)은 설계를 비교하는 선택 참고 자료입니다. 그 프로젝트의 빈칸 코드 실습을 수행할 필요는 없습니다. 이 키트는 실행 프로그램이나 API 키를 포함하지 않습니다.
