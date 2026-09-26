"""과거 기사 수집 Tool — 2026년 1월부터처럼 RSS에 더 이상 없는 과거 주의 기사를 매체별 과거 기사 목록(사이트맵)·RSS 지난 페이지에서 모은다.

사장님 결정(2026-09-26): 과거는 **월별 리포트**(1~8월), 한 달 대표 패션 기사 약 300개 — 매체별 개수(WEEKLY_QUOTA × 3)를 날짜가 고르게 뽑음.
(처음엔 주별 100개로 시작했다가 사장님이 월별로 바꿈. --unit week 도 남겨 둠)
- 보그·W·GQ 코리아: RSS 지난 페이지(?paged=N)에 제목·분류·사진이 다 있어 페이지를 따로 받지 않음
- 나머지: 사이트맵에서 기사 주소 → 기사 페이지의 표준 태그(og:title·og:image·datePublished·articleSection)로 채움
- BoF는 사이트맵이 주제별로만 있어 과거 주에서는 뺌
- 매체마다 1.5초 간격(common.get_text), 매체끼리는 동시에. robots.txt·Claude-User 규칙은 get_text가 검사.
- 결과는 data/articles.json(공개 필드만)과 .tmp/snippets.json(소개글)에 fetch_articles.py와 같은 형식으로 추가. "backfill": true 표시.

사용법: python tools/backfill.py --start 2026-01-01 --end 2026-08-31 [--unit month|week] [--only 매체id,...]
"""
from __future__ import annotations

import argparse
import gzip
import html
import json
import random
import re
import sys
import threading
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARTICLES_PATH, KST, SOURCES_PATH, USER_AGENT, BlockedError, allowed, get_text, load_json, save_json
from fetch_articles import _clean, _snippet, article_id, is_fashion_candidate, parse_items, split_snippets

# 매체별 주당 개수 (합 약 121개 → 판정 후 패션 약 100개). 남성지·국내·해외가 고르게 섞이도록.
WEEKLY_QUOTA = {"vogue_kr": 12, "w_kr": 10, "gq_kr": 9, "elle": 11, "bazaar": 9, "esquire": 8, "menshealth": 4,
                "w": 9, "highsnobiety": 7, "elle_kr": 9, "bazaar_kr": 7, "esquire_kr": 7, "hypebeast": 11, "hypebeast_kr": 8}
OVERSAMPLE = 1.7  # 페이지를 받아야 하는 매체는 패션이 아닌 게 섞이므로 넉넉히 받음

# 허스트(미국) 사이트맵에서 패션 기사 주소 경로
HEARST = {"elle": ("www.elle.com", r"elle\.com/fashion/"), "bazaar": ("www.harpersbazaar.com", r"harpersbazaar\.com/fashion/"),
          "esquire": ("www.esquire.com", r"esquire\.com/style/"), "menshealth": ("www.menshealth.com", r"menshealth\.com/style/")}
HJ_KR = {"elle_kr": "www.elle.co.kr", "bazaar_kr": "www.harpersbazaar.co.kr", "esquire_kr": "www.esquirekorea.co.kr"}
WP_FEEDS = {"vogue_kr": "https://www.vogue.co.kr/feed/", "w_kr": "https://www.wkorea.com/feed/", "gq_kr": "https://www.gqkorea.co.kr/feed/"}


