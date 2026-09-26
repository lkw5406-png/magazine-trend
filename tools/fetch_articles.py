"""기사 수집 Tool — 매체별 RSS에서 최근 기사(제목·소개 앞부분·링크·날짜·사진 주소)를 모아 data/articles.json에 쌓는다.

- 패션 기사만: 피드가 패션 전용이거나, 기사 분류(카테고리)·주소가 sources.json의 include에 맞는 것만 남김.
  분류가 없는 기사는 남기고 Claude가 판정할 때 거름.
- 기사 본문·사진 파일은 저장하지 않음(저작권). 소개글은 앞부분 SNIPPET_CHARS자까지만, 그것도 .tmp/snippets.json(로컬)에만.
  공개 저장소(data/articles.json)에는 제목·링크·날짜·분류·사진 주소만 (2026-09-26 사장님 결정).
- 여러 번 돌려도 같은 기사는 한 번만 저장. 매일 돌리면 최근 기사만 주는 매체(엘르·바자 코리아)도 빠짐없이 모임.

사용법: python tools/fetch_articles.py [--days 8] [--only 매체id,...]
"""
from __future__ import annotations

import argparse
import hashlib
import html
import re
import sys
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import ARTICLES_PATH, KST, LABELS_PATH, SNIPPETS_PATH, SOURCES_PATH, BlockedError, get_text, load_json, save_json, today_kst

SNIPPET_CHARS = 300   # 소개글 저장 길이 (요약 판정용, 원문 전체는 저장 안 함)
OG_FETCH_MAX = 80     # 소개글·사진이 없는 기사만 기사 페이지의 공식 소개·대표 사진(og 태그)을 받음, 한 번에 최대 개수

ITEM_RE = re.compile(r"<item\b.*?</item>", re.S)


def _tag(block: str, name: str) -> str:
    m = re.search(rf"<{name}\b[^>]*>(.*?)</{name}>", block, re.S)
    return _clean(m.group(1)) if m else ""


