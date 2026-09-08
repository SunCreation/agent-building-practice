# Python Harness Lab

직접 만든 모델·도구 반복을 로컬 작업과 Terminal-Bench Pro 평가에 함께 사용하는 Python 예시 구현입니다. 완성 코드를 베끼는 것이 과제의 목표는 아닙니다. 선택한 설계와 구현의 책임을 비교하고, 자기 하네스의 기준 성능을 측정한 뒤 개선 실험을 수행합니다.

## 처음 실행하기

**`pyproject.toml`, `run.py`, `harness_lab/` 폴더가 함께 보이는 프로젝트 루트에서 실행합니다.** `harness_lab/` 안으로 들어가지 않습니다. 아래 명령들은 이 위치를 기준으로 합니다.

```sh
uv sync --locked
uv run run.py
```

두 번째 명령은 사용법을 보여 줍니다. `uv sync`가 성공했다면 다시 설치할 필요 없이 다음 단계로 진행하면 됩니다. `run.py`는 실행 진입점이며, 실제 모델·도구 반복 코드는 `harness_lab/` 안에 있습니다.

개발 도우미에게 맡긴다면 다음처럼 요청하세요.

> README의 첫 실행 순서대로 준비해 줘. API 키 값은 내가 터미널에서 입력할게.

### 1. OpenAI API 키 입력

제공받은 **OpenAI API 키**를 아래 숨김 입력 창에 입력합니다. 키 문자가 화면에 나타나지 않는 것이 정상입니다. 키를 입력하고 Enter를 누릅니다. 코드·채팅·스크린샷에는 키 값을 넣지 마세요.

macOS / Linux:

```sh
export OPENAI_API_KEY="$(uv run python -c 'import getpass; print(getpass.getpass("OpenAI API key: "))')"
```

Windows PowerShell:

```powershell
$env:OPENAI_API_KEY = uv run python -c "import getpass; print(getpass.getpass('OpenAI API key: '))"
```

값을 출력하지 않고 설정 여부만 확인합니다.

```sh
uv run python -c "import os; print('키 설정됨' if os.getenv('OPENAI_API_KEY') else '키 없음')"
```

**이어서 같은 터미널에서 실행합니다.** 환경 변수는 다른 터미널에 자동으로 공유되지 않습니다. Claude Code/OpenCode 로그인과 이 하네스의 OpenAI API 인증은 별개입니다. 현재 예제의 OpenAI 기본 모델은 `gpt-4.1-mini`입니다. 이용 권한 오류가 나오면 수업에서 사용할 수 있는 모델을 확인하고 `--model 실제모델ID`를 추가하세요.

### 2. 예제 작업 폴더 복사

원본 예제를 보존하기 위해 별도 폴더로 복사합니다. 다음 명령은 macOS·Linux·PowerShell에서 동일하게 사용할 수 있습니다.

```sh
uv run python -c "import shutil; shutil.copytree('examples/workspace', 'my-workspace')"
```

`my-workspace`가 이미 있으면 복사를 반복하지 않습니다. 처음 상태로 비교하고 싶다면 기존 폴더를 덮어쓰지 말고 다른 이름으로 복사한 뒤 실행 명령의 `--workspace`도 그 이름으로 바꾸세요.

### 3. 첫 읽기 작업

```sh
uv run run.py --provider openai --workspace my-workspace --session reading --prompt "meeting.txt를 읽고 시작 시각과 준비물을 알려줘."
```

화면에서 모델의 도구 요청, 실제 파일 읽기 결과, 최종 답변을 확인합니다. `my-workspace/meeting.txt`를 직접 열어 답변과 대조하세요. 이 파일은 수업용 가상 모임 안내입니다. 답변이 자연스러운지만 보지 말고 실제 읽기 기록이 있는지도 봅니다.

실행이 끝나면 상태와 사용량, 세션 파일과 실행 기록 경로가 출력됩니다. `unknown` 또는 비어 있는 사용량은 0을 뜻하지 않습니다.

### 4. 코드 수정과 테스트

`receipt.py`에는 의도적인 결함이 있습니다. 함수 설명은 영수증 전체에 할인을 한 번만 적용하고, 결과가 음수가 되지 않도록 요구합니다. 먼저 복사본의 테스트를 실행해 기존 실패를 확인합니다.

```sh
uv run python -m unittest discover -s my-workspace -p test_receipt.py -v
```

이 단계에서 테스트가 실패하는 것은 준비된 예제의 특성입니다. 다음으로 하네스에 수정을 요청합니다.

```sh
uv run run.py --provider openai --workspace my-workspace --session coding --prompt "receipt.py를 함수 설명대로 고쳐 줘. 테스트는 바꾸지 말고 실행 결과를 확인해 줘."
```

