## 실행 엔진과 웹 질문 연결

제공된 PRD로 앱을 만들 때 **Claude Agent SDK, Claude CLI, OpenCode CLI 중 사용할 수 있는 하나를 선택**하세요. 세 가지를 모두 설치하거나 연결할 필요는 없습니다. 학생이 만든 웹앱은 주제와 자료를 받고 진행 상황과 질문을 보여주며, 선택한 실행 엔진은 자료 조사와 카드 원고 작성을 담당합니다.

개발 도우미에게 다음처럼 요청하세요. 대괄호에는 선택한 엔진 이름을 넣습니다. 아래에서 **세션**은 앞선 대화와 작업 맥락을 이어가기 위한 단위이며, 세션 ID는 이를 구분하는 값입니다.

> 실행 엔진을 [Claude Agent SDK]로 연결해 줘. 필요한 질문은 웹에서 답하고 같은 제작을 이어 가게 해 줘. 엔진 호출 부분은 분리해 줘.

여기서 엔진 연결은 웹 서버가 SDK를 사용하거나 설치된 CLI를 실행하는 기능입니다. 브라우저가 직접 명령을 실행하는 구조는 아닙니다. 개발 도우미가 구현한 결과를 실행한 뒤, **‘AI 최신소식 카드뉴스를 만들어 줘’라는 요청이 실제 선택한 엔진으로 전달되는지** 확인합니다.

| 선택 | 앱에서 연결하는 방식 | 앞선 대화를 이어가는 방식 |
|---|---|---|
| Claude Agent SDK | 코드에서 `query()`를 호출하고 메시지를 받습니다. | 저장한 세션 ID를 `options.resume`에 전달합니다. |
| Claude CLI | 서버에서 `claude -p`를 실행하고 JSON 결과를 읽습니다. | `--resume`에 해당 프로젝트의 세션 ID를 전달합니다. |
| OpenCode CLI | 서버에서 `opencode run --format json`을 실행하고 JSON 이벤트를 읽습니다. | `--session`에 해당 프로젝트의 세션 ID를 전달합니다. |

