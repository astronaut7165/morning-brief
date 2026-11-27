"""모닝 브리핑 파이프라인: 수집 → 분석 → 아카이브 → 발송.

사용법
  python src/main.py                 # 전체 실행
  python src/main.py --dry-run       # 저장·발송·기록 갱신 없이 결과 마크다운만 출력
  python src/main.py --no-llm        # LLM 없이 기본 정렬로 (수집/발송 점검용)
  python src/main.py --no-notify     # 아카이브만 남기고 메신저는 보내지 않음
  python src/main.py --publish       # 발송 전에 결과를 커밋·push (메신저 링크가 바로 열리도록, run_local.sh 가 사용)
  python src/main.py --config config/crypto.yaml   # 다른 브리핑(예: 코인). 저장 폴더는 config 의 brief.name
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import yaml

from analyzer import KST, BriefInfo, analyze
from archiver import render_markdown, write_daily
from collector import SeenStore, collect
from market import fetch_market
from notifier import notify
from webpage import page_marker, page_url, wait_until_live, write_page

ROOT = Path(__file__).resolve().parent.parent
PUBLISH_PATHS = ("vault/raw", "state", "docs")  # 자동화가 만드는 파일만 (curated/ 는 건드리지 않음)
log = logging.getLogger("morning-brief")


def load_dotenv(path: Path) -> None:
    """로컬 실행용 .env 로더. 이미 값이 있는 환경변수는 덮어쓰지 않는다."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key and not os.environ.get(key):
            os.environ[key] = value


def repo_web_url() -> str | None:
    """GitHub Actions 환경변수, 없으면 로컬 git remote(origin)에서 저장소 웹 주소를 찾는다."""
    if repo := os.getenv("GITHUB_REPOSITORY"):
        return f"{os.getenv('GITHUB_SERVER_URL', 'https://github.com')}/{repo}"
    try:
        remote = subprocess.run(
            ["git", "-C", str(ROOT), "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.match(r"^(?:https://github\.com/|git@github\.com:)([\w.-]+/[\w.-]+?)(?:\.git)?$", remote)
    return f"https://github.com/{match.group(1)}" if match else None


def archive_url(path: Path) -> str | None:
    """메신저에 넣을 아카이브 파일의 GitHub 주소 (push 된 뒤에 열림)."""
    base = repo_web_url()
    if not base:
        return None
    branch = os.getenv("GITHUB_REF_NAME") or "main"
    return f"{base}/blob/{quote(branch)}/{quote(path.relative_to(ROOT).as_posix())}"


def publish(day, name: str = "") -> bool:
    """자동화가 만든 파일을 커밋하고 push 한다. 메신저 링크가 열리도록 발송 전에 부른다."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True, timeout=120)

    try:
        paths = [p for p in PUBLISH_PATHS if (ROOT / p).exists()]
        git("add", "--", *paths)
        if git("diff", "--cached", "--quiet").returncode != 0:
            label = f"briefing({name})" if name else "briefing"
            commit = git("commit", "-q", "-m", f"{label}: {day.isoformat()}")
            if commit.returncode != 0:
                log.warning("git commit 실패: %s", (commit.stderr or commit.stdout).strip()[:300])
                return False
        push = git("push", "-q")
    except (OSError, subprocess.SubprocessError) as exc:
        log.warning("커밋·push 실패: %s", exc)
        return False
    if push.returncode != 0:
        log.warning("git push 실패: %s", push.stderr.strip()[:300])
        return False
    log.info("커밋·push 완료")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="매일 아침 기술·비즈니스 브리핑 생성")
    parser.add_argument("--config", type=Path, default=ROOT / "config" / "sources.yaml")
    parser.add_argument("--dry-run", action="store_true", help="저장·발송·기록 갱신 없이 결과를 출력")
    parser.add_argument("--no-llm", action="store_true", help="LLM 없이 기본 정렬로 브리핑")
    parser.add_argument("--no-notify", action="store_true", help="메신저 발송 생략")
    parser.add_argument("--publish", action="store_true", help="발송 전에 결과를 커밋·push")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
        datefmt="%H:%M:%S",
    )
    for noisy in ("httpx", "httpx2", "urllib3", "anthropic", "openai", "google_genai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    load_dotenv(ROOT / ".env")
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    settings = config.get("settings") or {}
    info = BriefInfo.from_config(config)
    if (config.get("brief") or {}).get("enabled") is False:
        log.info("%s: brief.enabled 가 false 라 건너뜁니다 (%s)", info.title, args.config.name)
        return 0

    now = datetime.now(timezone.utc)
    today = now.astimezone(KST).date()
    seen_file = ROOT / "state" / (f"seen-{info.name}.json" if info.name else "seen.json")
    seen = SeenStore(seen_file, today, int(settings.get("seen_retention_days", 14)))
    log.info("===== %s (%s) =====", info.title, args.config.name)

    # 1) 수집 (코인 브리핑은 시세도)
    result = collect(config, now, seen)
    market = fetch_market(config["coins"], now.astimezone(KST)) if config.get("coins") else None
    # 2) 분석
    briefing = analyze(result.articles, config, now, use_llm=not args.no_llm, market=market)

    if args.dry_run:
        sys.stdout.write(render_markdown(briefing, result))
        return 0

    # 3) 아카이브 + 휴대폰용 페이지 + 중복 기록 갱신
    path = write_daily(briefing, result, ROOT / "vault")
    log.info("아카이브 저장: %s", path.relative_to(ROOT))
    link = archive_url(path)
    page_link = None
    if settings.get("web_page"):
        docs = ROOT / "docs" / info.name if info.name else ROOT / "docs"
        page = write_page(briefing, result, docs, source_href=link)
        log.info("휴대폰용 페이지: %s", page.relative_to(ROOT))
        page_link = page_url(page, ROOT / "docs", repo_web_url())
        link = page_link or link
    seen.mark([a.key for a in briefing.candidates])
    seen.save()

    # 4) 커밋·push — 메신저 링크가 받자마자 열리도록 발송보다 먼저
    published = publish(briefing.day, info.name) if args.publish else None

    # 5) 발송
    notify_failed = False
    if args.no_notify:
        log.info("--no-notify: 메신저 발송을 건너뜁니다")
    else:
        if published and page_link:
            log.info("GitHub Pages 배포 대기: %s", page_link)
            wait_until_live(page_link, expect=page_marker(briefing))
        sent = notify(briefing, link)
        notify_failed = bool(sent) and not all(sent.values())

    problems = []
    if published is False:
        problems.append("커밋·push 실패")
    if briefing.error:
        problems.append("LLM 분석 실패(휴리스틱으로 대체)")
    if result.stats and len(result.failed_sources) == len(result.stats):
        problems.append("모든 소스 수집 실패")
    if market is not None and not market:
        problems.append("시세 수집 실패")
    if notify_failed:
        problems.append("일부 메신저 발송 실패")
    if problems:
        log.error("완료했지만 문제가 있습니다: %s", ", ".join(problems))
        return 1
    log.info("완료: %s", path.relative_to(ROOT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
