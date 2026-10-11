"""카페 게시판 분석 보고서 — 분석 결과(analysis.json) + Claude가 쓴 해석(insights.json)을 PDF로.

화면(HTML)을 만든 뒤 Edge 헤드리스로 PDF 저장. 추가 설치 없음.
출력: output/브랜디드_브랜드소식_분석_날짜.pdf (공개 저장소에 올리지 않음 — .gitignore)

사용: python tools/cafe_report.py [--out 파일.pdf]
"""
from __future__ import annotations

import argparse
import html
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import ROOT, TMP_DIR, load_json

CAFE_DIR = TMP_DIR / "cafe"
EDGE_PATHS = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
              r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]

CSS = """
@page { size: A4; margin: 14mm 13mm 15mm; }
* { box-sizing: border-box; -webkit-print-color-adjust: exact; print-color-adjust: exact; }
body { font-family: 'Malgun Gothic', sans-serif; font-size: 9.6pt; line-height: 1.55; color: #1c2430; margin: 0; }
h1 { font-size: 23pt; margin: 0 0 4px; letter-spacing: -0.5px; }
h2 { font-size: 14.5pt; margin: 0 0 8px; padding-bottom: 5px; border-bottom: 2px solid #1c2430; break-after: avoid; }
h3 { font-size: 10.8pt; margin: 14px 0 5px; break-after: avoid; }
section { break-before: page; }
p { margin: 0 0 7px; }
.sub { color: #5d6877; font-size: 9pt; }
.note { background: #f3f5f8; border-left: 3px solid #2f5fd0; padding: 7px 10px; margin: 6px 0 10px; break-inside: avoid; }
.cover { padding-top: 70mm; }
.cover .tag { color: #2f5fd0; font-weight: 700; font-size: 10.5pt; }
.cards { display: grid; grid-template-columns: repeat(4, 1fr); gap: 7px; margin: 10px 0 12px; }
.card { border: 1px solid #d5dae2; border-radius: 6px; padding: 8px 10px; }
.card b { display: block; font-size: 15pt; line-height: 1.25; }
.card span { color: #5d6877; font-size: 8.4pt; }
.find { break-inside: avoid; margin-bottom: 8px; }
.find b { display: block; font-size: 10.4pt; }
.find i { font-style: normal; color: #2f5fd0; font-weight: 700; margin-right: 5px; }
table { border-collapse: collapse; width: 100%; margin: 4px 0 10px; font-size: 8.5pt; }
th { text-align: left; font-weight: 700; color: #5d6877; border-bottom: 1px solid #1c2430; padding: 3px 5px; white-space: nowrap; }
td { border-bottom: 1px solid #e3e7ed; padding: 3px 5px; vertical-align: middle; }
tr { break-inside: avoid; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
td.r { color: #8791a0; width: 18px; text-align: right; }
td.t { max-width: 0; width: 46%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.bar { display: inline-block; height: 8px; background: #2f5fd0; border-radius: 2px; vertical-align: middle; margin-right: 5px; }
.bar.g { background: #9aa6b8; }
.up { color: #0f8a4f; font-weight: 700; } .down { color: #c2362b; font-weight: 700; }
.two { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.rec { border: 1px solid #d5dae2; border-radius: 6px; padding: 8px 11px; margin-bottom: 7px; break-inside: avoid; }
.rec b.h { font-size: 10.4pt; display: block; margin-bottom: 3px; }
.rec .k { color: #5d6877; font-weight: 700; margin-right: 4px; }
.rec .ex { background: #f3f5f8; padding: 2px 7px; border-radius: 3px; display: inline-block; margin-top: 3px; }
ul { margin: 4px 0 8px; padding-left: 17px; } li { margin-bottom: 3px; }
svg text { font-family: 'Malgun Gothic', sans-serif; }
"""

e = html.escape
num = lambda v: "-" if v is None else f"{v:,.0f}" if float(v).is_integer() else f"{v:,.1f}"
pct = lambda v: f"{v:.2f}%"


def table(headers: list[tuple[str, str]], rows: list[list], rank: bool = True) -> str:
    """headers: [(이름, 정렬 클래스)]. rows의 칸은 이미 HTML."""
    th = ("<th></th>" if rank else "") + "".join(f'<th class="{c}">{e(h)}</th>' for h, c in headers)
    body = ""
    for i, row in enumerate(rows, 1):
        tds = (f'<td class="r">{i}</td>' if rank else "") + "".join(f'<td class="{headers[j][1]}">{v}</td>' for j, v in enumerate(row))
        body += f"<tr>{tds}</tr>"
    return f"<table><tr>{th}</tr>{body}</table>"