def raw(url: str) -> str:
    """사이트맵(.gz 포함) 받기. robots.txt 확인."""
    if not allowed(url):
        raise PermissionError(f"robots.txt가 막은 주소: {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=90) as r:
        b = r.read()
    if b[:2] == b"\x1f\x8b":
        b = gzip.decompress(b)
    return b.decode("utf-8", "replace")


def sitemap_entries(xml: str) -> list[tuple[str, str]]:
    """[(주소, lastmod 날짜 YYYY-MM-DD)]"""
    out = []
    for block in re.findall(r"<(?:url|sitemap)>(.*?)</(?:url|sitemap)>", xml, re.S):
        loc = re.search(r"<loc>\s*([^<\s]+)", block)
        lm = re.search(r"<lastmod>\s*([^<\s]+)", block)
        if loc:
            out.append((html.unescape(loc.group(1)), lm.group(1)[:10] if lm else ""))
    return out


def week_end(date: str) -> str:
    """그 날짜가 속한 주의 끝(토요일). 리포트 주 = 일~토 (첫 리포트 2026-09-26 토요일에 맞춤)."""
    d = datetime.fromisoformat(date)
    return (d + timedelta(days=(5 - d.weekday()) % 7)).strftime("%Y-%m-%d")


def month_key(date: str) -> str:
    return date[:7]


def spread(items: list, n: int, key) -> list:
    """날짜가 고르게 퍼지도록 n개 고름."""
    items = sorted(items, key=key)
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


# ---------- 매체별 주소 모으기 → [(주소, 날짜 힌트)] ----------
def pool_hearst(sid: str, start: str, end: str) -> list[tuple[str, str]]:
    host, pat = HEARST[sid]
    idx = sitemap_entries(raw(f"https://{host}/sitemap_index.xml"))
    urls = []
    for loc, _ in idx:
        if "/sitemaps/content." in loc:
            urls += [(u, lm) for u, lm in sitemap_entries(raw(loc)) if re.search(pat, u)]
    # lastmod는 '수정일'이라 발행일보다 늦을 수 있음 → start 이후 수정된 것 전부를 후보로, 진짜 날짜는 페이지에서
    return [(u, lm) for u, lm in urls if lm >= start]


def pool_w(start: str, end: str) -> list[tuple[str, str]]:
    idx = sitemap_entries(raw("https://www.wmagazine.com/sitemaps/pages.xml"))
    out = []
    for loc, lm in idx:
        if lm >= start:
            out += [(u, d) for u, d in sitemap_entries(raw(loc)) if "/fashion/" in u and start <= d <= end]
    return out


def pool_highsnobiety(start: str, end: str) -> list[tuple[str, str]]:
    idx = sitemap_entries(raw("https://highsnobiety.com/sitemap-content.xml"))
    out = []
    for loc, _ in idx:
        if "articles" in loc:
            out += [(u, d) for u, d in sitemap_entries(raw(loc)) if "/p/" in u and start <= d <= end]
    return out


def pool_hj_kr(sid: str, start: str, end: str) -> list[tuple[str, str]]:
    host = HJ_KR[sid]
    return [(u, d) for u, d in sitemap_entries(raw(f"https://{host}/sitemap/sitemap.xml"))
            if "/article/" in u and start <= d <= end]


def pool_hypebeast(host: str, start: str, end: str) -> list[tuple[str, str]]:
    out, d = [], datetime.fromisoformat(start).replace(day=1)
    while d.strftime("%Y-%m") <= end[:7]:
        try:
            out += [(u, lm) for u, lm in sitemap_entries(raw(f"https://{host}/sitemap-post-{d:%Y-%m}.xml"))
                    if f"/{d.year}/{d.month}/" in u]
        except Exception as e:
            print(f"  {host} {d:%Y-%m} 사이트맵 실패: {e}")
        d = (d + timedelta(days=32)).replace(day=1)
    return out


def page_article(url: str, date_hint: str) -> dict | None:
    """기사 페이지 표준 태그 → fetch_articles.parse_items와 같은 모양. 날짜를 못 찾으면 사이트맵 날짜."""
    p = get_text(url)

    def meta(names: str) -> str:
        m = re.search(rf'<meta[^>]+(?:property|name)="(?:{names})"[^>]+content="([^"]*)"', p) or \
            re.search(rf'<meta[^>]+content="([^"]*)"[^>]+(?:property|name)="(?:{names})"', p)
        return html.unescape(html.unescape(m.group(1))) if m else ""

    raw_date = meta("article:published_time")
    if not raw_date:
        m = re.search(r'"datePublished"\s*:\s*"([^"]+)"', p)
        raw_date = m.group(1) if m else ""
    try:
        when = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).astimezone(KST) if raw_date else None
    except ValueError:
        when = None
    date = when.strftime("%Y-%m-%d") if when else date_hint
    section = meta("article:section")
    if not section:
        m = re.search(r'"articleSection"\s*:\s*\[?\s*"([^"]+)"', p)
        section = m.group(1).encode().decode("unicode_escape") if m and "\\u" in m.group(1) else (m.group(1) if m else "")
    tags = re.findall(r'<meta[^>]+property="article:tag"[^>]+content="([^"]*)"', p)
    title = meta("og:title")
    if not title or not date:
        return None
    return {"title": _clean(title), "link": url, "published": when.isoformat(timespec="minutes") if when else date,
            "date": date, "categories": sorted({html.unescape(html.unescape(x)) for x in [section, *tags] if x}),
            "desc": _snippet(_clean(meta("og:description|description"))), "image": meta("og:image")}


