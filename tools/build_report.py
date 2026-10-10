"""리포트 Tool — 판정표(data/article_labels.json)를 모아 주간 웹 리포트(docs/)를 만든다.

- [여성 | 남성] × [전체 | 국내 | 해외] 전환 (여성 = 여성+공용 기사, 남성 = 남성+공용 기사)
- 시각 참고 중심 (2026-09-26 사장님 요청): 트렌드마다 무드보드, 키워드·컬러마다 사진 줄, 런웨이 사진 카드, 기사 사진 카드
- 사진 파일은 저장하지 않고 매체 주소를 그대로 보여줌(저작권). 글은 제목 + Claude 요약 + 원문 링크만.

- 주간(매주 자동): docs/index.html, docs/reports/날짜.html / 월간(과거 1~8월, 2026-09-26 사장님 결정): docs/monthly/index.html, docs/monthly/reports/YYYY-MM.html
  맨 위 [주간 | 월간] 전환으로 같은 링크에서 오감.

사용법: python tools/build_report.py [--date YYYY-MM-DD]      주간 (그 날까지 7일)
        python tools/build_report.py --month YYYY-MM          월간
"""
from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARTICLES_PATH, DATA_DIR, DOCS_DIR, LABELS_PATH, NOTES_PATH, SOURCES_PATH, load_json, today_kst

MONTHLY_NOTES_PATH = DATA_DIR / "monthly_notes.json"  # 월간 핵심 트렌드 문장 (Claude 작성), 키 = YYYY-MM

LIST_FIELDS = ("items", "styles", "details", "colors", "materials", "brands")

# 리포트에서 빼는 아이템 (2026-10-11 사장님 결정): 스니커즈는 늘 많이 언급되는 베이직 아이템이라 트렌드로 보지 않음.
# 이 아이템만 다룬 기사(신상 발매 소식 등)는 리포트에서 통째로 빼고, 다른 아이템과 같이 나온 기사는 이 이름표만 뗌.
# 판정표(data/article_labels.json)는 그대로 둠 — 여기서 이름을 빼면 다음 리포트부터 다시 나옴.
EXCLUDE_ITEMS = {"스니커즈"}

# 컬러 이름표 → 견본 색 (무신사 리포트와 같은 이름)
SWATCH = {"블랙": "#111111", "화이트": "#f7f7f5", "아이보리/크림": "#efe6d2", "그레이/차콜": "#6e6e6e", "네이비": "#1f2a4a",
          "블루/인디고": "#3b5ea8", "브라운/카멜": "#8a5a36", "베이지/샌드": "#d6c3a0", "카키/올리브": "#6b6b3a",
          "그린/민트": "#3f8f5f", "레드/버건디": "#8e1f2c", "핑크": "#e79ab5", "퍼플/라벤더": "#8a6bb8",
          "옐로우/머스타드": "#d8a824", "오렌지": "#e0702a",
          "메탈릭(실버·골드)": "linear-gradient(135deg,#c9c9c9,#f1e3a8 50%,#b9b9b9)",
          "멀티컬러/비비드": "conic-gradient(#e0402a,#e0c02a,#3fb04f,#2a78d6,#9a4ad6,#e0402a)"}


def window(date: str, days: int = 7, back: int = 0) -> tuple[str, str]:
    end = datetime.fromisoformat(date) - timedelta(days=back)
    return (end - timedelta(days=days - 1)).strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


