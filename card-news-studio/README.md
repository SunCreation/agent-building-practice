# Card News Studio

자료에서 카드뉴스 원고를 만들고, Python으로 PNG와 PDF를 출력하는 로컬 학습 프로젝트입니다. Claude Agent SDK 또는 OpenCode CLI를 제작 엔진으로 연결합니다. 모델의 대화 세션과 앱의 제작 프로젝트를 별도로 보관하며, 선택한 카드만 수정합니다.

## 시작하기

AI 개발 도우미에게 짧게 요청할 수 있습니다.

> 이 프로젝트 README대로 실행해 줘. 먼저 예제 엔진으로 카드 이미지가 나오는지 확인하자.

<details><summary>수동 설치와 실행</summary>

Python과 uv를 준비한 뒤 이 폴더에서 실행합니다.

```sh
uv sync --locked
uv run playwright install chromium
uv run python app.py --host 127.0.0.1 --port 8765
```

브라우저에서 `http://127.0.0.1:8765`에 접속합니다. Linux에서 Chromium 시스템 라이브러리가 부족하면 운영체제 안내에 따라 `uv run playwright install --with-deps chromium`을 사용합니다. 한국어 글꼴은 OS의 Apple SD Gothic Neo / Malgun Gothic / Noto Sans CJK KR을 사용합니다. Linux에서는 Noto CJK 글꼴을 설치해야 한글이 정상적으로 나옵니다. 원격 웹 글꼴을 내려받지 않습니다.

화면 없이 예제 이미지 생성:

```sh
uv run python app.py --demo
```

</details>

## 예제 엔진과 실제 엔진

**예제 엔진은 모델을 호출하지 않습니다.** 가상의 도서 교환 행사 자료로 준비된 네 장을 렌더링합니다. 사용자가 다른 자료나 카드 수를 지정하면 거부합니다. 수정 요청의 의미를 해석하지 않고 선택 카드의 제목을 정해진 문구로 바꿉니다. 이미지 출력·프로젝트 저장·부분 수정 범위·UI 연결을 확인하기 위한 명시적 fixture입니다.

실제 엔진에서는 자신의 주제와 자료를 넣고 3~8장을 선택할 수 있습니다. 자료가 해당 모델 제공자에게 전달되므로 공개하거나 전송할 수 있는 자료를 사용합니다. 결과 JSON이 틀리면 오류로 종료하며 예제 원고로 대체하지 않습니다. 원고 생성 전 실패·취소했다면 원인을 확인한 뒤 기존 프로젝트의 **원고 생성 재시도** 버튼으로 다시 실행할 수 있습니다. 원고가 있는 프로젝트에는 이 버튼을 비활성화합니다. 원고는 저장됐지만 렌더가 실패할 수도 있습니다. 화면의 원고 제목과 실행 기록을 확인하고 문제 카드를 수정한 다음 다시 렌더합니다.

### Claude Agent SDK