# ---------- 매체 하나 처리 ----------
def run_source(src: dict, start: str, end: str, weeks: list[str], store: dict, lock: threading.Lock, log: list,
               key=week_end, mult: int = 1) -> None:
    sid, quota = src["id"], WEEKLY_QUOTA.get(src["id"], 0) * mult
    if not quota:
        return
    try:
        if sid in WP_FEEDS:
            items = wp_feed_items(WP_FEEDS[sid], start, end)
            by_week = defaultdict(list)
            for it in items:
                if start <= it["date"] <= end and is_fashion_candidate(it, src):
                    by_week[key(it["date"])].append(it)
            picked = [it for w in weeks for it in spread(by_week.get(w, []), quota, key=lambda x: x["published"])]
        else:
            if sid in HEARST:
                pool = pool_hearst(sid, start, end)
            elif sid == "w":
                pool = pool_w(start, end)
            elif sid == "highsnobiety":
                pool = pool_highsnobiety(start, end)
            elif sid in HJ_KR:
                pool = pool_hj_kr(sid, start, end)
            elif sid == "hypebeast":
                pool = pool_hypebeast("hypebeast.com", start, end)
            elif sid == "hypebeast_kr":
                pool = pool_hypebeast("hypebeast.kr", start, end)
            else:
                return
            pool = [(u, d) for u, d in pool if article_id(u) not in store]
            by_week = defaultdict(list)
            for u, d in pool:
                if d:
                    by_week[key(min(max(d, start), end))].append((u, d))
            picked = []
            for w in weeks:
                cands = by_week.get(w, [])
                random.Random(f"{sid}{w}").shuffle(cands)
                got = []
                for u, d in spread(cands, int(quota * OVERSAMPLE) + 2, key=lambda x: x[1]):
                    if len(got) >= quota:
                        break
                    try:
                        it = page_article(u, d)
                    except BlockedError:
                        raise
                    except Exception:
                        continue
                    if it and start <= it["date"] <= end and is_fashion_candidate(it, src):
                        got.append(it)
                picked += got
        with lock:
            added = 0
            for it in picked:
                aid = article_id(it["link"])
                if aid not in store:
                    store[aid] = {**it, "source": sid, "source_name": src["name"], "region": src["region"],
                                  "kind": src["kind"], "fetched": datetime.now(KST).strftime("%Y-%m-%d"), "backfill": True}
                    added += 1
        log.append(f"{src['name']:24} {added:5}개 추가")
        print(f"  완료: {src['name']} {added}개", flush=True)
    except BlockedError as e:
        log.append(f"{src['name']:24} 거부됨 — {e}")
    except Exception as e:
        log.append(f"{src['name']:24} 실패 — {e}")


def wp_feed_items(feed: str, start: str, end: str) -> list[dict]:
    """워드프레스 RSS 지난 페이지를 start보다 오래된 기사가 나올 때까지."""
    out, page = [], 1
    while page < 2000:
        items = parse_items(get_text(feed if page == 1 else f"{feed}?paged={page}"))
        if not items:
            break
        out += items
        if min(it["date"] for it in items) < start:
            break
        page += 1
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2026-01-01")
    ap.add_argument("--end", default="2026-08-31")
    ap.add_argument("--only")
    ap.add_argument("--unit", choices=["month", "week"], default="month")
    args = ap.parse_args()
    key, mult = (month_key, 3) if args.unit == "month" else (week_end, 1)
    weeks, d = [], datetime.fromisoformat(args.start if args.unit == "month" else week_end(args.start))
    while d.strftime("%Y-%m-%d") <= args.end:
        k = key(d.strftime("%Y-%m-%d"))
        if k not in weeks:
            weeks.append(k)
        d += timedelta(days=7 if args.unit == "week" else 1)
    sources = [s for s in load_json(SOURCES_PATH, {})["sources"] if s["id"] in WEEKLY_QUOTA]
    if args.only:
        sources = [s for s in sources if s["id"] in args.only.split(",")]
    store = load_json(ARTICLES_PATH, {})
    lock, log = threading.Lock(), []
    print(f"과거 기사 수집: {args.start}~{args.end}, {len(weeks)}주, 매체 {len(sources)}곳 (동시에, 매체마다 1.5초 간격)", flush=True)
    threads = [threading.Thread(target=run_source, args=(s, args.start, args.end, weeks, store, lock, log, key, mult)) for s in sources]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    split_snippets(store)
    save_json(ARTICLES_PATH, store)
    print("\n".join(log))
    per_week = defaultdict(int)
    for a in store.values():
        if args.start <= a["date"] <= args.end:
            per_week[key(a["date"])] += 1
    print("기간별 후보 수:", ", ".join(f"{w}:{per_week.get(w, 0)}" for w in weeks))
    return 0


if __name__ == "__main__":
    sys.exit(main())