def month_span(month: str, back: int = 0) -> tuple[str, str]:
    y, m = map(int, month.split("-"))
    m -= back
    while m < 1:
        y, m = y - 1, m + 12
    first = datetime(y, m, 1)
    last = (first + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    return first.strftime("%Y-%m-%d"), last.strftime("%Y-%m-%d")


def span(key: str, back: int = 0) -> tuple[str, str]:
    """리포트 키 → 기간. 'YYYY-MM' = 월간, 'YYYY-MM-DD' = 주간(그 날까지 7일)."""
    return month_span(key, back) if len(key) == 7 else window(key, back=7 * back)


def rows_for(articles: dict, labels: dict, start: str, end: str, full: bool) -> list[dict]:
    out = []
    for aid, lab in labels.items():
        a = articles.get(aid)
        if not a or not lab.get("fashion") or not (start <= a["date"] <= end):
            continue
        items = lab.get("items", [])
        if items and all(i in EXCLUDE_ITEMS for i in items):
            continue
        row = {"g": lab["gender"], "r": a["region"], **{f[:3]: lab.get(f, []) for f in LIST_FIELDS}}
        row["ite"] = [i for i in items if i not in EXCLUDE_ITEMS]
        if full:
            row.update({"id": aid, "t": a["title"], "s": a["source_name"], "d": a["date"], "u": a["link"],
                        "img": a.get("image", ""), "sum": lab["summary"], "k": lab["article_kind"],
                        "sea": lab.get("season", "")})
        out.append(row)
    return sorted(out, key=lambda r: r.get("d", ""), reverse=True)


def clean_notes(notes: dict) -> dict:
    """핵심 트렌드 문장에서 빼는 아이템 이름표를 떼고, 그것만 다룬 트렌드는 뺌 (안전장치)."""
    out = dict(notes)
    for g in ("여성", "남성"):
        kept = [{**n, "keys": [k for k in n["keys"] if k not in EXCLUDE_ITEMS]} for n in notes.get(g, [])]
        out[g] = [n for n in kept if n["keys"]]
    return out


CSS = """
:root { color-scheme: light;
  --page:#f9f9f7; --surface:#fcfcfb; --ink:#0b0b0b; --ink-2:#52514e; --muted:#898781; --grid:#e1e0d9;
  --border:rgba(11,11,11,.10); --accent:#2a78d6; --up:#006300; --chip:rgba(42,120,214,.12); --shade:rgba(0,0,0,.55); }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
  --border:rgba(255,255,255,.10); --accent:#3987e5; --up:#0ca30c; --chip:rgba(57,135,229,.25); } }
:root[data-theme="dark"] { color-scheme: dark;
  --page:#0d0d0d; --surface:#1a1a19; --ink:#fff; --ink-2:#c3c2b7; --muted:#898781; --grid:#2c2c2a;
  --border:rgba(255,255,255,.10); --accent:#3987e5; --up:#0ca30c; --chip:rgba(57,135,229,.25); }
* { box-sizing: border-box; } [hidden] { display: none !important; }
body { margin:0; background:var(--page); color:var(--ink);
  font:15px/1.55 system-ui,-apple-system,"Segoe UI","Apple SD Gothic Neo","Malgun Gothic",sans-serif; }
a { color: inherit; }
.wrap { max-width: 1160px; margin: 0 auto; padding: 28px 16px 64px; }
header { display:flex; flex-wrap:wrap; gap:12px 24px; align-items:flex-end; justify-content:space-between; }
header h1 { font-size:26px; margin:0 0 4px; letter-spacing:-.01em; }
header p { margin:0; color:var(--ink-2); }
.hright { display:flex; flex-wrap:wrap; gap:12px; align-items:flex-end; }
.pill a.pl { font-weight:700; padding:6px 14px; border-radius:999px; text-decoration:none; color:var(--ink-2); }
.pill a.pl[aria-current] { background:var(--accent); color:#fff; }
.datepick { display:flex; flex-direction:column; gap:4px; font-size:12px; color:var(--muted); }
.datepick select { font:inherit; font-size:15px; font-weight:600; color:var(--ink); background:var(--surface);
  border:1px solid var(--border); border-radius:10px; padding:8px 12px; min-width:170px; }
.topbar { position:sticky; top:0; z-index:5; background:var(--page); margin:20px 0 18px; padding:10px 0;
  display:flex; flex-wrap:wrap; gap:10px 12px; align-items:center; border-bottom:1px solid var(--grid); }
.pill { display:inline-flex; padding:3px; border:1px solid var(--border); border-radius:999px; background:var(--surface); }
.pill button { font:inherit; font-weight:700; padding:6px 14px; border:0; border-radius:999px; cursor:pointer;
  background:transparent; color:var(--ink-2); }
.pill button[aria-pressed="true"] { background:var(--ink); color:var(--page); }
.tabs { display:flex; gap:6px; flex:1 1 100%; overflow-x:auto; scrollbar-width:none; }
.tabs button { flex:none; white-space:nowrap; font:inherit; font-weight:600; padding:7px 14px; border-radius:999px;
  cursor:pointer; border:1px solid var(--border); background:var(--surface); color:var(--ink-2); }
.tabs button[aria-selected="true"] { background:var(--ink); color:var(--page); border-color:var(--ink); }
button:focus-visible, a:focus-visible, select:focus-visible { outline:2px solid var(--accent); outline-offset:2px; }
section.card { background:var(--surface); border:1px solid var(--border); border-radius:14px; padding:20px; margin-bottom:18px; }
section.card h2 { font-size:19px; margin:0 0 4px; }
.sub { font-size:13px; color:var(--muted); margin:0 0 14px; }
.stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(140px,100%),1fr)); gap:10px; }
.stat { border:1px solid var(--border); border-radius:12px; padding:14px; }
.stat .k { font-size:12px; color:var(--muted); } .stat .v { font-size:20px; font-weight:700; margin:2px 0; }
.stat .d { font-size:13px; color:var(--ink-2); }
/* 트렌드 무드보드 */
.trends { display:grid; gap:18px; }
.trend { display:grid; grid-template-columns:minmax(0,5fr) minmax(0,7fr); gap:16px; align-items:start;
  border:1px solid var(--border); border-radius:14px; padding:14px; }
.trend h3 { font-size:17px; margin:0 0 6px; } .trend p { margin:0 0 8px; color:var(--ink-2); font-size:14px; }
.board { display:grid; grid-template-columns:repeat(3,1fr); grid-auto-rows:120px; gap:6px; }
.board a:first-child { grid-row:span 2; grid-column:span 1; }
.board a { position:relative; border-radius:8px; overflow:hidden; background:var(--grid); display:block; }
.board img, .strip img, .acard img, .rcard img { width:100%; height:100%; object-fit:cover; display:block; }
.board span { position:absolute; left:0; right:0; bottom:0; padding:14px 6px 4px; font-size:11px; color:#fff;
  background:linear-gradient(transparent,var(--shade)); }
.chips { display:flex; flex-wrap:wrap; gap:5px; }
.chip { font-size:12px; padding:2px 9px; border-radius:999px; background:var(--chip); color:var(--ink); border:0; }
button.chip { cursor:pointer; font:inherit; font-size:12px; }
button.chip[aria-pressed="true"] { background:var(--ink); color:var(--page); }
/* 키워드 순위 + 사진 줄 */
.kwlist { display:grid; gap:10px; }
.kw { display:grid; grid-template-columns:28px minmax(110px,180px) minmax(0,1fr); gap:10px; align-items:center;
  padding:8px 0; border-top:1px solid var(--grid); }
.kw:first-child { border-top:0; }
.kw .rk { font-weight:700; color:var(--muted); font-variant-numeric:tabular-nums; text-align:right; }
.kw .nm { font-weight:700; } .kw .nm small { display:block; font-weight:400; color:var(--muted); font-size:12px; }
.kw .nm .new { color:var(--up); font-weight:700; }
.bar { height:6px; border-radius:3px; background:var(--accent); margin-top:4px; }
.strip { display:flex; gap:6px; overflow-x:auto; scrollbar-width:thin; }
.strip a { flex:none; width:86px; height:108px; border-radius:8px; overflow:hidden; background:var(--grid); }
.sw { width:22px; height:22px; border-radius:50%; border:1px solid var(--border); display:inline-block;
  vertical-align:middle; margin-right:6px; }
.twocol { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(460px,100%),1fr)); gap:18px; }
/* 런웨이·기사 카드 */
.rgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(260px,100%),1fr)); gap:14px; }
.rcard, .acard { border:1px solid var(--border); border-radius:12px; overflow:hidden; background:var(--surface);
  display:flex; flex-direction:column; text-decoration:none; }
.rcard .ph { aspect-ratio:3/4; background:var(--grid); } .acard .ph { aspect-ratio:4/3; background:var(--grid); }
.rcard .bd, .acard .bd { padding:10px 12px 12px; display:flex; flex-direction:column; gap:5px; }
.meta { font-size:12px; color:var(--muted); }
.ttl { font-size:13px; color:var(--ink-2); display:-webkit-box; -webkit-line-clamp:2; -webkit-box-orient:vertical; overflow:hidden; }
.sum { font-size:14px; }
.brand { font-weight:700; font-size:15px; }
.agrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(min(240px,100%),1fr)); gap:14px; }
.filters { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:14px; }
.empty { color:var(--muted); font-size:14px; }
footer { margin-top:28px; font-size:12px; color:var(--muted); }
@media (max-width: 720px) {
  .trend { grid-template-columns:1fr; } .board { grid-auto-rows:96px; }
  .kw { grid-template-columns:24px 1fr; } .kw .strip { grid-column:1 / -1; }
}
"""

JS = r"""
const D = JSON.parse(document.getElementById('data').textContent);
const SW = D.swatch;
const state = { g: '여성', r: '전체', tab: 'sum', kind: '전체' };
try { Object.assign(state, JSON.parse(localStorage.getItem('mag-view') || '{}')); } catch (e) {}
// 주소 끝 #남성/rw/해외 처럼 성별/탭/지역으로 특정 화면 공유
const H = decodeURIComponent(location.hash.slice(1)).split('/');
if (['여성', '남성'].includes(H[0])) state.g = H[0];
if (['sum', 'kw', 'cm', 'rw', 'art'].includes(H[1])) state.tab = H[1];
if (['전체', '국내', '해외'].includes(H[2])) state.r = H[2];
const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
// 같은 기사가 두 판(하입비스트 영문·코리아 등)에 실리면 한 번만: 같은 사진 + 같은 첫 브랜드면 같은 기사로 봄
// (사진만 같고 브랜드가 다르면 다른 기사 — W 코리아가 케이트·캘빈클라인에 같은 썸네일을 붙인 경우)
function dedupe(rows) {
  const seen = new Map();
  return rows.filter(a => {
    if (!a.img) return true;
    const k = imgKey(a.img) + '|' + ((a.bra || [])[0] || '');
    if (seen.has(k)) { const f = seen.get(k); if (a.s && !f.also.includes(a.s)) f.also.push(a.s); return false; }
    a.also = []; seen.set(k, a); return true;
  });
}
function pick(rows) { return dedupe(rows.filter(a => (a.g === state.g || a.g === '공용') && (state.r === '전체' || a.r === state.r)).map(a => ({...a}))); }
const FIELDS = {items:'ite', styles:'sty', details:'det', colors:'col', materials:'mat', brands:'bra'};
function count(rows, f) {
  const m = new Map();
  rows.forEach(a => (a[FIELDS[f]] || []).forEach(k => m.set(k, (m.get(k) || 0) + 1)));
  return [...m.entries()].sort((x, y) => y[1] - x[1]);
}
const img = (a, cls='') => a.img ? `<img src="${esc(a.img)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.remove()">` : '';
const has = (a, keys) => keys.some(k => Object.values(FIELDS).some(f => (a[f] || []).includes(k)));
// 같은 사진(크기 변형 포함)은 한 번만 — 매체가 두 기사에 같은 썸네일을 붙이는 경우가 있음(W 코리아 케이트·캘빈클라인)
// 하입비스트 영문·코리아판은 같은 사진을 다른 주소(인코딩된 원본 주소)로 씀 → 풀어서 파일 이름으로 비교
const imgKey = u => { let x = u || ''; try { x = decodeURIComponent(x); } catch (e) {}
  return x.split('?')[0].split('/').pop().replace(/-\d+x\d+(?=\.\w+$)/, '').toLowerCase(); };
function uniq(list) { const seen = new Set(); return list.filter(a => { const k = imgKey(a.img); if (seen.has(k)) return false; seen.add(k); return true; }); }
const hits = (a, keys) => keys.filter(k => Object.values(FIELDS).some(f => (a[f] || []).includes(k))).length;
// 키워드가 많이 겹치는 기사 사진 먼저 (관련도 순), skip = 이미 다른 트렌드에 쓴 사진
function photos(rows, keys, n, skip) {
  const list = uniq(rows.filter(a => a.img && has(a, keys) && !(skip && skip.has(imgKey(a.img)))));
  return list.map((a, i) => [a, hits(a, keys), i]).sort((x, y) => y[1] - x[1] || x[2] - y[2]).map(x => x[0]).slice(0, n);
}
function strip(list) {
  return `<div class="strip">${list.map(a => `<a href="${esc(a.u)}" target="_blank" rel="noopener" title="${esc(a.s + ' · ' + a.t)}">${img(a)}</a>`).join('')}</div>`;
}
function kwBlock(rows, prev, f, title, sub, n=10, swatch=false) {
  const list = count(rows, f).slice(0, n);
  if (!list.length) return `<section class="card"><h2>${title}</h2><p class="empty">이 조건의 기사가 없어요.</p></section>`;
  const pm = new Map(count(prev, f)); const max = list[0][1];
  const hasPrev = prev.length > 0;
  return `<section class="card"><h2>${title}</h2><p class="sub">${sub}</p><div class="kwlist">${list.map(([k, c], i) => {
    const p = pm.get(k) || 0;
    const delta = !hasPrev ? '' : p === 0 ? ' <span class="new">새로 등장</span>' : c > p ? ` <span class="new">▲${c - p}</span>` : '';
    const sw = swatch ? `<span class="sw" style="background:${SW[k] || 'var(--grid)'}"></span>` : '';
    return `<div class="kw"><div class="rk">${i + 1}</div><div class="nm">${sw}${esc(k)}<small>기사 ${c}개${delta}</small>
      <div class="bar" style="width:${Math.max(8, 100 * c / max)}%"></div></div>${strip(photos(rows, [k], 8))}</div>`;
  }).join('')}</div></section>`;
}
function summary(rows, prev) {
  const notes = (D.notes || {})[state.g] || [];
  const top = f => (count(rows, f)[0] || ['-', 0]);
  const [ti, tc] = top('items'), [cl, cc] = top('colors'), [mt, mc] = top('materials'), [st, sc] = top('styles');
  const srcs = new Set(rows.map(a => a.s)).size;
  let h = `<section class="card"><h2>${D.u.cur} 한눈에</h2><p class="sub">${esc(D.period_note || '')}</p><div class="stats">
    <div class="stat"><div class="k">패션 기사</div><div class="v">${rows.length}개</div><div class="d">${srcs}개 매체</div></div>
    <div class="stat"><div class="k">가장 많이 나온 아이템</div><div class="v">${esc(ti)}</div><div class="d">기사 ${tc}개</div></div>
    <div class="stat"><div class="k">스타일</div><div class="v">${esc(st)}</div><div class="d">기사 ${sc}개</div></div>
    <div class="stat"><div class="k">컬러</div><div class="v"><span class="sw" style="background:${SW[cl] || 'var(--grid)'}"></span>${esc(cl)}</div><div class="d">기사 ${cc}개</div></div>
    <div class="stat"><div class="k">소재</div><div class="v">${esc(mt)}</div><div class="d">기사 ${mc}개</div></div>
  </div></section>`;
  h += `<section class="card"><h2>${D.u.cur} 핵심 트렌드 · ${esc(state.g)}복</h2><p class="sub">매체 기사를 읽고 정리한 흐름. 사진을 누르면 원문 기사로 이동해요.</p><div class="trends">`;
  const used = new Set();
  h += notes.map(n => {
    const ph = photos(rows, n.keys, 8, used);
    ph.forEach(a => used.add(imgKey(a.img)));
    return `<div class="trend"><div><h3>${esc(n.title)}</h3><p>${esc(n.text)}</p><div class="chips">${n.keys.map(k => `<span class="chip">${esc(k)}</span>`).join('')}</div></div>
      <div class="board">${ph.length ? ph.map(a => `<a href="${esc(a.u)}" target="_blank" rel="noopener" title="${esc(a.t)}">${img(a)}<span>${esc(a.s)}</span></a>`).join('') : '<p class="empty">이 조건에 맞는 사진이 없어요.</p>'}</div></div>`;
  }).join('') || `<p class="empty">${D.u.cur} 정리 문장이 없어요.</p>`;
  return h + '</div></section>';
}
function runway(rows) {
  const rw = rows.filter(a => a.k === '런웨이·컬렉션');
  const by = new Map();
  rw.forEach(a => { const b = (a.bra || [])[0] || a.s; if (!by.has(b)) by.set(b, []); by.get(b).push(a); });
  const usedImg = new Set();
  const cards = [...by.entries()].sort((x, y) => y[1].length - x[1].length).map(([b, list]) => {
    const withImg = list.find(x => x.img && !usedImg.has(imgKey(x.img)));
    const a = withImg || { ...list[0], img: '' };
    if (withImg) usedImg.add(imgKey(withImg.img));
    const kws = [...new Set(list.flatMap(x => [...(x.sty || []), ...(x.ite || []), ...(x.mat || []), ...(x.det || [])]))].slice(0, 8);
    return `<a class="rcard" href="${esc(a.u)}" target="_blank" rel="noopener"><div class="ph">${img(a)}</div><div class="bd">
      <div class="brand">${esc(b)}</div><div class="meta">${esc(a.sea || '')} · 리뷰 ${list.length}개 · ${esc([...new Set(list.map(x => x.s))].join(', '))}</div>
      <div class="sum">${esc(a.sum)}</div><div class="chips">${kws.map(k => `<span class="chip">${esc(k)}</span>`).join('')}</div></div></a>`;
  });
  return `<section class="card"><h2>런웨이 · 컬렉션</h2><p class="sub">${D.u.cur} 컬렉션 리뷰를 브랜드별로 묶음 (리뷰 많은 순). 카드를 누르면 대표 리뷰 원문.</p>
    ${cards.length ? `<div class="rgrid">${cards.join('')}</div>` : '<p class="empty">이 조건의 런웨이 기사가 없어요.</p>'}</section>`
    + kwBlock(rw, [], 'styles', '런웨이에서 나온 스타일', '컬렉션 리뷰에 나온 스타일 순위', 8)
    + kwBlock(rw, [], 'items', '런웨이에서 나온 아이템', '컬렉션 리뷰에 나온 아이템 순위', 8);
}
function articles(rows) {
  const kinds = ['전체', ...D.kinds];
  const list = rows.filter(a => state.kind === '전체' || a.k === state.kind);
  return `<section class="card"><h2>기사 모음</h2><p class="sub">제목은 원문 그대로, 아래는 디자이너용 요약. 카드를 누르면 원문으로 이동.</p>
    <div class="filters">${kinds.map(k => `<button class="chip" data-kind="${esc(k)}" aria-pressed="${state.kind === k}">${esc(k)} ${k === '전체' ? rows.length : rows.filter(a => a.k === k).length}</button>`).join('')}</div>
    <div class="agrid">${list.map(a => `<a class="acard" href="${esc(a.u)}" target="_blank" rel="noopener"><div class="ph">${img(a)}</div><div class="bd">
      <div class="meta">${esc(a.s)}${a.also && a.also.length ? ' 외 ' + esc(a.also.join(', ')) : ''} · ${esc(a.r)} · ${esc(a.d.slice(5).replace('-', '/'))} · ${esc(a.k)}</div>
      <div class="sum">${esc(a.sum)}</div><div class="ttl">${esc(a.t)}</div>
      <div class="chips">${[...(a.ite || []), ...(a.col || []), ...(a.mat || [])].slice(0, 5).map(k => `<span class="chip">${esc(k)}</span>`).join('')}</div></div></a>`).join('') || '<p class="empty">기사가 없어요.</p>'}</div></section>`;
}
function render() {
  try { localStorage.setItem('mag-view', JSON.stringify(state)); } catch (e) {}
  history.replaceState(null, '', '#' + [state.g, state.tab, state.r].join('/'));
  document.querySelectorAll('[data-g]').forEach(b => b.setAttribute('aria-pressed', b.dataset.g === state.g));
  document.querySelectorAll('[data-r]').forEach(b => b.setAttribute('aria-pressed', b.dataset.r === state.r));
  document.querySelectorAll('[data-tab]').forEach(b => b.setAttribute('aria-selected', b.dataset.tab === state.tab));
  const rows = pick(D.rows), prev = pick(D.prev);
  const v = document.getElementById('view');
  const prevNote = prev.length ? D.u.prev + ' 대비 늘어난 만큼 ▲ 표시.' : D.u.prev + ' 기록이 쌓이면 변화(▲·새로 등장)가 표시돼요.';
  if (state.tab === 'sum') v.innerHTML = summary(rows, prev);
  else if (state.tab === 'kw') v.innerHTML = kwBlock(rows, prev, 'items', '뜨는 아이템', '기사에 가장 많이 나온 아이템. ' + prevNote)
      + kwBlock(rows, prev, 'styles', '스타일 키워드', '기사에 가장 많이 나온 스타일·무드. ' + prevNote)
      + kwBlock(rows, prev, 'details', '디테일·패턴', '자주 언급된 디테일과 패턴.', 8);
  else if (state.tab === 'cm') v.innerHTML = `<div class="twocol">${kwBlock(rows, prev, 'colors', '컬러', '기사에 언급된 컬러 순위 (이름은 무신사 리포트와 같음)', 12, true)}
      ${kwBlock(rows, prev, 'materials', '원단·소재', '기사에 언급된 원단·소재 순위', 12)}</div>`;
  else if (state.tab === 'rw') v.innerHTML = runway(rows);
  else v.innerHTML = articles(rows);
  v.querySelectorAll('[data-kind]').forEach(b => b.onclick = () => { state.kind = b.dataset.kind; render(); });
}
document.querySelectorAll('[data-g]').forEach(b => b.onclick = () => { state.g = b.dataset.g; render(); });
document.querySelectorAll('[data-r]').forEach(b => b.onclick = () => { state.r = b.dataset.r; render(); });
document.querySelectorAll('[data-tab]').forEach(b => b.onclick = () => { state.tab = b.dataset.tab; state.kind = '전체'; render(); window.scrollTo({top: 0}); });
const sel = document.getElementById('datesel');
fetch(D.root + 'dates.json').then(r => r.json()).then(ds => {
  sel.innerHTML = ds.map(d => `<option value="${d}"${d === D.date ? ' selected' : ''}>${d.replace(/-/g, '.')} ${D.u.sel}</option>`).join('');
}).catch(() => {});
sel.onchange = () => { location.href = D.root + 'reports/' + sel.value + '.html'; };
render();
"""


def page(date: str, rows: list, prev: list, notes: dict, kinds: list, root: str, n_sources: int, site: str) -> str:
    """root = 이 리포트 종류(주간/월간)의 맨 위 폴더까지 상대 경로, site = docs/ 까지 상대 경로."""
    start, end = span(date)
    monthly = len(date) == 7
    u = {"cur": "이번 달", "prev": "지난달", "sel": "월"} if monthly else {"cur": "이번 주", "prev": "지난주", "sel": "주"}
    data = {"date": date, "root": root, "rows": rows, "prev": prev, "swatch": SWATCH, "kinds": kinds, "u": u,
            "notes": {g: notes.get(g, []) for g in ("여성", "남성")}, "period_note": notes.get("period_note", "")}
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    tabs = [("sum", f"{u['cur']} 요약"), ("kw", "뜨는 키워드·아이템"), ("cm", "컬러·소재"), ("rw", "런웨이"), ("art", "기사 모음")]
    return f"""<!doctype html>
<html lang="ko">
<head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>매거진 트렌드 리포트</title>
<meta name="description" content="국내·해외 패션지·남성지·트렌드 매체 {n_sources}곳의 {'월간' if monthly else '주간'} 패션 트렌드 ({start}~{end})">
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='7' fill='%23111'/><text x='16' y='22' font-size='16' text-anchor='middle' fill='white' font-family='sans-serif'>M</text></svg>">
<style>{CSS}</style>
</head>
<body><div class="wrap">
<header><div><h1>매거진 트렌드 리포트</h1>
<p>{start.replace('-', '.')} ~ {end.replace('-', '.')} · 국내·해외 패션지·남성지·트렌드 매체 {n_sources}곳의 패션 기사</p></div>
<div class="hright"><div class="pill" role="group" aria-label="리포트 종류"><a class="pl" href="{site}index.html"{'' if monthly else ' aria-current="page"'}>주간</a><a class="pl" href="{site}monthly/index.html"{' aria-current="page"' if monthly else ''}>월간</a></div>
<label class="datepick">지난 리포트<select id="datesel"><option>{date.replace('-', '.')} {u['sel']}</option></select></label></div></header>
<nav class="topbar" aria-label="보기 전환">
<div class="pill" role="group" aria-label="성별"><button data-g="여성">여성</button><button data-g="남성">남성</button></div>
<div class="pill" role="group" aria-label="지역"><button data-r="전체">전체</button><button data-r="국내">국내</button><button data-r="해외">해외</button></div>
<div class="tabs" role="tablist">{''.join(f'<button role="tab" data-tab="{k}">{html.escape(v)}</button>' for k, v in tabs)}</div>
</nav>
<main id="view"></main>
<footer>여성 = 여성복 + 공용 기사, 남성 = 남성복 + 공용 기사. 사진·제목은 각 매체 소유이며 원문 링크로 연결됩니다. 요약·분류는 Claude가 기사 소개글을 읽고 작성.
Vogue.com·GQ.com 등 AI 이용을 막은 매체는 넣지 않았습니다.</footer>
</div>
<script id="data" type="application/json">{blob}</script>
<script>{JS}</script>
</body></html>
"""


def build(date: str) -> Path:
    monthly = len(date) == 7
    base = DOCS_DIR / "monthly" if monthly else DOCS_DIR
    articles, labels = load_json(ARTICLES_PATH, {}), load_json(LABELS_PATH, {})
    notes = clean_notes(load_json(MONTHLY_NOTES_PATH if monthly else NOTES_PATH, {}).get(date, {}))
    kinds = load_json(Path(__file__).resolve().parent / "trend_keywords.json", {})["article_kind"]
    n_sources = len(load_json(SOURCES_PATH, {})["sources"])
    rows = rows_for(articles, labels, *span(date), full=True)
    prev = rows_for(articles, labels, *span(date, back=1), full=False)
    (base / "reports").mkdir(parents=True, exist_ok=True)
    (base / "reports" / f"{date}.html").write_text(
        page(date, rows, prev, notes, kinds, "../", n_sources, "../../" if monthly else "../"), encoding="utf-8")
    dates = sorted({p.stem for p in (base / "reports").glob("*.html")}, reverse=True)
    (base / "dates.json").write_text(json.dumps(dates), encoding="utf-8")
    if date == dates[0]:
        (base / "index.html").write_text(page(date, rows, prev, notes, kinds, "", n_sources, "../" if monthly else ""),
                                         encoding="utf-8")
    rel = base.relative_to(DOCS_DIR.parent).as_posix()
    print(f"리포트 생성: 패션 기사 {len(rows)}개 ({'지난달' if monthly else '지난주'} {len(prev)}개) → {rel}/reports/{date}.html"
          + (f" + {rel}/index.html" if date == dates[0] else ""))
    return base / "index.html"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_kst())
    ap.add_argument("--month", help="월간 리포트 YYYY-MM")
    args = ap.parse_args()
    build(args.month or args.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
