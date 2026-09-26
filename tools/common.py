"""공통 설정 — 경로, 한국 시간, 정직한 요청(robots.txt 준수, 거부되면 멈춤)."""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLS_DIR = ROOT / "tools"
DATA_DIR = ROOT / "data"
TMP_DIR = ROOT / ".tmp"
DOCS_DIR = ROOT / "docs"
ARTICLES_PATH = DATA_DIR / "articles.json"        # 모은 기사 (제목·링크·날짜·분류·사진 주소) — 공개 저장소에 올라감
SNIPPETS_PATH = TMP_DIR / "snippets.json"         # 기사 소개글 앞부분 (판정용). 저작권 때문에 공개 저장소에 올리지 않음(.tmp는 gitignore)
LABELS_PATH = DATA_DIR / "article_labels.json"    # Claude가 읽고 쓴 판정표 (패션 여부·성별·요약·키워드)
NOTES_PATH = DATA_DIR / "weekly_notes.json"       # 주간 핵심 트렌드 문장 (Claude 작성)
SOURCES_PATH = TOOLS_DIR / "sources.json"
VOCAB_PATH = TOOLS_DIR / "trend_keywords.json"

KST = timezone(timedelta(hours=9))
USER_AGENT = "MagazineTrendResearch/0.1 (personal research; RSS a few times a day, low-volume)"
CLAUDE_AGENT = "Claude-User"
REQUEST_GAP = 1.5  # 같은 사이트에 연달아 요청할 때 쉬는 시간(초)


class BlockedError(RuntimeError):
    """사이트가 거부(403/429)함 — 재시도하지 않고 멈춘 뒤 사장님께 보고."""


def today_kst() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d")


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def save_json(path: Path, data, indent: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=indent, sort_keys=True), encoding="utf-8")
    tmp.replace(path)


_robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
_last_hit: dict[str, float] = {}


def allowed(url: str) -> bool:
    """robots.txt가 이 주소를 허용하는지. robots.txt를 못 받으면(없음) 허용으로 봄."""
    host = urllib.parse.urlparse(url)
    base = f"{host.scheme}://{host.netloc}"
    if base not in _robots:
        rp = urllib.robotparser.RobotFileParser()
        try:
            rp.parse(_raw_get(base + "/robots.txt").splitlines())
            _robots[base] = rp
        except urllib.error.HTTPError as e:
            _robots[base] = None if e.code == 404 else rp
        except Exception:
            _robots[base] = None
    rp = _robots[base]
    # 우리 이름표뿐 아니라 Claude-User(사용자 대신 Claude가 읽는 것) 규칙도 지킴 — 요약을 Claude가 하므로
    return True if rp is None else all(rp.can_fetch(agent, url) for agent in (USER_AGENT, CLAUDE_AGENT))


def _raw_get(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


def get_text(url: str) -> str:
    """robots.txt 확인 → 사이트별 간격 두고 요청. 403/429면 BlockedError."""
    if not allowed(url):
        raise PermissionError(f"robots.txt가 막은 주소라 받지 않음: {url}")
    host = urllib.parse.urlparse(url).netloc
    wait = REQUEST_GAP - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    try:
        return _raw_get(url)
    except urllib.error.HTTPError as e:
        if e.code in (403, 429):
            raise BlockedError(f"{host}가 요청을 거부함 ({e.code})") from e
        raise
    finally:
        _last_hit[host] = time.time()
