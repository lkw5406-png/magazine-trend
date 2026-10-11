"""카페 게시판 분석 — 브랜드 이름 정리 + 브랜드·글·콘텐츠 유형별 반응 지표 계산.

입력: .tmp/cafe/articles_{cafe}_{menu}.json (cafe_fetch.py 결과)
출력: .tmp/cafe/analysis.json (cafe_report.py가 읽음), .tmp/cafe/brand_map.json (이름 통일표 점검용)

기준
- 브랜드: 제목 맨 앞 [브랜드]. 표기가 흔들리거나(영문/한글, 오타, 'A X B' 협업) 없으면 그 작성자가 가장 자주 쓴 이름으로.
- 글당 평균·중앙값은 올린 지 7일 지난 글만(숫자가 덜 찬 글 제외). 순위는 글 10개 이상 브랜드만.
- 반응률 = (댓글 + 좋아요) ÷ 조회수.

사용: python tools/cafe_analyze.py [--cafe 27877258 --menu 6]
"""
from __future__ import annotations

import argparse
import re
import statistics as st
from collections import Counter, defaultdict
from datetime import datetime, timedelta

from common import KST, TMP_DIR, load_json, save_json

CAFE_DIR = TMP_DIR / "cafe"
MIN_POSTS = 10        # 브랜드 순위에 넣는 최소 글 수
MATURE_DAYS = 7       # 이보다 새 글은 평균 계산에서 제외
GROWTH_MIN = 5        # 성장 비교(6개월): 기간마다 최소 글 수
YOY_MIN = 8           # 성장 비교(작년 대비): 해마다 최소 글 수
ACTIVE_MIN = 3        # '반응 좋은 브랜드' 순위: 최근 12개월 최소 글 수
NEW_DAYS = 548        # 새로 등장한 브랜드: 첫 글이 최근 18개월 안
OPERATOR = {"브랜디드"}  # 카페 운영진 글 — 브랜드 순위에서 제외

TAG_RE = re.compile(r"\s*[\[\(【<]\s*([^\]\)】>]{1,40})[\]\)】>\[]")
GENERIC = {"신상정보", "ootd", "오프라인", "룩북", "이벤트", "안내사항", "컨텐츠", "재발매정보", "리오더", "예약판매",
           "공동구매", "preview", "lookbook", "재입고안내", "재입고리메이크", "콜라보", "공지", "안내", "스포", "발매"}

TYPES = [  # (이름, 제목에서 찾는 말) — 한 글이 여러 유형에 들어갈 수 있음
    ("신상 발매", r"발매|신상|출시|release|drop|드랍|컬렉션|collection|런칭|launch"),
    ("할인·세일", r"세일|sale|할인|\d\s*%|특가|시즌오프|아울렛|쿠폰|블랙프라이데이|블프"),
    ("1+1·증정", r"1\s*\+\s*1|증정|사은품|덤"),
    ("참여 이벤트(댓글·추첨·체험단)", r"이벤트|event|당첨|추첨|체험단|나눔|래플|raffle|무료"),
    ("협업", r"콜라보|협업|collab|\S\s+[x×]\s+\S"),
    ("팝업·오프라인", r"팝업|pop-?up|오프라인|쇼룸|매장|플리마켓|입점|스토어|백화점|더현대"),
    ("선발매·프리오더·공구", r"프리오더|pre-?order|예약|선발매|공구|공동구매|얼리버드"),
    ("재입고·리오더", r"재입고|리오더|재발매|restock|재출시|재생산|재오픈"),
    ("B급·샘플·리퍼브 판매", r"b급|b-?grade|샘플\s*세일|리퍼브|아카이브\s*세일|창고"),
    ("룩북·캠페인", r"룩북|lookbook|캠페인|화보"),
    ("착샷·스타일링", r"착샷|착용|ootd|데일리룩|스냅|코디|스타일링|입어|핏\b|사이즈"),
    ("제작기·미리보기", r"제작기|비하인드|스포|미리보기|preview|개발|샘플|원단|공장|이야기|만들"),
    ("라이브 방송", r"라이브|live"),
    ("플랫폼 행사(무신사·29CM 등)", r"무신사|29cm|eql|kream|크림|w컨셉|무진장"),
    ("사과·이슈 해명", r"사과|해명|입장|죄송|논란"),
]
TYPES = [(n, re.compile(p, re.I)) for n, p in TYPES]
WEEKDAYS = "월화수목금토일"