SDK는 앱 안에서 질문 처리를 연결하기 좋고, CLI는 익숙한 실행 도구를 서버에서 호출하는 방법입니다. CLI의 `--continue`는 최근 세션을 고르므로, 프로젝트가 여러 개라면 앱이 저장한 명시적 ID로 이어가도록 합니다. [Claude SDK 세션](https://code.claude.com/docs/en/agent-sdk/sessions), [Claude CLI](https://code.claude.com/docs/en/headless), [OpenCode CLI](https://opencode.ai/docs/cli/)

웹앱이 보관할 것은 엔진의 답변만이 아닙니다. **어느 제작인지, 무엇을 기다리는지, 사용자가 무엇을 골랐는지**도 저장해야 합니다. 예를 들어 앱은 카드뉴스 프로젝트와 제작 실행의 ID, 선택한 엔진과 세션 ID, 현재 단계, 질문과 선택지, 사용자 답변, 지금까지 만든 원고를 보관합니다. 브라우저를 새로고침해도 대기 중인 질문을 다시 보여줄 수 있어야 합니다.

> 질문이 생기면 제작 상태를 `waiting_for_user`으로 저장하고, 질문과 선택지를 웹 화면에 보여줘. 답변 전에는 다음 제작 단계로 넘어가지 마. 답변을 제출하면 같은 제작 실행의 상태를 다시 `running`으로 바꾸고, 저장된 답변과 엔진 세션을 연결해서 이어 가게 해 줘. 새로고침해도 질문이 남고, 제출 버튼을 두 번 눌러도 중복 실행되지 않는지 확인해 줘.

아래 연결 방식과 명령은 코딩 에이전트가 구현할 때 확인하는 참고입니다. 학생은 선택한 엔진의 질문이 화면에 나타나고 답변 후 이어지는지를 중심으로 검증하세요. SDK와 CLI가 질문을 전달하는 방법은 구분해서 구현합니다. **Claude SDK의 `AskUserQuestion`**을 사용하면 `canUseTool` 콜백에서 질문을 받아 웹으로 보낼 수 있습니다. 사용자가 고른 답을 콜백에 반환하면 대기하던 SDK 실행이 이어집니다. 웹 화면과 답변 저장 기능은 여전히 앱이 만들어야 합니다. [Claude SDK 사용자 입력](https://code.claude.com/docs/en/agent-sdk/user-input)

**CLI로 시작할 때는 앱이 정한 `need_input` 규약**을 사용할 수 있습니다. 필요한 정보가 없으면 엔진이 ‘질문이 필요하다’는 구조화된 결과를 반환하고, 앱이 이를 검증해 질문 화면으로 바꾸는 방식입니다. 다음은 공식 CLI 필드가 아니라 이번 앱에서 정할 수 있는 예시입니다.

```json
{
  "status": "need_input",
  "question_id": "audience",
  "question": "누구를 위한 카드뉴스인가요?",
  "options": ["AI 입문자", "업무에 AI를 쓰는 사람"],
  "multi_select": false
}
```

이 결과를 받은 CLI 프로세스는 종료될 수 있습니다. 웹앱은 제작을 완료로 표시하지 않고 `waiting_for_user`으로 남깁니다. 사용자가 답하면 저장한 세션 ID로 다음 CLI 호출을 시작하면서 질문과 답변을 함께 전달합니다. **같은 제작과 대화를 이어가는 것이며, 같은 운영체제 프로세스가 계속 살아 있다는 뜻은 아닙니다.**

> CLI 출력 형식을 검사하고, 질문과 답변을 해당 세션의 다음 입력으로 전달해 줘. 잘못된 출력을 완료로 처리하지 마.

Claude CLI의 JSON 결과는 `session_id`를 사용합니다. OpenCode의 JSON 출력은 줄마다 이벤트가 나오며 세션 필드는 `sessionID`, 텍스트 이벤트의 내용은 `part.text`입니다. 따라서 두 엔진의 원시 출력을 같은 JSON이라고 가정하면 안 됩니다. 앱은 엔진 출력에서 필요한 내용을 꺼낸 다음 자신이 정한 `need_input` 또는 완료 결과로 해석합니다. [Claude JSON 출력](https://code.claude.com/docs/en/headless), [OpenCode 공식 실행 소스](https://github.com/anomalyco/opencode/blob/dev/packages/opencode/src/cli/cmd/run.ts)

<details><summary>필요할 때 확인하는 실행 명령</summary>

아래는 앱의 서버에서 사용할 명령 형태입니다. 학생이 매번 직접 실행할 필요는 없습니다. `$SESSION_ID`에는 앱이 해당 엔진에서 받은 실제 값을 넣습니다.

```bash
# Claude CLI: 시작과 후속 답변
claude -p "카드뉴스 제작 요청" --output-format json
claude -p "질문: 독자 / 사용자 답변: AI 입문자" --resume "$SESSION_ID" --output-format json

# OpenCode CLI: 시작과 후속 답변
opencode run --format json "카드뉴스 제작 요청"
opencode run --session "$SESSION_ID" --format json "질문: 독자 / 사용자 답변: AI 입문자"
```

</details>

연결 후에는 독자 선택을 요구하는 요청으로 **질문 표시 → 대기 → 답변 → 제작 계속**을 확인하세요. 다른 프로젝트를 하나 더 열어 질문과 세션이 섞이지 않는지도 봅니다. 엔진 교체 시 기존 세션 ID는 다른 엔진에서 사용할 수 없으므로, 앱에 저장한 자료·선택·현재 원고를 새 엔진의 시작 맥락으로 전달하도록 요청합니다.

Antigravity는 이 실행 엔진 선택과 별도로 이미지 작업을 맡기는 작업자로 연결합니다. 이 단계에서는 이미지 요청과 돌아온 이미지 파일을 카드에 연결할 자리를 마련하고, 자세한 이미지 제작 과정은 다음 단계에서 다룹니다.