파일을 변경하기 전에는 diff가 표시됩니다. **Diff는 수정 전후 차이를 보여 주는 형식**입니다. 내용을 확인하고 적용하려면 `y`를 입력합니다. 명령 실행에도 인수와 실행 폴더가 표시되므로 확인 후 승인합니다. 다른 입력은 거절로 처리됩니다.

실행 후 위의 테스트 명령을 다시 수행해 결과를 직접 확인합니다. 모델이 “고쳤다”고 답한 것과 파일 변경·테스트 성공은 구별합니다. 명령 실행은 운영체제 샌드박스가 아니므로 신뢰할 수 있는 작은 실습 코드에서 진행하세요.

### 5. 같은 세션으로 후속 요청

```sh
uv run run.py --provider openai --workspace my-workspace --session coding --prompt "방금 고친 내용과 확인한 테스트 결과를 설명해 줘. 파일은 더 바꾸지 마."
```

`--session coding`을 유지하면 같은 대화 맥락을 이어 갑니다. `--workspace`와 제공자·모델 설정도 앞선 실행과 같게 유지합니다. 새 작업의 대화를 분리하려면 새 세션 이름을 사용하세요. 같은 세션을 동시에 실행하지 않습니다.

기본 세션은 `.harness/sessions/`, 실행별 JSONL 기록은 `.harness/runs/`에 저장됩니다. JSONL은 한 줄에 JSON 이벤트 하나씩 저장하는 형식입니다. 대화와 파일 내용이 평문으로 기록될 수 있으므로 민감한 자료를 실습에 넣지 않습니다.

<details><summary>Ollama 사용, 모델 선택과 추가 실행 옵션</summary>

Ollama 서비스와 도구 호출을 지원하는 로컬 모델을 준비합니다. 설치된 모델을 확인한 뒤 모델 이름을 선택하세요. 예제 CLI의 기본 Ollama 모델은 `qwen3.5:2b`이지만, 로컬에 준비된 모델과 같아야 합니다.

```sh
ollama list
uv run run.py --provider ollama --model qwen3.5:2b --workspace my-workspace --session ollama-reading --prompt "meeting.txt를 읽고 시작 시각과 준비물을 알려줘."
```

Ollama 경로에서는 OpenAI API 키가 필요하지 않습니다. 모델을 다운로드하거나 서비스를 시작하는 과정은 Ollama 환경 안내를 따릅니다. 다른 제공자나 모델로 바꿀 때는 새 세션 이름을 사용합니다.

모든 옵션은 다음 명령으로 확인합니다.

```sh
uv run run.py --help
```

`--model`은 모델 선택, `--base-url`은 제공자 주소, `--state-dir`은 기록 위치입니다. `--max-steps`, `--max-tool-calls`, `--timeout`, `--tool-timeout`, `--max-output-tokens`로 실행 상한을 지정할 수 있습니다. 처음에는 기본값으로 작은 작업부터 확인합니다.

</details>

<details><summary>Python 모듈 방식과 경로 오류 해결</summary>

`run.py` 대신 패키지를 모듈로 실행해도 됩니다. 역시 프로젝트 루트에서 실행합니다.

```sh
uv run python -m harness_lab.cli --help
uv run python -m harness_lab.cli --provider openai --workspace my-workspace --session reading --prompt "meeting.txt를 읽고 시작 시각과 준비물을 알려줘."
```

`-m harness_lab.cli`는 `harness_lab` 패키지 안의 `cli` 모듈을 실행하라는 뜻입니다. **`uv run harness_lab/cli.py`처럼 내부 파일을 직접 실행하면 상대 import 오류가 날 수 있습니다.** 이 파일은 같은 패키지의 `agent`, `providers`, `tools`를 상대 import로 가져오기 때문입니다.

이미 `cd harness_lab`로 내부 폴더에 들어갔다면 한 단계 나옵니다.

```sh
cd ..
uv run run.py
```

`pyproject.toml`이 보이는지 확인한 뒤 나머지 명령을 실행하세요. 실행 위치를 바꾸면 `my-workspace`와 `.harness`의 기준 위치도 달라집니다.

</details>

<details><summary>개발용 단위 테스트</summary>

```sh
uv sync --locked --extra dev
uv run python -m pytest -q
```

이는 프로젝트 구현의 단위 테스트입니다. 실제 모델이나 벤치마크 성능을 확인한 점수와 구별합니다. 기본 설치에 채점 의존성도 포함되어 있으므로 별도 추가 설치 명령은 필요하지 않습니다.

</details>

