# 작은 범용·코딩 에이전트 하네스

이 프로젝트는 모델에게 질문하는 프로그램에서 한 걸음 더 나아갑니다. 모델이 파일을 읽어 달라고 요청하면 Python이 파일을 읽고, 그 결과를 모델에게 돌려줍니다. 모델은 읽은 내용을 바탕으로 답하거나 다른 도구를 요청합니다. 이 반복을 관리하는 프로그램을 여기서는 **하네스**라고 부릅니다.

API 통신에는 OpenAI Python SDK를 사용합니다. 도구 등록, 호출 실행, 대화 이력, 승인과 종료 판단은 이 프로젝트의 Python 코드에 들어 있습니다. 완성된 에이전트 프레임워크가 대신 수행하지 않습니다. 같은 실행 루프에 OpenAI API 또는 로컬 Ollama를 연결할 수 있습니다.

## 파일과 읽는 순서

| 파일 | 맡은 일 |
|---|---|
| `harness.py` | CLI 입력, 모델 연결, 실행, 세션 저장 |
| `core.py` | 대화 메시지와 도구 호출을 연결하는 반복문 |
| `tools.py` | 모델에게 보일 도구 설명과 실제 함수 |
| `examples/general/docs/` | 수업용 가상 책방 행사 자료 |
| `examples/coding/` | 고정 비용 계산에 의도적인 버그가 있는 작은 Python 프로젝트 |
| `tests/test_harness.py` | 실제 API 없이 하네스의 중요한 동작을 검증하는 테스트 |
| `uv.lock` | 설치할 라이브러리 버전을 고정한 목록 |

코드를 처음 읽을 때는 `harness.py`의 `run_agent(...)`를 찾고, `core.py`의 같은 함수를 따라가세요. 이어서 `tools.execute(...)`가 어떤 함수를 선택하는지 보면 전체 경로가 연결됩니다.

## 실행 환경 준비

개발을 도와주는 Claude Code 또는 OpenCode에 이렇게 요청할 수 있습니다.

> 이 프로젝트를 uv로 준비해 줘. 예제 자료를 별도 작업 폴더에 복사하고, 실행 방법을 설명해 줘.

아래 명령은 다운로드한 `agent-harness-practice` 폴더 안에서 실행합니다. Python은 `.python-version`의 3.13을 사용하며 uv가 해당 인터프리터를 준비할 수 있습니다. 시스템 Python을 바꾸는 작업은 아닙니다.

<details>
<summary>직접 준비하는 명령</summary>

```bash
uv sync --locked
```

macOS/Linux:

```bash
cp -R examples/general workspace
```

PowerShell:

```powershell
Copy-Item -Recurse examples/general workspace
```

