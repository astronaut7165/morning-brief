# 🌅 Morning Brief

매일 아침 기술·비즈니스 동향과 코인(비트코인·이더리움·리플) 시장을 자동으로 모아 Claude로 중요한 기사를 고릅니다. 기사별 **사실**(날짜 포함)을 먼저 전하고, 마지막에 **사업가의 시선** — 이 소식들을 어떤 눈으로 봐야 하는지 — 을 붙여 메신저(카카오톡 포함)로 보내며, 결과를 마크다운 지식 베이스(Vault)와 휴대폰용 웹페이지로 남기는 파이프라인입니다.

```
내 Mac (launchd, 매일 07:00) — 브리핑마다(기술·비즈니스 → 코인) 아래를 반복
  └─ collector  RSS · Google News · Hacker News 에서 최근 24시간 기사 수집, 중복 제거 (코인은 시세·공포탐욕 지수도)
  └─ analyzer   Claude Code(`claude -p`, 구독 로그인)가 모든 기사를 1~5점으로 매기고, 상위 5~7건의 사실 정리 + 사업가의 시선
  └─ archiver   vault/raw/YYYY/MM/YYYY-MM-DD.md 로 저장
  └─ webpage    docs/ 에 휴대폰용 페이지 생성 (GitHub Pages 로 공개)
  └─ git commit & push → 페이지가 열리는지 확인
  └─ notifier   카카오톡 / Slack / Discord / Telegram 으로 발송 ('전체 브리핑 보기' → 휴대폰용 페이지)
```

