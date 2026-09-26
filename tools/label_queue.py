"""판정 도우미 Tool — Claude가 읽고 판정할 기사 묶음을 뽑고, 써 넣은 판정표를 검사·합친다.

판정표 한 줄 (data/article_labels.json, 기사 id → 값):
  {"fashion": true/false,          # 패션디자이너에게 쓸모 있는 패션 기사인가 (뷰티·연예·음식·시계만 등은 false)
   "gender": "여성|남성|공용",      # 옷의 대상 (셀럽이 여성이면 여성, 브랜드 전체 뉴스면 공용)
   "article_kind": "...",          # trend_keywords.json의 article_kind 중 하나
   "summary": "한국어 2~3줄 요약",   # 디자이너가 알아야 할 것 위주 (무엇이, 어떤 디테일로, 왜 뜨는지)
   "items": [...], "styles": [...], "details": [...], "colors": [...], "materials": [...],  # 이름표 안에서만
   "brands": [...], "season": "2027SS" 등 (없으면 "")}
패션이 아니면 {"fashion": false} 만 적어도 됨.

사용법:
  python tools/label_queue.py --next [--size 60]     다음 묶음을 .tmp/to_label.json 으로 (이번 주 기사 중 판정 안 된 것)
  python tools/label_queue.py --merge 파일.json      Claude가 쓴 판정 묶음을 검사 후 판정표에 합침
  python tools/label_queue.py --check                전체 판정표 검사 + 남은 개수
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ARTICLES_PATH, LABELS_PATH, SNIPPETS_PATH, TMP_DIR, VOCAB_PATH, load_json, save_json, today_kst

LIST_FIELDS = ("items", "styles", "details", "colors", "materials")


def vocab() -> dict[str, set[str]]:
    v = load_json(VOCAB_PATH, {})
    return {"gender": set(v["gender"]), "article_kind": set(v["article_kind"]), "colors": set(v["colors"]),
            "materials": set(v["materials"]), "styles": set(v["styles"]), "details": set(v["details"]),
            "items": {x for group in v["items"].values() for x in group}}


def week_window(date: str, days: int = 7) -> tuple[str, str]:
    end = datetime.fromisoformat(date)
    return (end - timedelta(days=days - 1)).strftime("%Y-%m-%d"), date


def validate(aid: str, lab: dict, v: dict, articles: dict) -> list[str]:
    errs = []
    if aid not in articles:
        errs.append(f"{aid}: 모은 기사에 없는 id")
    if not isinstance(lab.get("fashion"), bool):
        errs.append(f"{aid}: fashion은 true/false")
    if not lab.get("fashion"):
        return errs
    for key in ("gender", "article_kind"):
        if lab.get(key) not in v[key]:
            errs.append(f"{aid}: {key} '{lab.get(key)}'는 이름표 밖")
    if len(lab.get("summary", "")) < 20:
        errs.append(f"{aid}: 요약이 비었거나 너무 짧음")
    for f in LIST_FIELDS:
        bad = [x for x in lab.get(f, []) if x not in v[f]]
        if bad:
            errs.append(f"{aid}: {f} 이름표 밖 {bad}")
    return errs


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=today_kst(), help="리포트 날짜 (이 날까지 7일)")
    ap.add_argument("--next", action="store_true")
    ap.add_argument("--size", type=int, default=60)
    ap.add_argument("--merge")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    articles, labels, v = load_json(ARTICLES_PATH, {}), load_json(LABELS_PATH, {}), vocab()
    start, end = week_window(args.date)
    week = {k: a for k, a in articles.items() if start <= a["date"] <= end}
    todo = sorted((k for k in week if k not in labels), key=lambda k: (week[k]["source"], week[k]["published"]))

    if args.merge:
        batch = json.loads(Path(args.merge).read_text(encoding="utf-8"))
        errs = [e for aid, lab in batch.items() for e in validate(aid, lab, v, articles)]
        if errs:
            print("판정 묶음에 문제가 있어 합치지 않음:\n  " + "\n  ".join(errs[:40]))
            return 1
        labels.update(batch)
        save_json(LABELS_PATH, labels)
        print(f"합침: {len(batch)}개 → 판정표 {len(labels)}개, 이번 주 남은 것 {len(todo) - len(set(batch) & set(todo))}개")
        return 0

    if args.next:
        pick = todo[:args.size]
        # 소개글은 로컬 .tmp/snippets.json에만 있음. 없으면(자동 수집으로 모인 기사) 기사 페이지 공식 소개글을 받아 채움
        snippets = load_json(SNIPPETS_PATH, {})
        missing = [k for k in pick if len(snippets.get(k, "")) < 40]
        if missing:
            from fetch_articles import page_meta
            print(f"소개글 받는 중: {len(missing)}개 (기사 페이지 공식 소개)", flush=True)
            for k in missing:
                try:
                    snippets[k] = page_meta(week[k]["link"])[0] or snippets.get(k, "")
                except Exception as e:
                    print(f"  못 받음 {k}: {e}")
            save_json(SNIPPETS_PATH, snippets)
        out = [{"id": k, "매체": week[k]["source_name"], "지역": week[k]["region"], "날짜": week[k]["date"],
                "분류": week[k]["categories"], "제목": week[k]["title"], "소개": snippets.get(k, ""),
                "주소": week[k]["link"]} for k in pick]
        TMP_DIR.mkdir(exist_ok=True)
        (TMP_DIR / "to_label.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"이번 주({start}~{end}) 기사 {len(week)}개 중 판정 안 된 것 {len(todo)}개 → {len(pick)}개를 .tmp/to_label.json 에")
        return 0

    errs = [e for aid, lab in labels.items() for e in validate(aid, lab, v, articles)]
    fashion = sum(1 for k in week if labels.get(k, {}).get("fashion"))
    print(f"이번 주({start}~{end}) 기사 {len(week)}개 / 판정 {len(week) - len(todo)}개 (패션 {fashion}개) / 남은 것 {len(todo)}개")
    print("판정표 문제 없음" if not errs else "문제:\n  " + "\n  ".join(errs[:40]))
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
