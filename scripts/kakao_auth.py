#!/usr/bin/env python3
"""카카오톡 '나에게 보내기' 연결 (최초 1회).

준비: developers.kakao.com 에서 앱을 만들고 .env 에 KAKAO_REST_API_KEY, KAKAO_CLIENT_SECRET 을 넣은 뒤
    .venv/bin/python scripts/kakao_auth.py

브라우저에서 카카오 로그인·동의를 마치면 토큰이 저장소 밖(~/.config/morning-brief/kakao_token.json)에
저장되고, 확인 메시지가 내 카카오톡 '나와의 채팅'으로 옵니다.
"""

from __future__ import annotations

import html
import os
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import notifier  # noqa: E402
from main import load_dotenv, repo_web_url  # noqa: E402

DEFAULT_REDIRECT_URI = "http://localhost:8765/callback"
WAIT_SECONDS = 300


def wait_for_code(redirect_uri: str) -> tuple[str | None, str | None]:
    """리다이렉트 주소에서 잠깐 로컬 서버를 열어 인가 코드를 받는다. (code, error)"""
    target = urlsplit(redirect_uri)
    if target.hostname not in ("localhost", "127.0.0.1"):
        return None, None
    result: dict[str, str | None] = {}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            req = urlsplit(self.path)
            if req.path != (target.path or "/"):
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(req.query)
            result["code"] = (query.get("code") or [None])[0]
            result["error"] = (query.get("error_description") or query.get("error") or [None])[0]
            msg = "연결 완료! 이 창을 닫고 터미널로 돌아가세요." if result["code"] else f"실패: {result['error']}"
            body = f"<meta charset='utf-8'><h2>{html.escape(msg)}</h2>".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    try:
        server = HTTPServer((target.hostname, target.port or 80), Handler)
    except OSError as exc:
        print(f"로컬 서버를 열 수 없습니다 ({exc}).")
        return None, None
    server.timeout = 2
    deadline = time.monotonic() + WAIT_SECONDS
    with server:
        while not result and time.monotonic() < deadline:
            server.handle_request()
    return result.get("code"), result.get("error")


def main() -> int:
    load_dotenv(ROOT / ".env")
    key = os.getenv("KAKAO_REST_API_KEY")
    if not key:
        print(".env 에 KAKAO_REST_API_KEY 를 먼저 넣으세요 (README 의 '카카오톡' 항목 참고).")
        return 1
    redirect_uri = os.getenv("KAKAO_REDIRECT_URI") or DEFAULT_REDIRECT_URI
    auth_url = notifier.KAKAO_AUTH_URL + "?" + urlencode({
        "client_id": key,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": "talk_message",
    })

    print("브라우저에서 카카오 로그인 후 '카카오톡 메시지 전송'에 동의하세요.")
    print("창이 열리지 않으면 아래 주소를 직접 여세요:\n" + auth_url + "\n")
    webbrowser.open(auth_url)

    code, error = wait_for_code(redirect_uri)
    if error:
        print(f"로그인/동의가 완료되지 않았습니다: {error}")
        return 1
    if not code:
        pasted = input("로그인 후 이동한 페이지의 주소(URL) 전체를 붙여 넣으세요: ").strip()
        code = (parse_qs(urlsplit(pasted).query).get("code") or [pasted])[0]

    try:
        path = notifier.kakao_exchange_code(code, redirect_uri)
        print(f"토큰을 저장했습니다: {path}")
        link = repo_web_url() or notifier.KAKAO_FALLBACK_LINK
        notifier.kakao_send_texts(
            ["✅ 모닝 브리핑이 카카오톡에 연결됐습니다.\n다음 브리핑부터 이 채팅방으로 받아보실 수 있습니다."],
            link, button="저장소 보기",
        )
    except notifier.NotifyError as exc:
        print(f"실패: {exc}")
        return 1
    print("카카오톡 '나와의 채팅'에서 확인 메시지를 확인하세요.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
