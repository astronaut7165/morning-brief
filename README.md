# 🌅 Morning Brief

매일 아침 기술·비즈니스 동향을 자동으로 모으고, LLM으로 **사업 기회와 실무 적용 관점**에서 골라 요약해 메신저로 보내며, 결과를 마크다운 지식 베이스(Vault)에 쌓는 파이프라인입니다.

```
GitHub Actions (매일 07:00 KST)
  └─ collector  RSS · Google News · Hacker News 에서 최근 24시간 기사 수집, 중복 제거
  └─ analyzer   LLM이 모든 기사를 1~5점으로 매기고 상위 5~7건을 요약
  └─ archiver   vault/raw/YYYY/MM/YYYY-MM-DD.md 로 저장
  └─ notifier   Slack / Discord / Telegram 으로 발송
  └─ git commit & push
```

## 디렉토리 구조

```text
.
├── .github/workflows/daily_briefing.yml  # 매일 아침 실행 + 결과 커밋
├── config/sources.yaml                   # 수집 소스 · 독자 프로필 · 산업군 트래킹 설정
├── src/
│   ├── collector.py                      # 소스별 수집, 중복 제거
│   ├── analyzer.py                       # LLM 점수화(1~5) · 상위 기사 요약
│   ├── notifier.py                       # 메신저 발송
│   ├── archiver.py                       # 마크다운 생성
│   └── main.py                           # 전체 파이프라인 실행
├── vault/
│   ├── raw/                              # 날짜별 브리핑 자동 저장
│   └── curated/                          # 직접 인사이트를 덧붙여 옮긴 노트
├── state/seen.json                       # 이전 브리핑과의 중복 제거 기록 (자동 생성)
├── requirements.txt
└── .env.example
```

## 시작하기

### 1. GitHub에 비밀값 등록

저장소 **Settings → Secrets and variables → Actions** 에서 등록합니다. 코드에는 어떤 키도 들어 있지 않습니다.

| 종류 | 이름 | 설명 |
|---|---|---|
| Secret | `ANTHROPIC_API_KEY` | 기본 LLM. OpenAI·Gemini를 쓰려면 `OPENAI_API_KEY` / `GEMINI_API_KEY` |
| Secret | `SLACK_WEBHOOK_URL` | Slack Incoming Webhook (Mattermost 호환) |
| Secret | `DISCORD_WEBHOOK_URL` | Discord 채널 웹훅 |
| Secret | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Telegram 봇 |
| Variable (선택) | `LLM_PROVIDER` | `anthropic` \| `openai` \| `gemini`. 비우면 등록된 키로 자동 선택 |
| Variable (선택) | `LLM_MODEL` | 모델 변경. 기본값은 `claude-opus-5-5` |
| Variable (선택) | `LLM_EFFORT` | Anthropic 추론 강도 `low` \| `medium` \| `high` (기본 `medium`) |

메신저는 하나 이상 등록하면 되고, 여러 개를 등록하면 모두에게 보냅니다.

### 2. 첫 실행

**Actions → Daily Briefing → Run workflow** 로 바로 실행해 볼 수 있습니다. `notify` 체크를 끄면 메신저 없이 아카이브만 남깁니다. 이후에는 매일 07:00 KST에 자동으로 돕니다.

> GitHub 스케줄러는 정각 실행이 몰려 수~수십 분 늦을 수 있습니다. 공개 저장소의 스케줄은 60일 동안 커밋이 없으면 꺼지는데, 이 워크플로는 매일 커밋하므로 꺼지지 않습니다.

### 3. 로컬 실행 (선택)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # 값 채우기

python src/main.py --dry-run --no-llm   # 수집만 점검 (파일·발송 없음, 비용 없음)
python src/main.py --dry-run            # LLM 분석 결과를 화면에 출력
python src/main.py --no-notify          # vault 에 저장만
python src/main.py                      # 전체 실행
```

## 설정 바꾸기 — `config/sources.yaml`

- **소스 추가**: `sources` 에 항목을 하나 추가합니다. 지원 type은 `rss`, `google_news`(키워드 검색), `hackernews` 입니다.
  ```yaml
  - name: 내가 보는 블로그
    type: rss
    url: https://example.com/feed.xml
    tags: [ai]
  ```
- **산업군 트래킹**: `tracks` 아래 항목의 `enabled: true` 로 켭니다. 해당 소스가 수집에 합류하고, `description` 이 LLM의 '중점 관찰 분야'로 들어가 관련 기사에 가중치가 붙습니다. 새 산업군은 예시 블록을 복사해 키워드만 바꾸면 됩니다.
- **관점 조정**: `reader_profile` 을 내 상황에 맞게 고치면 점수 기준이 바뀝니다.
- **분량·비용**: `settings` 의 `top_n`(브리핑 건수), `min_score`, `max_candidates`(LLM에 넘길 후보 수 상한).
- **새 수집 방식**: `src/collector.py` 에 `@fetcher("새타입")` 함수를 하나 추가하면 yaml에서 바로 쓸 수 있습니다.

## Vault 활용

- `vault/raw/` 는 자동화만 씁니다. 매일 Top Picks(요약·사업 기회·실무 적용)와 전체 후보 목록(점수순), 소스별 수집 현황이 남습니다.
- `vault/curated/` 는 사람만 고칩니다. 다시 볼 가치가 있는 항목을 [`_template.md`](vault/curated/_template.md) 로 옮겨 내 인사이트를 덧붙이세요.
- YAML frontmatter와 태그가 붙어 있어 저장소를 그대로 [Obsidian](https://obsidian.md) Vault로 열 수 있습니다.

## 동작 메모

- **중복 제거**: URL 정규화(추적 파라미터 제거)와 제목 유사도로 같은 기사를 합치고, 여러 매체가 함께 보도한 기사는 '동시 보도 N곳'으로 표시해 LLM 점수에 반영합니다. 전날까지 브리핑에 올린 기사는 `state/seen.json` 으로 걸러내며, 같은 날 다시 실행하면 같은 결과가 나옵니다.
- **장애 대응**: 소스 하나가 실패해도 나머지로 진행하고, 실패한 소스는 아카이브의 '수집 현황'에 표시됩니다. LLM 호출이 실패하면 동시 보도 수·HN 점수·최신순으로 고른 기본 브리핑을 경고와 함께 보내고, 워크플로는 실패로 표시되어 GitHub 알림 메일이 옵니다.
- **Anthropic 호출**: 구조화 출력(JSON 스키마)으로 응답 형식을 강제하고, 정책상 거절이 나면 서버가 기본 대체 모델로 다시 실행하도록 `fallbacks: "default"` 를 켜 두었습니다.
- **비용**: 기본 설정(후보 150건, `claude-opus-5-5`, effort `medium`)으로 하루 한 번 호출하면 입력 약 2만 토큰 수준입니다. 비용을 줄이려면 `LLM_MODEL=claude-sonnet-5-5` 로 바꾸거나 `max_candidates` 를 낮추세요.
- OpenAI·Gemini 기본 모델명(`gpt-5-mini`, `gemini-2.5-flash`)은 바뀔 수 있으니 실제 쓰는 모델을 `LLM_MODEL` 로 지정하는 편이 안전합니다. 이 경우 `requirements.txt` 에서 해당 SDK 주석을 풀어 주세요.