def _clean(s: str) -> str:
    s = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", s, flags=re.S)
    s = html.unescape(s)
    s = re.sub(r"<!\[CDATA\[|\]\]>", "", s)          # 엘르·바자 코리아: CDATA가 한 번 더 감싸져 있음
    s = re.sub(r"<[^>]+>", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _snippet(s: str) -> str:
    s = re.sub(r"\s*\[(…|&#8230;|\.\.\.)\]\s*$", "", s).rstrip(". ")
    return s if len(s) <= SNIPPET_CHARS else s[:SNIPPET_CHARS].rsplit(" ", 1)[0] + "…"


def _date(block: str) -> datetime | None:
    raw = _tag(block, "pubDate") or _tag(block, "dc:date")
    if not raw:
        return None
    try:
        d = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        try:
            d = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    return d.astimezone(KST)


def _image(block: str) -> str:
    for pat in (r"<media:content[^>]+url=\"([^\"]+)\"", r"<media:thumbnail[^>]+url=\"([^\"]+)\"",
                r"<enclosure[^>]+url=\"([^\"]+)\"[^>]*type=\"image", r"<img[^>]+src=\"([^\"]+)\""):
        m = re.search(pat, block)
        if m:
            return html.unescape(m.group(1))
    return ""


def parse_items(xml: str) -> list[dict]:
    out = []
    for block in ITEM_RE.findall(xml):
        link = _tag(block, "link")
        when = _date(block)
        if not link or not when:
            continue
        cats = [_clean(c) for c in re.findall(r"<category\b[^>]*>(.*?)</category>", block, re.S)]
        cats += [x for x in (_tag(block, "cate_depth1"), _tag(block, "cate_depth2")) if x]
        desc = _tag(block, "description")
        if len(desc) < 40:  # 소개가 짧으면 본문 첫 부분으로 (W 등)
            desc = _tag(block, "content:encoded") or desc
        out.append({"title": _tag(block, "title"), "link": link, "published": when.isoformat(timespec="minutes"),
                    "date": when.strftime("%Y-%m-%d"), "categories": sorted(set(c for c in cats if c)),
                    "desc": _snippet(desc), "image": _image(block)})
    return out


def _hit(word: str, hay: list[str]) -> bool:
    """단어 단위로 찾음 — 'Style'이 'Lifestyle' 안에서 걸리던 문제(2026-09-26) 방지. 한글은 그대로 포함 검사."""
    pat = re.compile(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", re.I)
    return any(pat.search(h) for h in hay)


def is_fashion_candidate(item: dict, src: dict) -> bool:
    hay = item["categories"] + [item["link"]]
    if any(_hit(x, hay) for x in src.get("exclude", [])):
        return False
    if src.get("fashion_only") or not src.get("include"):
        return True
    if not item["categories"]:
        return True  # 분류가 없으면 Claude가 판정
    return any(_hit(x, hay) for x in src["include"])


def refilter(store: dict, labels: dict, sources: list[dict]) -> int:
    """sources.json 필터를 고친 뒤, 아직 판정 안 한 저장 기사에 다시 적용해서 안 맞는 것을 뺌."""
    by_id = {s["id"]: s for s in sources}
    drop = [k for k, a in store.items() if k not in labels and a["source"] in by_id
            and not is_fashion_candidate(a, by_id[a["source"]])]
    for k in drop:
        del store[k]
    return len(drop)


def article_id(link: str) -> str:
    return hashlib.sha1(link.split("?")[0].rstrip("/").encode()).hexdigest()[:12]


def _meta(page: str, names: str) -> str:
    m = re.search(rf"<meta[^>]+(?:property|name)=\"(?:{names})\"[^>]+content=\"([^\"]*)\"", page) or \
        re.search(rf"<meta[^>]+content=\"([^\"]*)\"[^>]+(?:property|name)=\"(?:{names})\"", page)
    return html.unescape(m.group(1)) if m else ""


def page_meta(url: str) -> tuple[str, str]:
    """기사 페이지의 공식 소개글·대표 사진(og:description, og:image). 피드에 없을 때만 씀.
    본문 <img>는 쓰지 않음 — 옆 칸 '관련 기사' 사진이 섞여 엉뚱한 사진이 됨(2026-09-26 W 코리아 캘빈클라인 기사)."""
    page = get_text(url)
    return _snippet(_clean(_meta(page, "og:description|description"))), _meta(page, "og:image|twitter:image")


def fill_from_page(it: dict) -> None:
    """소개글이 짧거나 사진이 없으면 기사 페이지에서 채움 (시각 참고용 사진이 중요 — 2026-09-26 사장님 요청)."""
    try:
        desc, image = page_meta(it["link"])
    except Exception:
        return
    if len(it.get("desc", "")) < 40 and desc:
        it["desc"] = desc
    if not it.get("image") and image:
        it["image"] = image


def split_snippets(store: dict) -> None:
    """소개글(desc)을 공개 파일에서 빼서 로컬 .tmp/snippets.json으로 옮김."""
    snippets = load_json(SNIPPETS_PATH, {})
    for aid, a in store.items():
        desc = a.pop("desc", "")
        if desc and len(desc) > len(snippets.get(aid, "")):
            snippets[aid] = desc
    save_json(SNIPPETS_PATH, snippets)


def fetch_source(src: dict, since: str) -> list[dict]:
    got: dict[str, dict] = {}
    for feed in src["feeds"]:
        for page in range(1, src.get("max_pages", 1) + 1):
            url = feed if page == 1 else f"{feed}{'&' if '?' in feed else '?'}{src['page_param']}={page}"
            items = parse_items(get_text(url))
            new = [it for it in items if article_id(it["link"]) not in got]
            for it in new:
                got[article_id(it["link"])] = it
            if not new or min(it["date"] for it in items) < since:
                break
    return [it for it in got.values() if it["date"] >= since]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=8, help="최근 며칠 기사까지 받을지")
    ap.add_argument("--only", help="이 매체만 (sources.json의 id, 쉼표로)")
    ap.add_argument("--fill-images", action="store_true", help="받지 않고, 사진 없는 저장 기사만 기사 페이지에서 사진 주소 채움")
    ap.add_argument("--refilter", action="store_true", help="받지 않고, 저장된 미판정 기사에 필터만 다시 적용")
    args = ap.parse_args()
    today = today_kst()
    since = (datetime.fromisoformat(today) - timedelta(days=args.days)).strftime("%Y-%m-%d")
    sources = load_json(SOURCES_PATH, {})["sources"]
    store: dict = load_json(ARTICLES_PATH, {})
    if args.fill_images:
        todo = [a for a in store.values() if not a.get("image")]
        for a in todo:
            fill_from_page(a)
        split_snippets(store)
        save_json(ARTICLES_PATH, store)
        print(f"사진 채우기: {len(todo)}개 중 {sum(1 for a in todo if a.get('image'))}개 채움")
        return 0
    if args.refilter:
        n = refilter(store, load_json(LABELS_PATH, {}), sources)
        split_snippets(store)
        save_json(ARTICLES_PATH, store)
        print(f"필터 다시 적용: {n}개 뺌 → 남은 기사 {len(store)}개")
        return 0
    if args.only:
        sources = [s for s in sources if s["id"] in args.only.split(",")]
    report, failed, og_budget = [], [], OG_FETCH_MAX
    for src in sources:
        try:
            items = fetch_source(src, since)
        except BlockedError as e:
            print(f"!! {e} — 이 매체는 멈춤. 재시도하지 않음.")
            failed.append(src["name"]); continue
        except Exception as e:
            print(f"!! {src['name']} 수집 실패: {e}")
            failed.append(src["name"]); continue
        fashion = [it for it in items if is_fashion_candidate(it, src)]
        added = 0
        for it in fashion:
            aid = article_id(it["link"])
            if aid in store:
                continue
            if not it["image"] and og_budget > 0:  # 소개글은 판정할 때 label_queue가 필요한 것만 받음
                og_budget -= 1
                fill_from_page(it)
            store[aid] = {**it, "source": src["id"], "source_name": src["name"], "region": src["region"],
                          "kind": src["kind"], "fetched": today}
            added += 1
        dates = sorted(it["date"] for it in items)
        report.append((src["name"], len(items), len(fashion), added, f"{dates[0]}~{dates[-1]}" if dates else "-"))
    split_snippets(store)
    save_json(ARTICLES_PATH, store)
    print(f"{'매체':22} {'받은 기사':>6} {'패션 후보':>6} {'새로 저장':>6}  기간")
    for name, n, f, a, span in report:
        print(f"{name:22} {n:>8} {f:>8} {a:>8}  {span}")
    print(f"전체 저장 기사: {len(store)}개" + (f" / 실패: {', '.join(failed)}" if failed else ""))
    return 1 if failed and len(failed) == len(sources) else 0


if __name__ == "__main__":
    sys.exit(main())