def bar(value: float, top: float, width: int = 70, gray: bool = False) -> str:
    w = max(1, round(value / top * width)) if top else 1
    return f'<span class="bar{" g" if gray else ""}" style="width:{w}px"></span>'


def delta(v) -> str:
    if v is None:
        return "-"
    return f'<span class="{"up" if v > 0 else "down" if v < 0 else ""}">{"▲" if v > 0 else "▼" if v < 0 else ""}{abs(v)}%</span>'


def line_chart(monthly: list[dict], key: str, label: str, color: str) -> str:
    w, h, pl, pb, pt = 680, 150, 38, 22, 14
    vals = [m[key] for m in monthly]
    top = max(vals) * 1.12
    x = lambda i: pl + i * (w - pl - 8) / (len(vals) - 1)
    y = lambda v: pt + (h - pt - pb) * (1 - v / top)
    pts = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(vals))
    grid = "".join(f'<line x1="{pl}" x2="{w - 8}" y1="{y(top * f):.1f}" y2="{y(top * f):.1f}" stroke="#e3e7ed"/>'
                   f'<text x="{pl - 5}" y="{y(top * f) + 3:.1f}" font-size="8" fill="#8791a0" text-anchor="end">{top * f:,.0f}</text>'
                   for f in (0.25, 0.5, 0.75))
    ticks = "".join(f'<text x="{x(i):.1f}" y="{h - 6}" font-size="8" fill="#5d6877" text-anchor="middle">{m["month"][2:].replace("-", ".")}</text>'
                    for i, m in enumerate(monthly) if m["month"][5:] in ("01", "04", "07", "10"))
    return (f'<h3>{e(label)}</h3><svg viewBox="0 0 {w} {h}" width="100%">{grid}'
            f'<polyline points="{pts}" fill="none" stroke="{color}" stroke-width="1.8"/>'
            + "".join(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="1.8" fill="{color}"/>' for i, v in enumerate(vals)) + ticks + "</svg>")


def brand_rows(rows: list[dict], key: str, n: int = 15) -> list[list]:
    top = rows[0][key]
    return [[e(s["brand"]), bar(s[key], top, 60) + num(s[key]), num(s["n"])] for s in rows[:n]]


def post_rows(posts: list[dict], n: int) -> list[list]:
    return [[e(p["subject"]), e(p["brand"]), p["date"][2:], num(p["views"]), num(p["comments"]), num(p["likes"])] for p in posts[:n]]


POST_HEAD = [("제목", "t"), ("브랜드", ""), ("날짜", "n"), ("조회", "n"), ("댓글", "n"), ("좋아요", "n")]


def growth_table(rows: list[dict], n: int) -> str:
    return table(
        [("브랜드", ""), ("글 수(전→후)", "n"), ("글당 조회(전→후)", "n"), ("조회 증감", "n"), ("글당 댓글(전→후)", "n"), ("댓글 증감", "n")],
        [[e(g["brand"]), f'{g["n_prev"]} → {g["n_recent"]}', f'{num(g["views_prev"])} → {num(g["views_recent"])}', delta(g["views_growth"]),
          f'{num(g["comments_prev"])} → {num(g["comments_recent"])}', delta(g["comments_growth"])] for g in rows[:n]])


def group_table(rows: list[dict], first: str, extra: bool = False) -> str:
    top = max(r["med_views"] for r in rows)
    head = [(first, ""), ("글 수", "n"), ("글당 조회(중앙값)", ""), ("글당 댓글(중앙값)", "n"), ("반응률", "n")]
    return table(head, [[e(str(r["key"])), num(r["n"]), bar(r["med_views"], top, 90) + num(r["med_views"]),
                         num(r["med_comments"]), pct(r["rate"])] for r in rows], rank=False)


def build_html(d: dict, ins: dict) -> str:
    m, o, bt, notes = d["meta"], d["overall"], d["brand_top"], ins["notes"]
    note = lambda k: f'<div class="note">{e(notes[k])}</div>'
    day = m["fetched_at"][:10]
    S = []

    S.append(f"""<div class="cover"><div class="tag">네이버 카페 '브랜디드' · 브랜드 소식 게시판</div>
<h1>브랜드 소식 게시판 반응 분석</h1>
<p class="sub">어떤 브랜드와 어떤 글이 반응을 얻는가 — 미치코런던 남성복 런칭을 위한 참고 자료</p>
<p class="sub">분석 기간 {m['from']} ~ {m['to']} · 글 {m['posts']:,}개 · 브랜드 {m['brands']:,}곳 · 수집일 {day}</p></div>""")

    # 1. 요약
    cards = [(f"{m['posts']:,}개", "분석한 글"), (f"{m['brands']:,}곳", f"브랜드 (순위 대상 {m['ranked_brands']}곳)"),
             (f"{o['med_views']:,}회", f"글당 조회 중앙값 (평균 {o['avg_views']:,})"), (f"{num(o['med_comments'])}개", f"글당 댓글 중앙값 (평균 {num(o['avg_comments'])})"),
             (pct(o["rate"]), "반응률 = (댓글+좋아요)÷조회"), (f"{d['percentiles']['views'][8]:,}회", "상위 10% 글의 조회수 기준"),
             (f"{d['percentiles']['comments'][8]}개", "상위 10% 글의 댓글 기준"), (f"{m['official_share']}%", "브랜드 공식 계정이 쓴 글")]
    S.append("<section><h2>1. 한눈에 요약</h2><div class='cards'>" + "".join(f"<div class='card'><b>{a}</b><span>{e(b)}</span></div>" for a, b in cards) + "</div>"
             + "<h3>핵심 발견 5가지</h3>" + "".join(f"<div class='find'><b><i>{i}</i>{e(f['title'])}</b>{e(f['text'])}</div>" for i, f in enumerate(ins["findings"], 1))
             + "</section>")

    # 2. 흐름
    S.append("<section><h2>2. 게시판 흐름 (월별)</h2>" + note("monthly")
             + line_chart(d["monthly"][:-1], "n", "월별 글 수", "#1c2430")
             + line_chart(d["monthly"][:-1], "med_views", "글 1개당 조회수 (중앙값)", "#2f5fd0")
             + line_chart(d["monthly"][:-1], "med_comments", "글 1개당 댓글수 (중앙값)", "#0f8a4f")
             + "<h3>연도별</h3>" + table([("연도", ""), ("글 수", "n"), ("글당 조회(중앙값)", "n"), ("글당 댓글(중앙값)", "n"), ("글당 좋아요(평균)", "n"), ("반응률", "n")],
                                      [[y["key"] + ("년 (10월 초까지)" if y["key"] == day[:4] else "년"), num(y["n"]), num(y["med_views"]), num(y["med_comments"]),
                                        num(y["avg_likes"]), pct(y["rate"])] for y in d["yearly"]], rank=False)
             + "</section>")

    # 3. 브랜드 조회·댓글
    bh = lambda label: [("브랜드", ""), (label, ""), ("글 수", "n")]
    S.append("<section><h2>3. 조회수·댓글이 많은 브랜드</h2>" + note("brand_total")
             + "<div class='two'><div><h3>누적 조회수 TOP 15</h3>" + table(bh("누적 조회"), brand_rows(bt["views_total"], "views"))
             + "</div><div><h3>글 1개당 조회수 TOP 15 (중앙값)</h3>" + table(bh("글당 조회"), brand_rows(bt["views_per_post"], "med_views"))
             + "</div></div><div class='two'><div><h3>누적 댓글수 TOP 15</h3>" + table(bh("누적 댓글"), brand_rows(bt["comments_total"], "comments"))
             + "</div><div><h3>글 1개당 댓글수 TOP 15 (중앙값)</h3>" + table(bh("글당 댓글"), brand_rows(bt["comments_per_post"], "med_comments"))
             + f"</div></div><p class='sub'>글 {m['min_posts']}개 이상 브랜드 {m['ranked_brands']}곳 대상. 카페 운영진('브랜디드') 글 제외.</p></section>")

    # 4. 글 TOP
    tp = d["top_posts"]
    S.append("<section><h2>4. 조회수·댓글이 많은 글</h2>" + note("top_posts")
             + "<h3>조회수 TOP 30</h3>" + table(POST_HEAD, post_rows(tp["views"], 30))
             + "<h3>댓글수 TOP 30</h3>" + table(POST_HEAD, post_rows(tp["comments"], 30))
             + "<h3>조회 대비 반응률 TOP 15 (조회 500회 이상 글)</h3>" + table(POST_HEAD, post_rows(tp["rate"], 15))
             + "<h3>최근 12개월 조회수 TOP 15</h3>" + table(POST_HEAD, post_rows(tp["views_recent12"], 15)) + "</section>")

    # 5. 반응 좋은 브랜드 TOP 30
    S.append("<section><h2>5. 반응이 좋은 브랜드 TOP 30</h2>"
             "<p>글당 조회수, 글당 댓글수, 반응률 세 가지를 각각 175개 브랜드 안에서 순위(백분위)로 바꾼 뒤 평균한 종합 점수입니다. "
             f"세 가지가 고르게 높아야 점수가 높습니다(100점 만점). 지금 참고할 수 있도록 최근 1년 안에 글을 {m['active_min']}개 이상 올린 브랜드만 넣었습니다.</p>" + note("score")
             + table([("브랜드", ""), ("종합 점수", ""), ("글 수", "n"), ("최근 1년 글", "n"), ("글당 조회", "n"), ("글당 댓글", "n"), ("반응률", "n"),
                      ("2천회↑ 글 비율", "n"), ("주로 올리는 글", "")],
                     [[f"<b>{e(s['brand'])}</b>", bar(s["score"], 100, 50) + f"{s['score']:.1f}", num(s["n"]), num(s["n_12m"]), num(s["med_views"]),
                       num(s["med_comments"]), pct(s["rate"]), f"{s['hit_rate']}%", e(s["top_type"])] for s in bt["score_active"][:30]])
             + "<p class='sub'>글당 조회·댓글은 중앙값. '주로 올리는 글'은 제목 단어로 분류한 유형 중 가장 많은 것('기타'는 특정 단어 없이 쓴 글). "
               "</p>"
             + "<p class='sub'><b>점수는 높지만 최근 1년 활동이 거의 없어 뺀 브랜드:</b> "
             + ", ".join(f"{e(s['brand'])}({s['score']:.1f}점, 글당 조회 {num(s['med_views'])})" for s in bt["score_dormant"]) + "</p>"
             + "<h3>반응률만 봤을 때 TOP 20</h3>" + note("rate")
             + table([("브랜드", ""), ("반응률", ""), ("댓글만 ÷ 조회", "n"), ("글 수", "n"), ("글당 조회", "n"), ("글당 댓글", "n")],
                     [[e(s["brand"]), bar(s["rate"], bt["rate"][0]["rate"], 70) + pct(s["rate"]), pct(s["comment_rate"]), num(s["n"]), num(s["med_views"]),
                       num(s["med_comments"])] for s in bt["rate"][:20]]) + "</section>")

    # 6. 성장
    yoy, g6 = d["growth_yoy"], d["growth"]
    gp, gr = m["growth_prev"], m["growth_recent"]
    S.append("<section><h2>6. 반응이 빠르게 커지는 브랜드</h2>" + note("growth")
             + f"<h3>작년 대비 — {int(day[:4]) - 1}년 → {day[:4]}년 (해마다 글 {m['yoy_min']}개 이상, {len(yoy)}곳 중 상위 15)</h3>" + growth_table(yoy, 15)
             + f"<h3>최근 6개월 — {gp[0]}~{gp[1]} → {gr[0]}~{gr[1]} (기간마다 글 {m['growth_min']}개 이상, {len(g6)}곳 중 상위 10)</h3>" + growth_table(g6, 10)
             + f"<h3>새로 등장해 빠르게 자리 잡은 브랜드 (첫 글 {m['new_since']} 이후, 글 5개 이상)</h3>" + note("newcomers")
             + table([("브랜드", ""), ("첫 글", "n"), ("글 수", "n"), ("글당 조회", ""), ("글당 댓글", "n"), ("반응률", "n")],
                     [[e(s["brand"]), s["first"], num(s["n"]), bar(s["med_views"], d["newcomers"][0]["med_views"], 70) + num(s["med_views"]),
                       num(s["med_comments"]), pct(s["rate"])] for s in d["newcomers"][:12]])
             + "<h3>반응이 내려간 브랜드 (작년 대비 하위 6)</h3>" + growth_table(sorted(yoy, key=lambda g: g["views_growth"])[:6], 6) + "</section>")

    # 7. 콘텐츠 유형
    by_views = lambda rows: sorted(rows, key=lambda r: -r["med_views"])
    feats = table([("제목 특징", ""), ("해당 글 수", "n"), ("해당 글 조회", "n"), ("그 외 글 조회", "n"), ("해당 글 댓글", "n"), ("그 외 글 댓글", "n")],
                  [[e(f["key"]), num(f["yes"]["n"]), num(f["yes"]["med_views"]), num(f["no"]["med_views"]), num(f["yes"]["med_comments"]),
                    num(f["no"]["med_comments"])] for f in d["title_feats"]], rank=False)
    S.append("<section><h2>7. 콘텐츠 유형별 성과</h2>" + note("types")
             + "<h3>제목 단어로 나눈 유형 (글당 조회 높은 순)</h3>" + group_table(by_views(d["types"]), "유형")
             + "<h3>게시판 말머리별</h3>" + group_table(by_views(d["heads"]), "말머리")
             + "<h3>올린 시간대·요일</h3>" + note("timing")
             + "<div class='two'><div>" + group_table(d["hours"], "시간대") + "</div><div>" + group_table(d["weekday"], "요일") + "</div></div>"
             + "<h3>제목 형식</h3>" + note("titles") + feats + "</section>")

    # 8. 공통점
    S.append("<section><h2>8. 잘 되는 글의 공통점</h2><p class='sub'>조회·댓글·반응률 상위 글과 반응 좋은 브랜드의 글 흐름을 직접 읽고 정리했습니다.</p>"
             + "".join(f"<div class='find'><b><i>{i}</i>{e(p['title'])}</b>{e(p['text'])}</div>" for i, p in enumerate(ins["patterns"], 1)) + "</section>")

    # 9. 추천
    S.append("<section><h2>9. 실행 추천 콘텐츠 — 미치코런던 남성복</h2>"
             "<p>런칭 전후 순서대로 정리했습니다. 근거 숫자는 모두 이 게시판의 실제 글입니다.</p>"
             + "".join(f"<div class='rec'><b class='h'>{e(r['title'])}</b><div><span class='k'>무엇을</span>{e(r['what'])}</div>"
                       f"<div><span class='k'>근거</span>{e(r['why'])}</div><div class='ex'><span class='k'>제목 예</span>{e(r['title_ex'])}</div></div>" for r in ins["recs"])
             + "<h3>올리는 주기와 시간</h3><p>" + e(ins["rhythm"]) + "</p>"
             + "<h3>하지 말 것</h3><ul>" + "".join(f"<li>{e(x)}</li>" for x in ins["donts"]) + "</ul>"
             + f"<div class='note'>{e(ins['check'])}</div></section>")

    # 10. 기준·한계
    S.append("<section><h2>10. 데이터 기준과 한계</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in ins["limits"]) + "</ul>"
             + "<h3>참고: 카페 운영진('브랜디드') 글 조회수 TOP 10</h3>" + table(POST_HEAD, post_rows(tp["operator"], 10)) + "</section>")

    return f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><title>브랜디드 브랜드 소식 분석</title><style>{CSS}</style></head><body>{''.join(S)}</body></html>"


def to_pdf(html_path: Path, pdf_path: Path) -> None:
    edge = next((p for p in EDGE_PATHS if Path(p).exists()), None)
    if not edge:
        sys.exit("Edge를 찾지 못함 — PDF로 바꾸지 못했습니다. HTML: " + str(html_path))
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    if pdf_path.exists():
        pdf_path.unlink()
    with tempfile.TemporaryDirectory() as profile:
        subprocess.run([edge, "--headless=new", "--disable-gpu", "--no-pdf-header-footer", f"--user-data-dir={profile}",
                        f"--print-to-pdf={pdf_path}", html_path.as_uri()], check=False, capture_output=True, timeout=180)
        for _ in range(60):  # Edge는 바로 끝난 것처럼 돌아오고 파일은 몇 초 뒤 생김
            if pdf_path.exists() and pdf_path.stat().st_size > 0:
                return
            time.sleep(1)
    sys.exit("PDF가 만들어지지 않음. HTML: " + str(html_path))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    d = load_json(CAFE_DIR / "analysis.json", None)
    ins = load_json(CAFE_DIR / "insights.json", None)
    if not d or not ins:
        sys.exit("analysis.json(cafe_analyze.py) 또는 insights.json(Claude 작성)이 없습니다.")
    html_path = CAFE_DIR / "report.html"
    html_path.write_text(build_html(d, ins), encoding="utf-8")
    out = Path(args.out) if args.out else ROOT / "output" / f"브랜디드_브랜드소식_분석_{d['meta']['fetched_at'][:10]}.pdf"
    to_pdf(html_path, out)
    print(f"PDF 저장: {out} ({out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
