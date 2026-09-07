# Python Harness Lab

직접 만든 모델·도구 반복을 로컬 작업과 Terminal-Bench Pro 평가에 함께 사용하는 Python 예시 구현입니다. 완성 코드를 베끼는 것이 과제의 목표는 아닙니다. 선택한 설계와 구현의 책임을 비교하고, 자기 하네스의 기준 성능을 측정한 뒤 개선 실험을 수행합니다.

## 코드가 연결되는 지점
| 파일 | 책임 | 읽으면서 확인할 질문 |
|---|---|---|
| harness_lab/agent.py | 요청→모델→도구→결과 반환, 한도와 세션 | 실행할 도구는 누가 검사하고 결과를 다음 요청에 어떻게 잇는가 |
| harness_lab/providers.py | OpenAI Responses / Ollama native chat 변환 | 제공자 고유 응답과 호출 식별자를 내부 계약에 어떻게 대응시키는가 |
| harness_lab/tools.py | 로컬 파일 도구, 변경 표시·승인, 승인한 명령 실행 | 거절하면 실행되지 않는가, 경로 제한은 어디에 적용되는가 |
| harness_lab/cli.py | 로컬 입력, 설정, 실행 기록과 세션 | 같은 세션을 재사용할 때 무엇이 보존되는가 |
| harness_lab/harbor_agent.py | 동일 루프를 Harbor의 컨테이너 도구에 연결 | 모델이 요청한 명령이 호스트가 아닌 문제 환경에서 실행되는가 |
| harness_lab/bench.py | 고정 10문항·실험 설정·실행 | 문제와 모델, 예산, 코드 버전을 어떻게 남기는가 |
| harness_lab/report.py | 독립 채점 결과 집계와 비교 | 오류·미완료가 점수 분모에서 빠지지 않는가 |

실제 수정 전에 `agent.py`의 `Agent.run`과 `harbor_agent.py`의 `HarborTools.execute`를 함께 읽어 보세요. Harbor는 문제 환경과 채점기를 관리하고, 모델을 호출해 다음 도구를 고르는 반복은 이 프로젝트가 수행합니다. Claude Code/OpenCode가 문제를 대신 푸는 구성은 아닙니다.

## 환경 준비
Python 3.13과 uv를 사용합니다. 개발 도우미에게 다음처럼 요청합니다.

> 이 프로젝트의 실행 환경을 준비하고 테스트를 돌려 줘. 파일별 역할도 설명해 줘.

<details><summary>수동 설치와 단위 테스트</summary>

```sh
uv sync --extra benchmark --extra dev
uv run python -m pytest -q
```

`uv.lock`으로 의존성을 고정합니다. Harbor 0.22.0과 OpenAI SDK 2.54.0을 기준으로 구현했습니다. 단위 테스트는 모의 모델·모의 환경을 사용하며 벤치마크 점수가 아닙니다.

</details>

## 작은 로컬 작업
제공된 `examples/workspace`는 가상 모임 안내와 의도적인 결함이 있는 영수증 함수입니다. 실행 전에 별도 작업 폴더로 복사해 원본 fixture를 보존합니다.

> 예제 작업 폴더를 복사하고 이 하네스로 모임 안내를 읽어 보자. 모델과 키 설정 방법은 알려 주되 키 값은 채팅에 넣지 않을게.

<details><summary>Ollama로 읽기 작업 실행</summary>

Ollama 서비스와 도구 호출을 지원하는 로컬 모델을 먼저 준비합니다. 아래 모델 이름은 로컬에 설치한 이름으로 바꿉니다.

```sh
cp -R examples/workspace my-workspace
uv run python -m harness_lab.cli --provider ollama --model qwen3.5:2b --workspace my-workspace --session reading --prompt 'meeting.txt를 읽고 시작 시각과 준비물을 알려줘.'
```

PowerShell에서는 복사 명령을 `Copy-Item -Recurse examples/workspace my-workspace`로 바꿉니다. 나머지 실행 명령은 한 줄로 입력합니다.

OpenAI를 사용하면 프로그램을 실행할 터미널에 `OPENAI_API_KEY`를 설정하고 `--provider openai --model <사용할-모델>`로 선택합니다. 꺾쇠 부분은 실제 모델 이름으로 바꿉니다. 모델 키와 구독 인증은 다르며 Claude Code/OpenCode 로그인만으로 이 API 키가 제공되지는 않습니다.

</details>

코딩 작업은 `receipt.py`의 명세와 테스트를 읽은 뒤 수정합니다. 먼저 복사본에서 테스트의 실패를 확인하고, 하네스에 다음 요청을 넣습니다.

> receipt.py를 명세대로 고쳐 줘. 테스트는 바꾸지 말고 실행 결과를 확인해 줘.