def key_of(name: str) -> str:
    return re.sub(r"[\s\.\,\-_/:'’|]+", "", name).lower()


def raw_tag(subject: str) -> str | None:
    m = TAG_RE.match(subject)
    if not m:
        return None
    tag = m.group(1).strip(" ,.")
    return None if key_of(tag) in GENERIC or not tag else tag


def resolve_brands(articles: list[dict]) -> dict:
    """글마다 a['brand']를 채우고 {대표 이름: {흡수된 표기: 글 수}}를 돌려준다."""
    by_writer: dict[str, Counter] = defaultdict(Counter)
    for a in articles:
        a["_tag"] = raw_tag(a["subject"])
        if a["_tag"]:
            by_writer[a["writer"]][key_of(a["_tag"])] += 1
    dominant = {w: c.most_common(1)[0][0] for w, c in by_writer.items()}
    majors = set(dominant.values())  # 어떤 작성자의 주 브랜드인 이름만 독립 브랜드로 인정
    display: dict[str, Counter] = defaultdict(Counter)
    absorbed: dict[str, Counter] = defaultdict(Counter)
    for a in articles:
        tag, k = a["_tag"], key_of(a["_tag"]) if a["_tag"] else None
        if k in majors:
            final = k
        elif a["writer"] in dominant:
            final = dominant[a["writer"]]
        else:
            final = k or key_of(a["writer"])
            tag = tag or a["writer"]
        a["_key"] = final
        if k == final:
            display[final][tag] += 1
        absorbed[final][tag or f"(이름 없음 · {a['writer']})"] += 1
    names = {k: (display[k].most_common(1)[0][0] if display[k] else absorbed[k].most_common(1)[0][0]) for k in absorbed}
    for a in articles:
        a["brand"] = names[a.pop("_key")]
        a.pop("_tag")
    return {names[k]: dict(v.most_common()) for k, v in absorbed.items() if len(v) > 1}


def stats(rows: list[dict]) -> dict:
    """글 묶음의 지표. 평균·중앙값은 rows 그대로(호출 쪽에서 성숙한 글만 넘김)."""
    if not rows:
        return {"n": 0}
    v = [r["views"] for r in rows]
    c = [r["comments"] for r in rows]
    lk = [r["likes"] for r in rows]
    return {
        "n": len(rows), "views": sum(v), "comments": sum(c), "likes": sum(lk),
        "avg_views": round(st.mean(v)), "med_views": round(st.median(v)),
        "avg_comments": round(st.mean(c), 1), "med_comments": round(st.median(c), 1),
        "avg_likes": round(st.mean(lk), 1),
        "rate": round((sum(c) + sum(lk)) / sum(v) * 100, 2) if sum(v) else 0,
        "comment_rate": round(sum(c) / sum(v) * 100, 2) if sum(v) else 0,
    }


def pct_rank(values: dict[str, float]) -> dict[str, float]:
    order = sorted(values, key=values.get)
    n = max(len(order) - 1, 1)
    return {k: i / n * 100 for i, k in enumerate(order)}