API 키 없이 **로컬 Claude Code에 로그인된 Claude 구독**으로 분석합니다. 원하면 GitHub Actions + API 키 방식으로도 돌릴 수 있습니다([아래](#대안-github-actions--api-키)).

## 디렉토리 구조

```text
.
├── .github/workflows/daily_briefing.yml  # (대안) GitHub Actions 실행 — 기본은 수동 실행만
├── config/sources.yaml                   # 기술·비즈니스 브리핑: 수집 소스 · 독자 프로필 · 산업군 트래킹
├── config/crypto.yaml                    # 코인 브리핑: 코인 목록 · 소스 · 독자 프로필
├── docs/                                 # 휴대폰용 페이지 (자동 생성, GitHub Pages 가 공개)
├── scripts/
│   ├── install_launchd.sh                # 매일 아침 자동 실행 등록 (macOS)
│   ├── kakao_auth.py                     # 카카오톡 '나에게 보내기' 최초 로그인
│   └── run_local.sh                      # pull → 브리핑 → 커밋·push
├── src/
│   ├── collector.py                      # 소스별 수집, 중복 제거
│   ├── analyzer.py                       # LLM 점수화(1~5) · 사실 정리 · 분석(브리핑 종류별 프롬프트)
│   ├── market.py                         # 코인 시세 · 김치 프리미엄 · 공포·탐욕 지수
│   ├── notifier.py                       # 메신저 발송
│   ├── archiver.py                       # 마크다운 생성
│   ├── webpage.py                        # 휴대폰용 HTML 페이지 · 모아보기 생성
│   └── main.py                           # 전체 파이프라인 실행
├── vault/
│   ├── raw/                              # 날짜별 브리핑 자동 저장 (코인은 raw/crypto/)
│   └── curated/                          # 직접 인사이트를 덧붙여 옮긴 노트
├── state/seen.json                       # 이전 브리핑과의 중복 제거 기록 (자동 생성)
├── requirements.txt
└── .env.example
```

## 시작하기 (로컬 Mac)

**준비물**: [Claude Code](https://code.claude.com)가 설치되어 있고 `claude` 로 한 번 로그인한 상태, Python 3.11+.

```bash
git clone https://github.com/astronaut7165/morning-brief.git && cd morning-brief

# 1) 메신저 설정 — .env 에 웹훅 하나 이상 입력 (.env 는 git에 올라가지 않습니다)
cp .env.example .env

# 2) 매일 07:00 자동 실행 등록 (가상환경 생성 + launchd 등록)
bash scripts/install_launchd.sh

# 3) 지금 한 번 실행해 보기
launchctl kickstart gui/$(id -u)/com.morning-brief.daily
tail -f ~/Library/Logs/morning-brief.log
```

| `.env` 항목 | 설명 |
|---|---|
| `KAKAO_REST_API_KEY`, `KAKAO_CLIENT_SECRET` | 카카오톡 '나와의 채팅'으로 받기 — [아래](#카카오톡으로-받기) 설정 후 로그인 1회 |
| `DISCORD_WEBHOOK_URL` | 채널 설정 → 연동 → 웹후크 → URL 복사 (가장 간단) |
| `SLACK_WEBHOOK_URL` | api.slack.com/apps → Incoming Webhooks (Mattermost 호환) |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | @BotFather 로 봇 생성, `getUpdates` 로 chat id 확인 |
| `LLM_MODEL` (선택) | 기본 `opus`. 구독 사용량을 아끼려면 `sonnet` |
| `LLM_EFFORT` (선택) | `low` \| `medium` \| `high` (기본 `medium`) |

- 실행 시간 변경: `BRIEF_HOUR=6 BRIEF_MINUTE=30 bash scripts/install_launchd.sh`
- 해제: `bash scripts/install_launchd.sh --uninstall`
- 그 시각에 Mac이 **잠자기** 중이면 깨어날 때 실행되고, **꺼져** 있으면 그날은 건너뜁니다. 매일 자동으로 깨우려면 `sudo pmset repeat wakeorpoweron MTWRFSU 06:55:00`.
- 분석은 `claude -p` 를 도구·MCP·개인 설정 없이 호출하며, 로그인된 구독의 사용량에서 차감됩니다. 환경에 `ANTHROPIC_API_KEY` 가 있어도 claude 에는 넘기지 않아 API 과금으로 바뀌지 않습니다.

### 카카오톡으로 받기

카카오 공식 API의 '나에게 보내기'로 내 카카오톡 **나와의 채팅**에 브리핑이 옵니다. 개인 용도라 사업자 등록·검수가 필요 없습니다.

1. [Kakao Developers](https://developers.kakao.com) 에 로그인해 앱을 하나 만듭니다.
2. 앱 키의 **REST API 키**를 `.env` 의 `KAKAO_REST_API_KEY` 에, **클라이언트 시크릿**(기본으로 켜져 있음)을 `KAKAO_CLIENT_SECRET` 에 넣습니다.
3. **카카오 로그인**을 켜고 Redirect URI 에 `http://localhost:8765/callback` 을 등록합니다.
4. **동의항목**에서 '카카오톡 메시지 전송(talk_message)'을 사용하도록 설정합니다.
5. **제품 링크 관리 > 웹 도메인**에 `https://<GitHub계정>.github.io`(휴대폰용 페이지)와 `https://github.com` 을 등록합니다. 메시지 버튼('전체 브리핑 보기')은 등록된 도메인만 열립니다.
6. 로그인을 한 번 합니다. 브라우저에서 로그인·동의를 마치면 카카오톡으로 확인 메시지가 옵니다.
   ```bash
   .venv/bin/python scripts/kakao_auth.py
   ```

- 말풍선 하나가 200자로 제한돼 있어 **헤더 → 기사별 → 사업가의 시선별**로 나눠 하루 10개 안팎의 말풍선으로 옵니다. 잘린 내용과 원문 링크는 '전체 브리핑 보기'에서 볼 수 있습니다.
- 토큰은 저장소 밖 `~/.config/morning-brief/kakao_token.json` 에 보관되고 매일 실행할 때 자동으로 갱신됩니다. 두 달 넘게 실행하지 않아 만료되면 6번만 다시 하면 됩니다.
- localhost 리다이렉트가 막히면 Redirect URI 를 다른 주소로 등록하고 `.env` 의 `KAKAO_REDIRECT_URI` 를 같게 맞추세요. 로그인 후 이동한 페이지의 주소를 붙여 넣으라는 안내가 나옵니다.
- 갱신되는 토큰을 저장해야 해서 카카오톡 발송은 로컬 실행에서만 지원합니다(GitHub Actions 대안에서는 불가).

### 휴대폰용 페이지 (GitHub Pages)

메신저의 '전체 브리핑 보기'를 누르면 휴대폰에서 읽기 좋게 정리한 페이지가 열립니다. 기사 카드, 사업가의 시선, 접어 둔 전체 후보, 이전·다음 브리핑 이동이 들어 있고, 다크 모드도 따라갑니다.

1. 저장소 **Settings → Pages** 에서 Source 를 `Deploy from a branch`, 브랜치 `main`, 폴더 `/docs` 로 저장합니다. 터미널이라면:
   ```bash
   gh api -X POST repos/<계정>/<저장소>/pages -f "source[branch]=main" -f "source[path]=/docs"
   ```
2. 주소는 `https://<계정>.github.io/<저장소>/` 입니다(날짜별 모아보기).
3. 끄려면 `config/sources.yaml` 의 `web_page: false`. 그러면 링크가 GitHub 의 마크다운 파일로 바뀝니다.

- 매일 실행은 **커밋·push → 페이지 배포 확인(최대 5분) → 발송** 순서라, 메시지를 받자마자 눌러도 열립니다.
- 공개 저장소의 Pages 는 누구나 볼 수 있습니다(검색 엔진 색인만 막아 둠). 비공개 저장소의 Pages 는 GitHub 유료 플랜이 필요합니다.

### 코인 브리핑

기술·비즈니스 브리핑에 이어 `config/crypto.yaml` 로 코인 브리핑이 따로 만들어집니다. 저장 위치도 따로입니다(`vault/raw/crypto/`, `docs/crypto/`, 페이지 주소 `…github.io/<저장소>/crypto/`).

- **시세**: 달러 가격, 24시간·7일·30일·1년 변동률, 사상 최고가 대비, 업비트 원화 가격과 김치 프리미엄, 공포·탐욕 지수 (CoinGecko·업비트·alternative.me, 키 불필요)
- **오늘의 소식**: 코인별·시장 전체 주요 기사의 사실
- **🔮 단기·장기 시선**: 지금 시장 분위기, 코인별 단기(1~4주)·장기(6개월~) 방향 기울기(상승 우위·하락 우위·중립)와 확신도, 근거, 판단이 바뀌는 신호. 하루치 시세와 뉴스로 내린 판단이라 가격 목표나 매수·매도 권유는 하지 않습니다.
- **🔎 시장을 읽는 눈**: 자금 흐름·규제·수급 같은 시선 2~3개
- 카카오톡은 말풍선 3~4개로 요약해 보냅니다(`kakao: compact`). 코인을 바꾸려면 `coins` 를, 끄려면 `brief.enabled: false`.
- 다른 주제의 브리핑도 같은 방식으로 만들 수 있습니다. 설정 파일을 복사해 `brief.name`·`title` 을 바꾸고 `scripts/run_local.sh` 의 목록에 추가하세요.

### 수동 실행

```bash
bash scripts/run_local.sh               # 예약 실행과 동일 (pull → 브리핑 → 커밋·push)
.venv/bin/python src/main.py --dry-run --no-llm   # 수집만 점검 (파일·발송 없음)
.venv/bin/python src/main.py --dry-run            # 분석 결과를 화면에만 출력
.venv/bin/python src/main.py --no-notify          # vault 에 저장만
.venv/bin/python src/main.py --config config/crypto.yaml --dry-run   # 코인 브리핑만 화면에 출력
```

## 설정 바꾸기 — `config/sources.yaml`

- **관점 조정 (가장 중요)**: `reader_profile` 을 내 상황·관심 산업·고민 중인 사업에 맞게 구체적으로 쓰면 점수와 마지막 '사업가의 시선'이 내 상황에 맞춰집니다.
- **소스 추가**: `sources` 에 항목을 하나 추가합니다. 지원 type은 `rss`, `google_news`(키워드 검색), `hackernews` 입니다.
  ```yaml
  - name: 내가 보는 블로그
    type: rss
    url: https://example.com/feed.xml
    tags: [ai]
  ```
- **산업군 트래킹**: `tracks` 아래 항목의 `enabled: true` 로 켭니다. 해당 소스가 수집에 합류하고, `description` 이 LLM의 '중점 관찰 분야'로 들어가 관련 기사에 가중치가 붙습니다. 새 산업군은 예시 블록을 복사해 키워드만 바꾸면 됩니다.
- **분량**: `settings` 의 `top_n`(브리핑 건수), `min_score`, `max_candidates`(LLM에 넘길 후보 수 상한). 휴대폰용 페이지는 `web_page`.
- **새 수집 방식**: `src/collector.py` 에 `@fetcher("새타입")` 함수를 하나 추가하면 yaml에서 바로 쓸 수 있습니다.

## Vault 활용

- `vault/raw/` 는 자동화만 씁니다. 매일 오늘의 소식(기사별 사실·발행일·관련 보도), 사업가의 시선(큰 그림, 오늘 기사에 맞는 시선 3~4개와 스스로 던져볼 질문), 전체 후보 목록(점수순, 2점 이하는 접힘), 소스별 수집 현황이 남습니다.
- `vault/curated/` 는 사람만 고칩니다. 다시 볼 가치가 있는 항목을 [`_template.md`](vault/curated/_template.md) 로 옮겨 내 인사이트를 덧붙이세요. 자동 커밋은 `vault/raw`, `state`, `docs` 만 올리므로 작성 중인 노트가 섞여 올라가지 않습니다.
- 이 폴더를 그대로 [Obsidian](https://obsidian.md) Vault로 열 수 있습니다. 메신저의 '전체 브리핑 보기'는 같은 내용을 휴대폰용으로 만든 페이지(`docs/`)로 연결됩니다.

## 동작 메모

- **중복 제거**: URL 정규화(추적 파라미터 제거)와 제목 유사도로 같은 기사를 합치고, 여러 매체가 함께 보도한 기사는 '동시 보도 N곳'으로 표시해 점수에 반영합니다. 전날까지 브리핑에 올린 기사는 `state/seen.json` 으로 걸러내며, 같은 날 다시 실행하면 같은 결과가 나옵니다.
- **장애 대응**: 소스 하나가 실패해도 나머지로 진행하고, 실패한 소스는 아카이브의 '수집 현황'에 표시됩니다. LLM 분석이 실패하면(로그인 만료 등) 동시 보도 수·HN 점수·최신순으로 고른 기본 브리핑을 경고와 함께 보냅니다. 원인은 `~/Library/Logs/morning-brief.log` 에 남습니다.

## 대안: GitHub Actions + API 키

Mac이 꺼져 있어도 돌아야 한다면 GitHub에서 실행할 수 있습니다. 이 경우 Claude 구독이 아니라 API 키로 과금됩니다.

1. 저장소 **Settings → Secrets and variables → Actions** 에 `ANTHROPIC_API_KEY`(또는 `OPENAI_API_KEY` / `GEMINI_API_KEY`)와 메신저 값을 Secret 으로 등록합니다. 모델은 Variable `LLM_MODEL` 로 바꿀 수 있습니다(기본 `claude-opus-5-5`).
2. `.github/workflows/daily_briefing.yml` 의 `schedule` 주석을 풀고, 로컬 launchd 는 해제합니다(중복 실행 방지).
3. **Actions → Daily Briefing → Run workflow** 로 수동 실행도 됩니다.

OpenAI·Gemini를 쓰려면 `requirements.txt` 에서 해당 SDK 주석을 풀고 `LLM_PROVIDER`·`LLM_MODEL` 을 지정하세요.
