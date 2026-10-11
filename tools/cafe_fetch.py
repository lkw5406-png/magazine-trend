"""네이버 카페 게시판 목록 수집 — 최신 글부터 시작 날짜까지 페이지를 넘기며 전부 받는다.

- 받는 것: 글 번호·제목·말머리·작성자·작성 시각·조회수·댓글수·좋아요수·본문 앞부분(판정용).
- 정직한 이름표, 요청 사이 고정 간격. 거부(401/403/429)되면 재시도하지 않고 멈춘다.
- 결과는 .tmp/cafe/ 에만 (공개 저장소에 올리지 않음). 중간에 끊기면 다시 실행해 이어받는다.

사용: python tools/cafe_fetch.py --start 2024-01-01 [--cafe 27877258 --menu 6] [--max-pages 2]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime

from common import KST, TMP_DIR, BlockedError, load_json, save_json

USER_AGENT = "FashionCafeResearch/0.1 (personal research; low-volume)"
API = ("https://apis.naver.com/cafe-web/cafe-boardlist-api/v1/cafes/{cafe}/menus/{menu}/articles"
       "?page={page}&pageSize={size}&sortBy=TIME&viewType=L")
PAGE_SIZE = 50
REQUEST_GAP = 1.5
SUMMARY_LEN = 300
CAFE_DIR = TMP_DIR / "cafe"


def fetch_page(cafe: int, menu: int, page: int) -> list[dict]:
    url = API.format(cafe=cafe, menu=menu, page=page, size=PAGE_SIZE)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Referer": "https://cafe.naver.com/"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 429):
            raise BlockedError(f"네이버 카페가 요청을 거부함 ({e.code}), {page}쪽") from e
        raise
    rows = []
    for entry in data.get("result", {}).get("articleList", []):
        it = entry.get("item") or {}
        if entry.get("type") != "ARTICLE" or it.get("menuId") != menu:
            continue
        w = it.get("writerInfo") or {}
        rows.append({
            "id": it["articleId"],
            "subject": it.get("subject", ""),
            "head": it.get("headName", ""),
            "writer": w.get("nickName", ""),
            "writer_level": w.get("memberLevelName", ""),
            "ts": it.get("writeDateTimestamp", 0),
            "views": it.get("readCount", 0),
            "comments": it.get("commentCount", 0),
            "likes": it.get("likeCount", 0),
            "summary": (it.get("summary") or "")[:SUMMARY_LEN],
        })
    return rows


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cafe", type=int, default=27877258)
    ap.add_argument("--menu", type=int, default=6)
    ap.add_argument("--start", default="2024-01-01", help="이 날짜(한국 시간)까지 거슬러 올라가며 수집")
    ap.add_argument("--max-pages", type=int, default=0, help="시험용. 0이면 끝까지")
    args = ap.parse_args()

    start_ms = int(datetime.strptime(args.start, "%Y-%m-%d").replace(tzinfo=KST).timestamp() * 1000)
    out_path = CAFE_DIR / f"articles_{args.cafe}_{args.menu}.json"
    state_path = CAFE_DIR / f"state_{args.cafe}_{args.menu}.json"
    articles = {a["id"]: a for a in load_json(out_path, [])}
    state = load_json(state_path, {})
    # 이어받기: 그 사이 새 글이 올라와 쪽이 밀렸을 수 있으니 몇 쪽 앞에서 다시 시작(글 번호로 중복 제거)
    page = max(1, state.get("next_page", 1) - 3) if state.get("start") == args.start and not state.get("done") else 1

    def save(done: bool) -> None:
        save_json(out_path, sorted(articles.values(), key=lambda a: -a["ts"]))
        save_json(state_path, {"start": args.start, "next_page": page, "done": done,
                               "fetched_at": datetime.now(KST).isoformat(timespec="seconds")})

    pages = 0
    try:
        while True:
            rows = fetch_page(args.cafe, args.menu, page)
            pages += 1
            for r in rows:
                if r["ts"] >= start_ms:
                    articles[r["id"]] = r
            oldest = min((r["ts"] for r in rows), default=0)
            if pages % 20 == 0 or not rows:
                day = datetime.fromtimestamp(oldest / 1000, KST).strftime("%Y-%m-%d") if oldest else "-"
                print(f"{page}쪽 · 누적 {len(articles)}개 · 여기까지 {day}", flush=True)
                save(False)
            if not rows or oldest < start_ms:
                page += 1
                save(True)
                break
            page += 1
            if args.max_pages and pages >= args.max_pages:
                save(False)
                break
            time.sleep(REQUEST_GAP)
    except BlockedError as e:
        save(False)
        print(f"멈춤: {e}. {len(articles)}개까지 저장함. 재시도하지 않음.", file=sys.stderr)
        sys.exit(2)

    ts = [a["ts"] for a in articles.values()]
    fmt = lambda t: datetime.fromtimestamp(t / 1000, KST).strftime("%Y-%m-%d")
    print(f"끝: {len(articles)}개 ({fmt(min(ts))} ~ {fmt(max(ts))}), {pages}쪽 읽음 → {out_path}")


if __name__ == "__main__":
    main()