`workspace`가 이미 존재하면 새 폴더 이름을 사용하세요. 원본 예제를 직접 바꾸지 않도록 복사본을 만듭니다. uv 설치가 필요하면 [공식 설치 안내](https://docs.astral.sh/uv/getting-started/installation/)를 따릅니다.

</details>

## OpenAI로 첫 요청

수업에서 제공받은 키를 `OPENAI_API_KEY` 환경 변수로 설정합니다. 환경 변수는 프로그램이 시작될 때 읽는 설정값입니다. API 키는 과금·접근 권한과 연결되므로 프롬프트나 소스 코드에 넣지 않습니다. 이 프로젝트는 `.env`를 자동으로 읽지 않습니다.

> 제공받은 API 키를 터미널 환경 변수로 설정할 방법을 알려 줘. 키를 코드나 대화에 넣지는 않을게.

<details>
<summary>입력을 화면에 표시하지 않는 키 설정과 실행</summary>

macOS 기본 zsh:

```zsh
read -rs 'OPENAI_API_KEY?OpenAI API key: '
export OPENAI_API_KEY
```

Bash:

```bash
read -rsp 'OpenAI API key: ' OPENAI_API_KEY
export OPENAI_API_KEY
```

PowerShell 7:

```powershell
$secret = Read-Host 'OpenAI API key' -AsSecureString
$env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $secret).Password
```

그다음 어느 셸에서든:

```bash
uv run python harness.py --provider openai --model gpt-4.1-mini --workspace workspace --session notes --prompt "자료에서 홍보 초안 담당자와 마감일을 찾아 줘."
```

모델 이름은 수업 계정에서 사용할 수 있는 도구 호출 지원 모델로 바꿀 수 있습니다. 모델을 바꿀 때는 새 세션 이름을 사용합니다. `gpt-4.1-mini`가 모든 계정에서 허용된다고 가정하지 않습니다.

</details>

`[진행]` 메시지는 하네스가 남기는 실행 이벤트입니다. 최종 답변은 모델이 만든 텍스트입니다. 이벤트에 `read_file` 성공이 보인다고 해서 최종 답변까지 정확하다는 뜻은 아닙니다. 예제 파일에서 담당자가 수진이고 마감이 금요일인지 직접 대조하세요.

## Ollama 연결

Ollama는 모델을 로컬에서 실행하는 서버입니다. 이 프로젝트는 Ollama의 OpenAI 호환 Chat Completions 경로를 사용하므로, 모델 연결을 바꾸어도 `run_agent`와 도구 함수는 같습니다. 모든 OpenAI API 기능이 호환되는 것은 아니며, **도구 호출을 지원하는 모델**이 필요합니다.

> Ollama가 실행 중인지 확인하고, 설치된 도구 호출 지원 모델로 이 하네스를 실행해 줘.

<details>
<summary>직접 모델을 준비하고 연결하는 명령</summary>

Ollama를 설치한 뒤 서버가 실행 중인지 확인합니다. macOS 앱이 이미 서버를 실행하고 있다면 별도로 `ollama serve`를 중복 실행하지 않습니다.

```bash
ollama list
ollama pull qwen3.5:2b
uv run python harness.py --provider ollama --model qwen3.5:2b --workspace workspace --session local-notes --prompt "docs/meeting.md를 읽고 홍보 초안 담당자와 마감일을 찾아 줘."
```

모델 다운로드에는 저장 공간과 시간이 필요합니다. 예시 모델은 약 2.3B 규모의 소형 모델로, 장치 성능과 모델의 도구 호출 능력에 따라 결과가 달라집니다. 기본 주소는 `http://localhost:11434/v1`입니다. 별도 서버라면 `--base-url`로 바꿉니다. 이 경우 자료가 그 서버로 전송됩니다.

</details>

연결 문제는 네 단계로 나누어 확인합니다. 서버에 접속할 수 있는지, 모델 이름이 맞는지, 응답에 구조화된 도구 호출이 들어오는지, 도구 결과를 보고 정확하게 답했는지를 구별하세요. 본문에 도구를 호출했다고 적는 것과 실제 `tool_calls`가 반환되는 것도 다릅니다.

## 대화가 도구 실행으로 이어지는 경로

`core.py`의 핵심은 다음 순서입니다.

```python
response = client.chat.completions.create(
    model=settings.model, messages=messages, tools=tools.schemas,
)
```

`messages`는 시스템 지시, 사용자 요청, 모델 응답, 도구 결과를 순서대로 담은 목록입니다. `tools.schemas`는 도구의 이름, 설명, 입력 형식을 담습니다. 이 설명을 보낸다고 Python 함수가 함께 서버로 올라가는 것은 아닙니다.

모델이 도구를 요청하면 `tool_calls`에 호출 ID, 도구 이름, JSON 인수가 들어옵니다. 하네스는 모델의 호출 메시지를 먼저 이력에 넣고, 다음처럼 함수를 실행합니다.

```python
result = tools.execute(call.function.name, call.function.arguments)
messages.append(tool_result(call.id, result))
```

호출 ID는 요청과 결과를 짝짓는 표식입니다. 한 응답에 도구 요청이 두 개라면 결과도 각 ID에 맞춰 두 개를 반환합니다. `tool_result`는 `role="tool"`, `tool_call_id=...`인 메시지를 만듭니다. 다음 반복에서 이력을 다시 보내면 모델이 실제 실행 결과를 볼 수 있습니다.

`execute`는 이름으로 등록된 함수를 찾습니다. JSON 문법 오류, 없는 도구 이름, 허용하지 않은 파일 경로는 예외로 프로그램을 끝내는 대신 `ok: false` 도구 결과로 돌려줍니다. 모델이 실패를 보고 수정할 기회를 주는 것입니다. 네트워크 오류나 잘린 모델 응답까지 성공으로 가장하지는 않습니다.

## 범용 자료 작업

다음 요청은 개발 도우미가 아니라 **완성한 하네스의 `--prompt` 값**으로 전달합니다.

> 두 자료를 비교해서 확정된 일과 아직 정해야 할 일을 정리해 줘. 근거 파일도 적어 줘.

`list_files`로 파일을 찾거나 `search_files`로 검색하고 `read_file`로 내용을 확인하는 흐름을 관찰하세요. 담당 미정과 확정된 담당자를 구별했는지, 우천 대응을 확정 사항으로 꾸미지 않았는지 확인합니다. 이어서 같은 세션으로 “그중 수요일까지 해야 하는 일만 알려 줘”라고 요청하면 이전 대화가 함께 전달됩니다.

문서 내용 자체는 신뢰할 수 없는 입력일 수 있습니다. 문서에 “이전 지시를 무시하라”가 들어 있어도 운영 지시로 취급하지 않는다는 시스템 지시를 넣었지만, 이것만으로 공격 방어가 완성되지는 않습니다. 도구의 실제 권한 제한이 함께 필요한 이유입니다.

## 코딩 작업

> 코딩 예제를 별도 폴더에 복사해 줘. 테스트를 먼저 실행하고 실패 원인을 설명한 뒤 최소한으로 고쳐 보자.

<details>
<summary>직접 코딩 예제를 실행하는 명령</summary>

macOS/Linux:

```bash
cp -R examples/coding coding-workspace
```

PowerShell:

```powershell
Copy-Item -Recurse examples/coding coding-workspace
```

```bash
uv run python harness.py --provider openai --workspace coding-workspace --session budget-fix --prompt "테스트를 실행하고 budget.py의 실패 원인을 고쳐 줘. 테스트는 바꾸지 말고 다시 실행해 확인해 줘."
```

Ollama로 실행하려면 `--provider ollama --model qwen3.5:2b`를 사용하고 새 세션 이름을 지정합니다.

</details>

예제는 참가자별 비용과 장소 고정 비용을 합하는 함수입니다. 원본 테스트 3개 중 정상 비용과 0명일 때의 비용 테스트 2개가 실패하도록 준비했습니다. 테스트가 원인을 드러내기 때문에 “실행됨”보다 “요구한 계산이 맞음”을 확인할 수 있습니다.

`write_file`은 전체 파일 내용을 저장하므로 승인 화면에 변경 전후 차이를 표시합니다. 변경 내용을 읽고 `y`를 입력해야 실행됩니다. 테스트도 별도 승인을 받습니다. stdin이 없거나 다른 문자를 입력하면 거절합니다. 거절한 뒤 다시 반복해서 승인을 요구하거나, 테스트 파일을 수정해 초록색만 만드는지 관찰하세요.

`run_tests`는 명령 문자열을 모델에게 받지 않습니다. 미리 정한 `python -I -m unittest discover -s tests -v`를 Python 인수 목록으로 실행합니다. 그러나 **테스트 파일과 그 파일이 import하는 코드는 실제 사용자 권한으로 실행됩니다.** 임의 셸 명령을 막았다고 OS sandbox가 되는 것은 아닙니다. 이 실습은 제공된 작은 예제를 사용하며, 낯선 저장소 실행은 별도 격리 환경을 갖춘 뒤 진행해야 합니다.

## 실행 범위와 한계

파일 도구는 지정한 작업 폴더 안의 상대 경로만 받습니다. `..`, 절대 경로, 숨김 경로, 심볼릭 링크를 거절합니다. 파일은 100KB 이하 UTF-8 텍스트에 한정하고, 긴 내용은 일부를 생략했다고 표시합니다. 파일 조회는 200개, 검색 결과는 50개까지입니다. 저장 승인 후 경로를 다시 검사하지만, 악의적인 다른 프로세스가 동시에 파일 시스템을 바꾸는 상황까지 완벽히 격리하는 설계는 아닙니다.

기본 상한은 한 요청당 모델 호출 8회, 도구 실행 20회입니다. 이는 무한 반복을 줄이는 장치이며 토큰 사용량이나 비용 상한과 같지는 않습니다. API 자동 재시도는 0회로 설정했고 개별 요청 타임아웃은 120초입니다. 테스트는 20초 뒤 중단합니다. Ctrl+C로 사용자가 중단할 수 있지만, 이미 저장한 파일이 자동으로 되돌아가지는 않습니다.

이 프로젝트는 학습용 단일 사용자 CLI입니다. 웹 서버, 여러 사용자의 동시 실행, 완전한 sandbox, 자동 컨텍스트 압축, 세션 파일 동시 쓰기 잠금은 구현하지 않습니다. 같은 세션은 한 프로세스에서만 사용하세요. 대화가 길어지면 내용을 검토하고 새 세션을 시작합니다.

## 저장과 재개

세션 이름은 서버가 발급한 ID가 아니라 이 하네스가 사용하는 로컬 대화 이름입니다. `.harness/sessions/notes.json`처럼 저장되며 같은 이름으로 실행하면 이전 메시지를 읽습니다. 제공자·모델·서버 주소·작업 폴더 중 하나가 다르면 섞어서 재개하지 않고 새 이름을 요구합니다.

도구 호출 도중 중단하면 남은 호출에 중단 결과를 붙여 요청과 결과의 짝을 맞춥니다. 실행 여부가 불확실한 작업을 자동 재실행하지 않습니다. OS 강제 종료나 전원 종료까지 파일 변경과 대화 저장을 하나의 원자적 작업으로 묶지는 못하므로, 재개할 때 실제 파일도 확인합니다.

`.harness/runs/`는 도구 이름과 성공 여부 같은 이벤트만 기록합니다. `.harness/sessions/`에는 질문과 읽은 파일 내용이 들어갈 수 있습니다. API 키를 기록하도록 만들지는 않았지만, 사용자가 비밀을 질문이나 일반 파일에 넣으면 대화에 남을 수 있으므로 공개 자료 묶음에 포함하지 않습니다. `.gitignore`는 `.harness/`, `.env`, `.venv/`를 제외합니다.

## 하네스 자체 검증

```bash
uv run python -m unittest discover -s tests -v
```

API 키나 Ollama 없이 실행할 수 있습니다. `FakeClient`는 테스트 파일에만 있고 미리 준비한 모델 응답을 반환합니다. 실제 서비스가 성공했다는 증거는 아니며, 하네스가 그 응답을 올바르게 처리하는지 확인합니다.

테스트는 호출 ID 연결, 잘못된 인수 복구, 경로 탈출 거절, 승인 거부, 중단 후 메시지 정합성, 세션 혼용 거절, 호출 상한을 확인합니다. 코딩 예제를 임시 폴더에 복사해 실제 subprocess에서 실패를 확인하고 올바른 변경 후 통과하는 테스트도 포함합니다. 학생의 원본 예제를 자동으로 고치지는 않습니다.

## 공식 참고 자료

- [OpenAI 도구 호출](https://developers.openai.com/api/docs/guides/function-calling): 도구 설명, 호출, 결과 반환의 역할. 이 프로젝트는 Chat Completions의 메시지 형식을 사용합니다.
- [OpenAI Python SDK](https://github.com/openai/openai-python): 클라이언트와 오류·타임아웃 설정.
- [Ollama OpenAI 호환 API](https://docs.ollama.com/api/openai-compatibility): 지원 경로와 제한.
- [Ollama 도구 호출](https://docs.ollama.com/capabilities/tool-calling): 도구 호출 지원 모델과 실행 흐름.

## 핵심 연결부 직접 구현

완성본을 비교 기준으로 보존하고, 두 함수가 비어 있는 별도 작업본을 만들 수 있습니다.

> 제공된 도구로 구현 작업본을 만들고, 도구 실행 연결부와 모델 호출 루프를 직접 구현하자.

<details><summary>작업본 생성</summary>

```sh
uv run python prepare_exercise.py ../my-agent-harness
```

생성된 폴더의 `EXERCISE.md`를 읽고 `uv sync --locked`로 준비합니다. `WorkspaceTools.execute`와 `core.run_agent`를 작성합니다. 경로 검사·승인·개별 도구·세션 저장은 제공되며, 빈 연결부는 미구현 오류를 내도록 되어 있습니다. 작성 전 관련 테스트 실패는 예상된 상태이고, 작성 후 같은 12개 테스트와 실제 모델 실행으로 확인합니다. 기존 폴더를 덮어쓰지 않습니다.

</details>