파일 변경은 diff를 표시하고 `y`를 받아야 적용됩니다. 명령은 argv와 실행 폴더를 보여 주고 승인받습니다. 명령 실행은 운영체제 샌드박스가 아니며 승인한 코드에 현재 사용자의 권한이 있습니다. 신뢰할 수 있는 작은 실습 코드만 로컬에서 실행합니다. 세션과 실행 로그는 `.harness`에 저장됩니다. 세션은 평문이며 민감한 자료를 넣지 않습니다. 같은 세션을 동시에 쓰지 않습니다.

## 고정 10문항 평가
`benchmark/tasks.json`은 Terminal-Bench Pro 공개 문제 중 원본 난이도 easy 2개·hard 8개를 고정한 교육용 목록입니다. 8개 분야를 포함하며 원본 커밋, 메타데이터 확인 값, 환경 요구사항을 기록합니다. 원본 문제나 정답을 이 ZIP에 복제하지 않습니다. 실행 시 Harbor가 지정 저장소 커밋을 받습니다.

먼저 Docker 호환 엔진을 설치·시작하고 이미지 저장 공간과 네트워크 접근을 확보합니다. GPU 0 선언이 저용량을 뜻하지는 않습니다. 문제별 Docker 이미지와 빌드 의존성이 있으며, 일부 이미지 태그·의존성은 원본에서 완전히 고정되지 않았습니다. Apple Silicon 등의 아키텍처와 원본 레지스트리 접근도 환경 검사 대상입니다.

> 이 10문항의 환경 검사를 준비하고, 내 하네스를 평가할 설정을 확인해 줘. 아직 모델 평가는 시작하지 마.

<details><summary>설정 검사와 Oracle 환경 검사</summary>

```sh
uv run python -m harness_lab.bench --name config-check --model YOUR_MODEL --dry-run
uv run python -m harness_lab.bench --name oracle-check --oracle
```

`YOUR_MODEL`은 사용할 실제 모델 이름으로 바꿉니다. dry-run은 설정 형식만 확인하며 컨테이너·모델·채점 성공을 뜻하지 않습니다. Oracle은 원본 정답 풀이로 환경과 채점이 동작하는지 검사합니다. Oracle 점수는 학생 하네스 성능이 아닙니다. 정답 파일을 개발 AI의 프롬프트나 학생 하네스의 입력에 넣지 않습니다. 환경이 실패하면 원인과 재실행을 기록하고 해결한 뒤 기준 측정을 시작합니다.

</details>

> 같은 모델과 실행 한도로 10문항 기준 측정을 시작하고, 진행 점수를 볼 수 있게 해 줘.

<details><summary>기준 측정과 모니터링</summary>

```sh
uv run python -m harness_lab.bench --name baseline --provider openai --model YOUR_MODEL
```

다른 터미널에서:

```sh
uv run python -m harness_lab.report jobs/baseline --watch 5 --output reports/baseline
```

HTML·JSON·CSV가 출력 폴더에 갱신됩니다. `reports/baseline/index.html`을 브라우저에서 엽니다. 완료된 뒤 한 번만 집계하려면 `--watch 5`를 생략합니다. Ollama는 `--provider ollama --model 설치한모델`로 선택하며 모델 API는 Harbor를 실행하는 호스트에서 연결합니다. 로컬 작은 모델이 hard 문제를 풀 수 있다는 보장은 없습니다.

</details>

문제별 새 컨테이너에서 시험하고, 기본은 문항당 1회·동시 실행 1개·자동 재시도 0회입니다. `run-metadata.json`에는 코드 해시·문제 버전·모델·한도·시작과 종료를 기록합니다. 이미 존재하는 실험 이름을 덮어쓰지 않습니다. 모델이 '완료'라고 답해도 통과는 독립 채점기의 reward로 판정합니다.

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
`EXPERIMENT_REPORT.md`를 작성하고 구현체, 실행 방법, 설계 문서, 두 실행의 원본 결과·설정·CSV·HTML을 제출합니다. 높지 않은 점수도 유효한 실험 결과입니다. 실패나 오류를 숨기지 않고 구현과 관찰에 근거해 설명하는 것이 중요합니다. 이 10문항은 교육용 부분집합이며 공식 전체 점수 또는 통계적으로 입증한 우월성으로 표현하지 않습니다.

## 검증 범위와 공식 자료
실측 기록은 배포 시 `VALIDATION.md`를 확인합니다. 단위 테스트·로컬 모델 확인·설정 검사를 실제 10문항 성능 측정과 구별합니다.

- [OpenAI 도구 호출](https://developers.openai.com/api/docs/guides/function-calling)
- [Ollama 도구 호출](https://docs.ollama.com/capabilities/tool-calling)
- [Harbor 자체 에이전트 연결](https://www.harborframework.com/docs/agents)
- [Terminal-Bench Pro 원본](https://github.com/alibaba/terminal-bench-pro)