`claude-agent-sdk`는 uv로 설치됩니다. [공식 빠른 시작](https://code.claude.com/docs/en/agent-sdk/quickstart)에 따라 SDK에서 지원하는 인증을 준비하세요. SDK 인증·요금은 개발 도우미의 로그인/구독과 동일하다고 가정하면 안 됩니다. API 키를 사용하는 경우 서버를 실행할 터미널의 `ANTHROPIC_API_KEY` 환경 변수로 설정하고 코드나 자료에 넣지 않습니다. 필요하면 `STUDIO_CLAUDE_MODEL`로 사용할 모델을 지정합니다.

`sdk_worker.py`에서 `ClaudeSDKClient`를 사용합니다. 새 프로젝트에서는 `resume=None`, 후속 수정에서는 저장된 세션 ID를 `resume`에 전달합니다. `ResultMessage.session_id`와 결과 텍스트를 부모 프로세스에 JSON으로 전달합니다. SDK 실행을 자식 프로세스로 분리했으므로 앱은 실제 실행 프로세스를 종료할 수 있습니다.

### OpenCode CLI

[공식 설치 안내](https://opencode.ai/docs/)에 따라 OpenCode를 설치하고, 서버를 실행할 사용자로 `opencode auth login`을 진행해 사용할 제공자를 연결합니다. `opencode models`에서 실제 이용 가능한 모델 ID를 확인합니다. 선택하려면 `STUDIO_OPENCODE_MODEL` 환경 변수에 `provider/model` 형식으로 지정합니다. OpenCode 인증이 OpenAI 키를 자동으로 사용한다고 가정하지 않습니다.

엔진 어댑터가 생성하는 명령의 형태는 다음과 같습니다. 사용자가 이 긴 명령을 매번 입력할 필요는 없습니다.

```sh
opencode run --pure --format json --agent AGENT_NAME -- "제작 요청"
opencode run --pure --format json --agent AGENT_NAME --session ses_example -- "후속 수정 요청"
```

`AGENT_NAME`은 어댑터가 매 호출마다 생성하는 전용 에이전트 이름의 자리 표시자입니다. 앱이 이 에이전트의 도구 권한을 모두 거부하도록 설정한 뒤 이름을 전달합니다. 일반 build 에이전트의 사용자 권한을 물려받지 않게 하기 위한 구성입니다. 위 명령은 어댑터 구조 설명이며 그대로 복사해 실행하는 명령이 아닙니다. `ses_example`은 예시입니다. 실제 ID는 첫 실행의 JSON 이벤트 `sessionID`에서 얻어 프로젝트에 저장합니다. `--continue`로 임의의 최근 세션을 선택하지 않습니다. `--pure`는 외부 플러그인을 제외하는 옵션이며, 확인한 OpenCode 버전은 1.18.29입니다. 출력 이벤트에서 `type=text`의 `part.text`를 모읍니다. 재개나 인증 실패 시 새 세션으로 몰래 다시 실행하지 않습니다.

## 제작 과정과 파일

1. 주제·독자·근거 자료·카드 수·실행 엔진을 입력합니다.
2. 앱이 프로젝트 ID를 발급하고 `project.json`을 저장합니다.
3. 선택 엔진이 출처 ID를 포함한 카드 원고 JSON을 반환합니다.
4. Python이 카드 수·ID·필드·길이·출처 연결을 검증합니다.
5. 고정 HTML/CSS 템플릿에 원고를 **이스케이프**해 넣고 Chromium으로 PNG/PDF를 출력합니다.
6. 카드를 선택해 수정하면 엔진에는 전체 문맥과 선택 카드 ID를 보내지만, 반환값은 카드 객체 하나만 받습니다. 앱이 그 카드만 교체하므로 다른 카드 JSON은 유지됩니다.

```text
workspace/<project-id>/
  project.json          # engine, session_id, topic, audience, sources, card_count
  storyboard.json       # title, audience, sources, cards
  last-job.json         # 마지막 작업 상태와 이벤트
  engine-request.json   # Claude worker 입력 (실제엔진 사용시)
  exports/
    c1.png ... cN.png
    cards.pdf
    cards.html
    storyboard.json
```

폴더 전체에 대화·자료가 들어갈 수 있어 `.gitignore`로 제외했습니다. 프로젝트 ID와 엔진의 세션 ID는 서로 다릅니다. 엔진은 프로젝트 생성 후 바꾸지 않습니다. 새 엔진을 비교하려면 새 프로젝트를 만드세요. 앱 재시작 후 프로젝트·원고·세션은 남지만 메모리의 현재 작업 목록은 초기화됩니다. 이 예제는 단일 프로세스·단일 사용자용이며 다중 Uvicorn worker로 실행하지 않습니다.

`skills/card-news-editor/SKILL.md`와 `references/editorial-decisions.md`가 있으면 앱이 파일을 읽어 제작 프롬프트 앞에 명시적으로 붙입니다. SDK나 OpenCode의 Skill 자동 발견을 사용한 것은 아닙니다. Skill을 수정해도 렌더러의 길이·출처·카드 범위 검증은 그대로 작동합니다.

## 실행 권한과 중단

이번 제작 단계는 텍스트 JSON을 받으면 충분해서 Claude SDK에는 `tools=[]`, `mcp_servers={}`, `strict_mcp_config=True`를 지정합니다. OpenCode에는 전용 에이전트를 만들고 그 에이전트의 `permission: {"*":"deny"}`를 지정한 뒤 `--agent`로 선택합니다. HTML 파일 작성과 브라우저 렌더는 Python의 고정 코드가 수행합니다. 모델이 반환한 HTML이나 명령을 실행하지 않습니다. 이것은 운영체제의 격리 환경을 보장하는 설정은 아닙니다. 도구를 추가할 때 인증·권한·프로세스 실행 범위를 다시 설계해야 합니다.

웹의 취소 버튼은 단순히 진행 표시만 숨기지 않습니다. 비동기 작업을 취소하고 Unix에서는 엔진 프로세스 그룹에 종료 신호를 보냅니다. 3초 안에 끝나지 않으면 강제 종료합니다. Windows에서는 직접 자식 프로세스를 종료하므로 손자 프로세스 전체 종료는 보장하지 않습니다. 이미 외부 모델 서버로 전달된 요청의 처리·과금이나 완료된 파일 변경을 되돌리는 기능은 아닙니다. 작업 전체 제한은 300초입니다.

## Tailscale로 다른 내 기기에서 접속

AI에게 다음처럼 요청합니다.

> 이 앱을 Tailscale Serve로 내 기기에서만 접속하게 설정해 줘. 접속 토큰도 사용하자.

<details><summary>수동 설정</summary>

두 기기에 Tailscale을 설치하고 같은 tailnet에 연결합니다. Serve가 필요한 HTTPS 설정은 [공식 안내](https://tailscale.com/docs/features/tailscale-serve)를 따릅니다. 서버 터미널에서 충분히 긴 임의 토큰을 설정하고 앱을 실행합니다. 아래 방식은 터미널 기록에 토큰 값을 직접 쓰지 않습니다.

```sh
export STUDIO_TOKEN="$(uv run python -c 'import secrets; print(secrets.token_urlsafe(32))')"
uv run python app.py --host 127.0.0.1 --port 8765
```

다른 터미널에서:

```sh
tailscale serve status
# 기존 Serve 설정이 없는 실습용 기기에서 실행합니다.
tailscale serve --bg http://127.0.0.1:8765
tailscale serve status
```

발급된 `https://기기이름.tailnet이름.ts.net` 주소를 사용합니다. 설정한 토큰을 확인해서 접속 화면 오른쪽에 입력합니다. 토큰은 해당 브라우저 탭의 sessionStorage에만 저장합니다. 주소나 스크린샷에 포함하지 않습니다. 이 실습만을 위해 구성한 기기는 `tailscale serve reset`으로 Serve 구성을 해제할 수 있습니다. 기존 서비스가 함께 있으면 전체 설정을 초기화하지 말고 해당 경로의 설정만 해제합니다. 앱도 종료하려면 서버 터미널에서 Ctrl+C를 누릅니다.

</details>

**Serve는 공개 인터넷 배포가 아닙니다.** tailnet의 접근 정책과 앱의 토큰이 각각 접속을 제어합니다. Funnel은 이 실습에 사용하지 않습니다. 앱은 허용된 Host와 요청 Origin, 변경 요청 헤더를 확인합니다. 외부 공개 서비스용 사용자 계정·속도 제한·감사 시스템은 구현하지 않았습니다.

## 검증과 한계

```sh
uv run python -m unittest -v test_studio
uv run python app.py --demo
```

단위 테스트는 출처 위조·선택 범위·HTML 삽입·Origin·실제 로컬 프로세스 종료 등을 검사합니다. OpenCode 이벤트 테스트는 **직접 만든 테스트 이벤트**이며 실제 제공자 연결의 증거가 아닙니다. 예제 렌더 성공도 실제 모델 인증 성공을 의미하지 않습니다. 실제 엔진은 인증 후 별도로 새 제작과 동일 세션 후속 수정을 확인하세요.

출처 ID 검증은 존재하는 자료에 연결되었는지만 확인합니다. 문장의 사실성·공정성·저작권을 자동 판정하지 않습니다. 숫자나 일정이 없는 자료에서 모델이 이를 지어내지 않았는지 사람이 검수합니다. 기본 디자인은 타이포그래피와 색면이며 생성형 이미지 서비스를 호출하지 않습니다. 제목이 배정된 영역을 넘거나 본문이 하단 출처 영역과 겹치면 렌더 실패로 알려 줍니다.

## 공식 참고

- [Claude Agent SDK Python](https://code.claude.com/docs/en/agent-sdk/python)
- [Claude Agent SDK 세션](https://code.claude.com/docs/en/agent-sdk/sessions)
- [OpenCode CLI](https://opencode.ai/docs/cli/)
- [OpenCode 권한](https://opencode.ai/docs/permissions/)
- [Playwright Python 스크린샷](https://playwright.dev/python/docs/screenshots)
- [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve)

`uv run python check_layout.py`로 모델 호출 없이 구조 검사와 실제 배치 검사의 실패를 비교할 수 있습니다. 기존 프로젝트 원고를 바꾸지 않는 가상 복사본 실습입니다.