## 코드가 연결되는 지점
| 파일 | 책임 | 읽으면서 확인할 질문 |
|---|---|---|
| harness_lab/agent.py | 요청→모델→도구→결과 반환, 한도와 세션 | 실행할 도구는 누가 검사하고 결과를 다음 요청에 어떻게 잇는가 |
| harness_lab/providers.py | OpenAI Responses / Ollama native chat 변환 | 제공자 고유 응답과 호출 식별자를 내부 계약에 어떻게 대응시키는가 |
| harness_lab/tools.py | 로컬 파일 도구, 변경 표시·승인, 승인한 명령 실행 | 거절하면 실행되지 않는가, 경로 제한은 어디에 적용되는가 |
| harness_lab/cli.py | 로컬 입력, 설정, 실행 기록과 세션 | 같은 세션을 재사용할 때 무엇이 보존되는가 |
| harness_lab/local_agent.py | 동일 루프를 로컬 평가 작업 폴더에 연결 | 평가용 도구가 실제 제출할 하네스를 실행하는가 |
| harness_lab/benchmark_source.py | 고정 원본 다운로드·해시 검사·로컬 경로 변환 | 문제와 원본 난도가 고정되어 있는가 |
| harness_lab/grading.py | 원본 pytest 채점 실행 | 모델의 완료 주장 대신 실제 산출물을 검사하는가 |
| harness_lab/bench.py | 고정 10문항·실험 설정·실행 | 문제와 모델, 예산, 코드 버전을 어떻게 남기는가 |
| harness_lab/report.py | 독립 채점 결과 집계와 비교 | 오류·미완료가 점수 분모에서 빠지지 않는가 |

실제 수정 전에 `agent.py`의 `Agent.run`과 `local_agent.py`의 `LocalBenchmarkTools`를 함께 읽어 보세요. 반복은 이 프로젝트가 직접 수행하고, 평가 도구는 문제마다 새 작업 폴더에 연결됩니다.

## 실제 벤치마크 10문항 평가

Docker 없이 학생 컴퓨터에서 실행합니다. `benchmark/tasks.json`은 Terminal-Bench Pro 원본 `task.toml`의 **easy 2 / medium 4 / hard 4**를 그대로 사용합니다. 임의 난도를 붙인 자체 문제가 아닙니다. 실제 문제·입력·Python 채점 코드를 고정 커밋에서 가져오며 원본 파일을 이 ZIP에 재배포하지 않습니다.

원본의 `/app`, `/protected`, `/db`, `/home/user` 등을 시행별 작업 폴더로 옮긴 **로컬 이식판(local-port)**입니다. 원본 함수·클래스 계약과 채점 조건을 유지하지만 컨테이너 환경을 재현하지 않으므로 공식 점수와 직접 비교하지 않습니다. 더 다양한 실행 환경을 일관되게 맞추려면 Docker를 사용할 수 있으며 이번 실습에서는 요구하지 않습니다.

> README를 보고 로컬 10문제 평가를 준비하고 설정을 확인해 줘.

<details><summary>문제 준비와 설정 확인</summary>

프로젝트 루트에서:

```sh
uv sync --locked
uv run python -m harness_lab.benchmark_source --prepare
uv run python -m harness_lab.bench --name baseline --provider openai --model gpt-4.1-mini --dry-run
```

첫 준비에는 원본 다운로드를 위한 인터넷 연결이 필요합니다. `.benchmark-cache`에 받은 파일의 SHA256을 확인합니다. 수정되거나 손상된 캐시는 해시 오류로 멈춥니다. dry-run은 설정만 보여 주며 모델 호출·문제 실행·채점을 하지 않습니다. 모델 이름은 제공받은 키로 접근할 수 있는 모델로 바꿉니다.

</details>

> 현재 하네스로 baseline을 실행하고, 점수 화면을 열어 줘.

<details><summary>첫 평가와 모니터링</summary>

```sh
uv run python -m harness_lab.bench --name baseline --provider openai --model gpt-4.1-mini
```

Ollama를 쓰면 대신 다음 명령을 실행합니다.

```sh
uv run python -m harness_lab.bench --name baseline --provider ollama --model qwen3.5:2b
```

실행 중 표시되는 `jobs/baseline/scoreboard/index.html`을 브라우저로 엽니다. 각 문제 결과가 저장될 때 표가 갱신됩니다. 별도 터미널에서 다른 위치로 결과를 모으려면:

```sh
uv run python -m harness_lab.report jobs/baseline --watch 5 --output reports/baseline
```

이 경우 `reports/baseline/index.html`을 엽니다. 한 번만 저장하려면 `--watch 5`를 빼고 실행합니다. 같은 실험 이름은 덮어쓰지 않으며 새 실험에는 새 이름을 붙입니다.

</details>