def slim(a: dict) -> dict:
    return {k: a[k] for k in ("id", "subject", "brand", "head", "date", "views", "comments", "likes", "types", "summary")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cafe", type=int, default=27877258)
    ap.add_argument("--menu", type=int, default=6)
    args = ap.parse_args()

    articles = load_json(CAFE_DIR / f"articles_{args.cafe}_{args.menu}.json", [])
    state = load_json(CAFE_DIR / f"state_{args.cafe}_{args.menu}.json", {})
    now = datetime.fromisoformat(state["fetched_at"]) if state.get("fetched_at") else datetime.now(KST)
    brand_map = resolve_brands(articles)
    save_json(CAFE_DIR / "brand_map.json", brand_map, indent=1)

    for a in articles:
        d = datetime.fromtimestamp(a["ts"] / 1000, KST)
        a["dt"], a["date"], a["month"] = d, d.strftime("%Y-%m-%d"), d.strftime("%Y-%m")
        a["mature"] = (now - d).days >= MATURE_DAYS
        a["types"] = [n for n, rx in TYPES if rx.search(a["subject"])] or ["기타"]
        a["rate"] = (a["comments"] + a["likes"]) / a["views"] * 100 if a["views"] else 0
        a["official"] = a["writer_level"] == "브랜드"
    mature = [a for a in articles if a["mature"]]
    brand_posts = [a for a in mature if a["brand"] not in OPERATOR]

    # 월별 흐름
    by_month = defaultdict(list)
    for a in articles:
        by_month[a["month"]].append(a)
    monthly = [{"month": m, **stats(rows), "brands": len({r["brand"] for r in rows})} for m, rows in sorted(by_month.items())]

    # 브랜드별
    by_brand = defaultdict(list)
    for a in brand_posts:
        by_brand[a["brand"]].append(a)
    cut12 = now - timedelta(days=365)
    brands = {}
    for b, rows in by_brand.items():
        s = stats(rows)
        s.update(brand=b, first=min(r["date"] for r in rows), last=max(r["date"] for r in rows),
                 n_12m=sum(1 for r in rows if r["dt"] >= cut12),
                 top_type=Counter(t for r in rows for t in r["types"]).most_common(1)[0][0],
                 hit_rate=round(sum(1 for r in rows if r["views"] >= 2000) / len(rows) * 100))
        brands[b] = s
    ranked = {b: s for b, s in brands.items() if s["n"] >= MIN_POSTS}

    # 반응 좋은 브랜드 종합 점수: 글당 조회 중앙값·글당 댓글 중앙값·반응률의 순위 백분위 평균
    p_v = pct_rank({b: s["med_views"] for b, s in ranked.items()})
    p_c = pct_rank({b: s["med_comments"] for b, s in ranked.items()})
    p_r = pct_rank({b: s["rate"] for b, s in ranked.items()})
    for b, s in ranked.items():
        s["score"] = round((p_v[b] + p_c[b] + p_r[b]) / 3, 1)
        s["p_views"], s["p_comments"], s["p_rate"] = round(p_v[b]), round(p_c[b]), round(p_r[b])
    top = lambda key, n=30, pool=ranked: sorted(pool.values(), key=lambda s: -s[key])[:n]

    # 성장: 최근 6개월 vs 그 전 6개월 (둘 다 7일 지난 글만)
    end = now - timedelta(days=MATURE_DAYS)
    mid, start = end - timedelta(days=182), end - timedelta(days=364)
    new_cut = now - timedelta(days=NEW_DAYS)
    growth, newcomers, yoy = [], [], []
    for b, rows in by_brand.items():
        rec = [r for r in rows if mid <= r["dt"] < end]
        prev = [r for r in rows if start <= r["dt"] < mid]
        if len(rec) >= GROWTH_MIN and len(prev) >= GROWTH_MIN:
            sr, sp = stats(rec), stats(prev)
            growth.append({
                "brand": b, "n_recent": sr["n"], "n_prev": sp["n"],
                "views_recent": sr["med_views"], "views_prev": sp["med_views"],
                "comments_recent": sr["med_comments"], "comments_prev": sp["med_comments"],
                "views_growth": round((sr["med_views"] / sp["med_views"] - 1) * 100) if sp["med_views"] else None,
                "comments_growth": round((sr["med_comments"] / sp["med_comments"] - 1) * 100) if sp["med_comments"] else None,
                "rate_recent": sr["rate"], "rate_prev": sp["rate"],
            })
        first = min(r["dt"] for r in rows)
        if first >= new_cut and len(rows) >= GROWTH_MIN:
            newcomers.append({**stats(rows), "brand": b, "first": first.strftime("%Y-%m-%d")})
        y1, y2 = [r for r in rows if r["dt"].year == now.year - 1], [r for r in rows if r["dt"].year == now.year]
        if len(y1) >= YOY_MIN and len(y2) >= YOY_MIN:
            s1, s2 = stats(y1), stats(y2)
            yoy.append({"brand": b, "n_prev": s1["n"], "n_recent": s2["n"],
                        "views_prev": s1["med_views"], "views_recent": s2["med_views"],
                        "comments_prev": s1["med_comments"], "comments_recent": s2["med_comments"],
                        "views_growth": round((s2["med_views"] / s1["med_views"] - 1) * 100) if s1["med_views"] else None,
                        "comments_growth": round((s2["med_comments"] / s1["med_comments"] - 1) * 100) if s1["med_comments"] else None,
                        "rate_prev": s1["rate"], "rate_recent": s2["rate"]})
    yoy.sort(key=lambda g: -(g["views_growth"] or -999))
    growth.sort(key=lambda g: -(g["views_growth"] or -999))
    newcomers.sort(key=lambda s: -s["med_views"])

    # 분기별 추이 (성장 상위 브랜드 그래프용)
    quarter = lambda d: f"{d.year}-Q{(d.month - 1) // 3 + 1}"
    q_brand = defaultdict(lambda: defaultdict(list))
    for a in brand_posts:
        q_brand[a["brand"]][quarter(a["dt"])].append(a)
    quarters = sorted({quarter(a["dt"]) for a in brand_posts})
    quarterly = {b: {q: stats(rows) for q, rows in qs.items()} for b, qs in q_brand.items() if b in ranked}

    # 콘텐츠 유형·말머리·요일·시간대·작성자 종류
    def group(keyfn, rows=brand_posts, multi=False):
        g = defaultdict(list)
        for a in rows:
            for k in (keyfn(a) if multi else [keyfn(a)]):
                g[k].append(a)
        return [{"key": k, **stats(v)} for k, v in g.items()]

    hour_band = lambda a: ["0~6시", "6~9시", "9~12시", "12~15시", "15~18시", "18~21시", "21~24시"][
        0 if a["dt"].hour < 6 else (a["dt"].hour - 3) // 3]
    title_feats = [
        ("숫자·혜택 수치가 있는 제목 (%, 1+1, 원)", lambda a: bool(re.search(r"\d\s*%|1\s*\+\s*1|\d+\s*원|\d+만", a["subject"]))),
        ("날짜·기간이 있는 제목", lambda a: bool(re.search(r"\d{1,2}\s*[/.월]\s*\d{1,2}|오늘|내일|마감|까지", a["subject"]))),
        ("물음표가 있는 제목", lambda a: "?" in a["subject"]),
        ("제품 이름 없이 말 거는 제목 (30자 미만, 발매·안내 없음)",
         lambda a: len(a["subject"]) < 30 and not re.search(r"발매|안내|출시|공지|release|sale|세일|컬렉션|collection", a["subject"], re.I)),
        ("'안내'로 끝나는 공지형 제목", lambda a: bool(re.search(r"안내\s*[\.\!]*\s*(\(.*\))?$", a["subject"]))),
    ]
    feats = []
    for name, fn in title_feats:
        yes, no = [a for a in brand_posts if fn(a)], [a for a in brand_posts if not fn(a)]
        feats.append({"key": name, "yes": stats(yes), "no": stats(no)})

    # 글을 얼마나 자주 올리는 브랜드가 반응이 좋은가 (최근 12개월 글 수 구간별)
    freq = defaultdict(list)
    for b, s in brands.items():
        if s["n_12m"] >= 1:
            band = "월 4개 이상" if s["n_12m"] >= 48 else "월 2~4개" if s["n_12m"] >= 24 else "월 1~2개" if s["n_12m"] >= 12 else "월 1개 미만"
            freq[band] += [r for r in by_brand[b] if r["dt"] >= cut12]
    frequency = [{"key": k, **stats(v), "brands": len({r["brand"] for r in v})} for k, v in freq.items()]

    top_posts = lambda key, n, rows=brand_posts: [slim(a) for a in sorted(rows, key=lambda a: -a[key])[:n]]
    rate_pool = [a for a in brand_posts if a["views"] >= 500]
    all_posts = [a for a in mature]

    out = {
        "meta": {
            "cafe": "브랜디드 [패션, 도메스틱, 디자이너, 자체 제작 브랜드]", "menu": "브랜드 소식",
            "from": min(a["date"] for a in articles), "to": max(a["date"] for a in articles),
            "fetched_at": now.isoformat(timespec="seconds"), "posts": len(articles), "mature_posts": len(mature),
            "brands": len({a["brand"] for a in articles}), "ranked_brands": len(ranked),
            "operator_posts": sum(1 for a in articles if a["brand"] in OPERATOR),
            "official_share": round(sum(a["official"] for a in articles) / len(articles) * 100),
            "min_posts": MIN_POSTS, "mature_days": MATURE_DAYS, "growth_min": GROWTH_MIN, "yoy_min": YOY_MIN, "active_min": ACTIVE_MIN, "new_since": new_cut.strftime("%Y-%m-%d"),
            "growth_recent": [mid.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")],
            "growth_prev": [start.strftime("%Y-%m-%d"), mid.strftime("%Y-%m-%d")],
        },
        "overall": stats(all_posts),
        "percentiles": {k: [round(x) for x in st.quantiles([a[k] for a in all_posts], n=10)] for k in ("views", "comments", "likes")},
        "monthly": monthly,
        "yearly": [{"key": y, **stats([a for a in mature if a["date"].startswith(y)])} for y in ("2024", "2025", "2026")],
        "brand_top": {
            "views_total": top("views"), "comments_total": top("comments"),
            "views_per_post": top("med_views"), "comments_per_post": top("med_comments"),
            "rate": top("rate"), "score": top("score"), "posts": top("n"),
            # 지금 참고할 수 있는 브랜드만: 최근 12개월에 글 ACTIVE_MIN개 이상
            "score_active": top("score", pool={b: s for b, s in ranked.items() if s["n_12m"] >= ACTIVE_MIN}),
            "score_dormant": [s for s in top("score", 40) if s["n_12m"] < ACTIVE_MIN],
        },
        "brands_all": sorted(ranked.values(), key=lambda s: -s["score"]),
        "growth": growth, "growth_yoy": yoy, "newcomers": newcomers[:20],
        "quarters": quarters, "quarterly": quarterly,
        "types": sorted(group(lambda a: a["types"], multi=True), key=lambda s: -s["n"]),
        "heads": sorted(group(lambda a: a["head"] or "(말머리 없음)"), key=lambda s: -s["n"]),
        "weekday": sorted(group(lambda a: a["dt"].weekday()), key=lambda s: s["key"]),
        "hours": sorted(group(hour_band), key=lambda s: int(re.match(r"\d+", s["key"]).group())),
        "writer_kind": group(lambda a: "브랜드 공식 계정" if a["official"] else "일반 회원 등급 계정"),
        "title_feats": feats, "frequency": frequency,
        "top_posts": {
            "views": top_posts("views", 30), "comments": top_posts("comments", 30),
            "rate": top_posts("rate", 20, rate_pool), "likes": top_posts("likes", 15),
            "views_recent12": top_posts("views", 25, [a for a in brand_posts if a["dt"] >= cut12]),
            "operator": top_posts("views", 10, [a for a in mature if a["brand"] in OPERATOR]),
        },
    }
    for s in out["weekday"]:
        s["key"] = WEEKDAYS[s["key"]]
    save_json(CAFE_DIR / "analysis.json", out, indent=1)
    m = out["meta"]
    print(f"글 {m['posts']}개 · 브랜드 {m['brands']}곳(순위 대상 {m['ranked_brands']}곳) · 이름 통일 {len(brand_map)}건 → {CAFE_DIR / 'analysis.json'}")


if __name__ == "__main__":
    main()