기본 한도는 문제당 모델 40단계·300초, Python 도구 한 번 10초이며 원본 채점은 별도로 실행됩니다. 기준·개선에서 같은 한도를 사용합니다. 순차적으로 열 문제를 시도하고 결과가 없는 문제도 분모에서 빼지 않습니다. 코드 사본, 문제 버전, 모델, 한도, 운영체제·Python 버전과 실제 실행 기록을 남깁니다. 모델이 완료했다고 답해도 통과는 채점 결과로 판단합니다. 각 시행의 `agent/events.jsonl`에는 순서·시간·상태가, `agent/sessions/trial.json`에는 모델에 전달된 대화와 도구 인수·결과가 남습니다. 후자는 출력 제한을 적용한 대화 기록이며 원본 프로세스 출력 전체를 보장하지 않습니다. 실패 분석 때 두 기록과 verifier 결과를 함께 읽습니다.

평가용 도구는 새 작업 폴더의 수정을 자동 승인합니다. 상대경로뿐 아니라 이 작업 폴더 안의 절대경로도 받아 내부에서 상대경로로 바꿉니다. 폴더 밖 절대경로·상위 이동·심볼릭 링크는 계속 거절합니다. 실행한 Python은 사용자 계정 권한으로 동작하며 OS 보안 샌드박스가 아닙니다. `tests/`와 `solution/`은 모델 작업 폴더에 넣지 않습니다. 데이터 복구 문제의 원본에 포함된 기대값 파일은 채점자 쪽에만 둡니다. 공개 벤치마크의 답을 찾아 복사하거나 입력 자료를 정답에 맞춰 바꾸지 않습니다.

### 자기 하네스 연결

기본 명령은 제공 예제를 평가합니다. 자신의 구현을 평가하려면 `local_agent.py`의 비동기 `solve_task(instruction, workspace, logs_dir, options)`를 참고해 연결 함수를 작성합니다. 실제 종료 상태·답변·사용량을 사전으로 반환하며, 파일 결과는 전달받은 workspace에 남깁니다.

```sh
uv run python -m harness_lab.bench --name baseline-own --agent my_agent:solve_task --provider openai --model gpt-4.1-mini
```

이는 `my_agent.py`를 작성한 경우의 예입니다. 다른 언어의 실행 프로그램을 호출한다면 그 프로그램의 소스·빌드·버전과 종료·시간 제한 처리도 기록합니다. 제출할 구현이 실제로 호출되는지 실행 로그로 확인합니다.

## 개선 실험
기준 실행을 보존하고 실패 기록에서 가설 하나를 고릅니다. 예를 들어 도구 출력이 잘려 중요한 오류를 놓쳤다면 출력 정책을 바꿉니다. 모델, 10문항, 반복 횟수와 예산을 유지하고 다시 평가합니다. 공식 정답·채점 테스트를 읽고 특정 문제 답을 하드코딩하는 것은 하네스 개선이 아닙니다.

> 기준 실행에서 반복되는 실패 원인을 찾아 가설 하나를 정하자. 개선 후 같은 조건으로 다시 측정하고 비교해 줘.

<details><summary>재측정과 비교</summary>

```sh
uv run python -m harness_lab.bench --name improved --provider openai --model YOUR_MODEL
uv run python -m harness_lab.report jobs/improved --compare jobs/baseline --output reports/comparison
```

문항당 3회 반복하려면 **양쪽 실행 모두** `--attempts 3`을 사용하고 집계에도 `--attempts 3`을 넘깁니다. 반복 평균은 전체 30번의 성공 수/30입니다. 세 번 중 한 번만 성공해도 그 문제를 성공으로 보는 pass@3과 다릅니다.

</details>

## 제출
`EXPERIMENT_REPORT.md`를 작성하고 구현체, 실행 방법, 설계 문서, 두 실행의 원본 결과·설정·CSV·HTML을 제출합니다. jobs의 source 사본에서 benchmark/upstream 원문과 캐시·풀이·채점 파일은 제출 묶음에서 제외하고, 고정 manifest와 다시 내려받는 절차를 남깁니다. 높지 않은 점수도 유효한 실험 결과입니다. 실패나 오류를 숨기지 않고 구현과 관찰에 근거해 설명하는 것이 중요합니다. 이 10문항은 교육용 부분집합이며 공식 컨테이너·전체 점수 또는 통계적으로 입증한 우월성으로 표현하지 않습니다.

## 검증 범위와 공식 자료
실측 기록은 배포 시 `VALIDATION.md`를 확인합니다. 단위 테스트·로컬 모델 확인·설정 검사를 실제 10문항 성능 측정과 구별합니다.

- [OpenAI 도구 호출](https://developers.openai.com/api/docs/guides/function-calling)
- [Ollama 도구 호출](https://docs.ollama.com/capabilities/tool-calling)
- [고정 Terminal-Bench Pro 문제](https://github.com/alibaba/terminal-bench-pro/tree/874af409da6aafebccbf3bc5bb41a2fa4d78784d)
- [Terminal-Bench Pro 원본](https://github.com/alibaba/terminal-bench-pro)
