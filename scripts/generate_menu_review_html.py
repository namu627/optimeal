#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
스크립트: generate_menu_review_html.py
목적: 영양사 검수용 **예시 식단표**를 self-contained HTML 로 생성한다.

산출물: reports/menu_review_YYYYMMDD.html
  · 프로파일별 7일 식단표 (일자 × 끼니 × 접시, 카테고리·kcal 표기)
  · 끼니별/일별 에너지 목표 대비 준수 표 (Hard 검증 결과 그대로)
  · 메뉴 중복 검증 (창 내 재등장 0건 확인)
  · 일별 영양소 합계 (단백질·지방·탄수화물·나트륨)
  · 루브릭 검수란 (ADR-002 3항목 5점 척도) + 한계 고지

실행:
  docker exec optimeal_app python scripts/generate_menu_review_html.py
  docker exec optimeal_app python scripts/generate_menu_review_html.py --days 7 --profiles 성인,학령기

⚠ 예산(식단가) 제약은 비활성으로 생성한다 — `ingredient_price` 미적재로 전 후보 원가가
  0원이어서 제약이 항상 만족(무의미)하기 때문이다. 0원을 식단가처럼 보여 주지 않는다.
⚠ 알레르기 대체식 트랙은 이번 산출물에서 제외한다 — `constraints` 테이블이 비어 있어
  알레르겐 매핑이 없다. 불완전한 매핑을 임의로 만들어 "알레르기 대응됨"으로 보이게 하는 것은
  안전상 위험하므로 하지 않는다.
"""

import argparse
import html
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

from sqlalchemy import text

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import csp_hard_constraints as hc  # noqa: E402
import csp_solver as cs  # noqa: E402
from load_nutrition_from_recipe_db import get_engine  # noqa: E402

# 검수용 프로파일 (user_group 테이블 미적재 → 생성 파라미터로 선언)
PROFILES = {
    "성인": {"kcal": 2000.0, "note": "일반 성인 기준 (권장 2,000kcal)"},
    "학령기": {"kcal": 1800.0, "note": "초·중학생 기준 (권장 1,800kcal)"},
    "노인": {"kcal": 1700.0, "note": "노인복지 기준 (권장 1,700kcal)"},
}
MEAL_NAMES = ("아침", "점심", "저녁")


def load_nutrients(menu_ids: list[int]) -> dict:
    """menu_id → 영양소(단백질·지방·탄수화물·나트륨) 매핑을 조회한다."""
    if not menu_ids:
        return {}
    with get_engine().connect() as conn:
        rows = conn.execute(text("""
            SELECT nutrition_id, protein, fat, carbs, sodium
            FROM nutrition_recipe WHERE nutrition_id = ANY(:ids)
        """), {"ids": menu_ids}).mappings().all()
    return {r["nutrition_id"]: {k: float(r[k] or 0) for k in
                                ("protein", "fat", "carbs", "sodium")} for r in rows}


def solve_profile(menus: list, kcal: float, days: int, time_limit: float):
    """프로파일 1건을 풀이한다.

    Returns:
        (MealPlanResult, HardConstraintConfig).
    """
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=kcal,
        budget_limit_per_person=None,   # 원가 0원 → 예산 제약 무의미하므로 비활성
    )
    res = cs.build_and_solve(
        menus, cs.MealPlanRequest(days=days, meals=MEAL_NAMES, hard=cfg,
                                  solver_time_limit=time_limit))
    return res, cfg


def day_nutrients(plan_day: dict, by_name: dict, nutrients: dict) -> dict:
    """하루 식단의 영양소 합계."""
    total = defaultdict(float)
    for picks in plan_day.values():
        for name in picks:
            m = by_name.get(name)
            if not m:
                continue
            for k, v in nutrients.get(m.menu_id, {}).items():
                total[k] += v
    return {k: round(v, 1) for k, v in total.items()}


def esc(s) -> str:
    """HTML 이스케이프."""
    return html.escape(str(s))


def render_plan_table(res, by_name, cfg) -> str:
    """일자 × 끼니 식단표 HTML."""
    meal_ok = {(i["day"], i["meal_index"]): i for i in res.hard_breakdown["meal_kcal"]}
    out = ['<table><thead><tr><th>일자</th>']
    out += [f"<th>{esc(n)}<br><small>목표 {cfg.target_kcal_per_day * r:.0f}kcal</small></th>"
            for n, r in zip(MEAL_NAMES, cfg.meal_energy_ratios)]
    out.append("<th>일 합계</th></tr></thead><tbody>")
    for day, meals in res.plan.items():
        out.append(f"<tr><td class='day'>{day}일</td>")
        for si, mname in enumerate(MEAL_NAMES):
            dishes = []
            for name in meals.get(mname, []):
                m = by_name.get(name)
                cat = esc(m.category) if m else "?"
                kc = f"{m.calories:.0f}" if m else "-"
                dishes.append(f"<li>{esc(name)} <span class='tag'>{cat}</span>"
                              f"<span class='kc'>{kc}kcal</span></li>")
            info = meal_ok.get((day, si))
            badge = ""
            if info:
                cls = "ok" if info["ok"] else "bad"
                badge = (f"<div class='sum {cls}'>{info['kcal']:.0f}kcal "
                         f"{'적합' if info['ok'] else '이탈'}</div>")
            out.append(f"<td><ul>{''.join(dishes)}</ul>{badge}</td>")
        lo = cfg.target_kcal_per_day * (1 - cfg.kcal_tolerance)
        hi = cfg.target_kcal_per_day * (1 + cfg.kcal_tolerance)
        dk = res.daily_kcal[day]
        cls = "ok" if lo <= dk <= hi else "bad"
        out.append(f"<td class='sum {cls}'>{dk:.0f}kcal</td></tr>")
    out.append("</tbody></table>")
    return "".join(out)


def render_nutrient_table(res, by_name, nutrients) -> str:
    """일별 영양소 합계 표."""
    rows = []
    for day, meals in res.plan.items():
        n = day_nutrients(meals, by_name, nutrients)
        rows.append(f"<tr><td>{day}일</td><td>{res.daily_kcal[day]:.0f}</td>"
                    f"<td>{n.get('protein',0)}</td><td>{n.get('fat',0)}</td>"
                    f"<td>{n.get('carbs',0)}</td><td>{n.get('sodium',0)}</td></tr>")
    return ("<table><thead><tr><th>일자</th><th>열량(kcal)</th><th>단백질(g)</th>"
            "<th>지방(g)</th><th>탄수화물(g)</th><th>나트륨(mg)</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>")


def render_verification(res, cfg) -> str:
    """Hard 제약 준수·중복 검증 요약."""
    hb = res.hard_breakdown
    rp = hb["menu_repeat"]
    items = [
        ("풀이 상태", f"{res.status} ({res.wall_time:.1f}초)", res.status in ("OPTIMAL", "FEASIBLE")),
        ("일별 에너지 ±10%", "전일 준수" if all(i["kcal_ok"] for i in hb["per_day"])
         else "이탈 있음", all(i["kcal_ok"] for i in hb["per_day"])),
        ("끼니별 배분 30·40·30 (±15%)",
         "전 끼니 준수" if all(i["ok"] for i in hb["meal_kcal"]) else "이탈 있음",
         all(i["ok"] for i in hb["meal_kcal"])),
        (f"메뉴 중복 회피 ({rp['window_days']}일 내 재등장 금지)",
         f"위반 {len(rp['violations'])}건 · 동일 메뉴 최대 {rp['max_same_menu_count']}회 등장",
         not rp["violations"]),
        ("끼니 구성 (주식1·국1·반찬2)",
         "전 끼니 충족" if all(len(p) == 4 for m in res.plan.values() for p in m.values())
         else "불충족", all(len(p) == 4 for m in res.plan.values() for p in m.values())),
    ]
    rows = "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(v)}</td>"
        f"<td class='{'ok' if good else 'bad'}'>{'✔' if good else '✘'}</td></tr>"
        for k, v, good in items)
    return f"<table><thead><tr><th>검증 항목</th><th>결과</th><th></th></tr></thead><tbody>{rows}</tbody></table>"


CSS = """
:root{--fg:#1a1a1a;--mut:#666;--line:#d8d8d8;--ok:#0a7d3c;--bad:#c0392b;--bg:#fff;--card:#fafafa}
*{box-sizing:border-box}
body{margin:0;padding:32px 20px;font-family:-apple-system,'Apple SD Gothic Neo','Malgun Gothic',sans-serif;
color:var(--fg);background:var(--bg);line-height:1.6}
.wrap{max-width:1100px;margin:0 auto}
h1{font-size:1.6rem;margin:0 0 4px}h2{font-size:1.15rem;margin:36px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--fg)}
h3{font-size:1rem;margin:22px 0 8px}
.meta{color:var(--mut);font-size:.86rem;margin-bottom:20px}
.box{background:var(--card);border:1px solid var(--line);border-radius:6px;padding:14px 16px;margin:14px 0}
.warn{background:#fff8e6;border-color:#e6c96b}
.tblwrap{overflow-x:auto;margin:10px 0}
table{border-collapse:collapse;width:100%;font-size:.86rem;min-width:640px}
th,td{border:1px solid var(--line);padding:7px 9px;text-align:left;vertical-align:top}
th{background:#f0f0f0;font-weight:600}
td.day{white-space:nowrap;font-weight:600;background:#f7f7f7}
ul{margin:0;padding-left:16px}li{margin:2px 0}
.tag{display:inline-block;margin-left:5px;padding:0 5px;font-size:.72rem;color:#fff;background:#7a7a7a;border-radius:3px}
.kc{color:var(--mut);font-size:.76rem;margin-left:5px}
.sum{margin-top:6px;font-weight:600;font-size:.82rem}
.ok{color:var(--ok)}.bad{color:var(--bad)}
.rubric td{height:34px}.rubric td.blank{background:#fffdf5}
small{color:var(--mut);font-weight:400}
@media (prefers-color-scheme:dark){
:root{--fg:#e8e8e8;--mut:#a0a0a0;--line:#3a3a3a;--bg:#161616;--card:#1f1f1f;--ok:#4ade80;--bad:#f87171}
th{background:#262626}td.day{background:#202020}.warn{background:#2a2410;border-color:#6b5a2b}
.rubric td.blank{background:#221f18}}
"""

RUBRIC = """
<table class="rubric"><thead><tr><th>검수 항목</th><th>1</th><th>2</th><th>3</th><th>4</th><th>5</th>
<th style="width:38%">의견</th></tr></thead><tbody>
<tr><td>① 맛·간 적절성</td><td class="blank"></td><td class="blank"></td><td class="blank"></td>
<td class="blank"></td><td class="blank"></td><td class="blank"></td></tr>
<tr><td>② 조리 실현 가능성</td><td class="blank"></td><td class="blank"></td><td class="blank"></td>
<td class="blank"></td><td class="blank"></td><td class="blank"></td></tr>
<tr><td>③ 대량 조리 현장 적합성</td><td class="blank"></td><td class="blank"></td><td class="blank"></td>
<td class="blank"></td><td class="blank"></td><td class="blank"></td></tr>
</tbody></table>
<p><small>ADR-002 루브릭 · 3항목 평균 4.00 이상 = "상용 가능" 판정</small></p>
"""


def build_html(sections: list, days: int, n_menus: int) -> str:
    """전체 HTML 문서를 조립한다."""
    today = date.today().isoformat()
    limits = """
<h2>검수 시 알고 계셔야 할 한계</h2>
<div class="box warn">
<p><b>1. 식단가(원가)가 표시되지 않습니다.</b> 식재료 단가 테이블(<code>ingredient_price</code>)이
비어 있어 모든 메뉴 원가가 0원입니다. 예산 제약을 켜면 항상 만족하는 무의미한 제약이 되므로
<b>비활성 상태로 생성</b>했습니다. 0원을 식단가로 오해하지 않도록 아예 표기하지 않았습니다.
(가락시장 API 인증정보 확보 후 재생성 필요)</p>
<p><b>2. 알레르기 대체식 트랙이 없습니다.</b> 알레르겐 매핑 테이블(<code>constraints</code>)이
비어 있습니다. 불완전한 매핑을 임시로 만들어 "알레르기 대응됨"처럼 보이게 하는 것은 안전상
위험하므로 <b>이번 검수 범위에서 제외</b>했습니다. 대체식 로직 자체는 구현되어 있습니다.</p>
<p><b>3. 기저질환 상한(당류·나트륨·칼륨·인)은 미구현입니다.</b> 나트륨은 참고용으로 합계만
표시하며 상한 제약으로 걸리지 않습니다.</p>
<p><b>4. 메뉴 분류는 원본 <code>grouping_type</code>을 기계적으로 매핑한 결과입니다.</b>
(밥·일품요리→주식 / 국 / 주찬·부찬·반찬·김치→반찬) 국물 요리가 '반찬'으로 분류된 사례가
있을 수 있습니다 — <b>분류가 어색한 접시를 지적해 주시면 매핑을 고치겠습니다.</b></p>
<p><b>5. 1인분 기준입니다.</b> 대량 조리 환산은 별도 모듈이며, 현재 초기 추정은 선형(인원수 비례)
입니다(ADR-008). 이는 "예측"이 아니라 영양사 보정을 누적하기 위한 출발점입니다.</p>
</div>

<h2>검수 요청 사항</h2>
<div class="box">
<p>아래 세 가지를 중심으로 보아 주시면 가장 도움이 됩니다.</p>
<ol>
<li><b>끼니 조합이 현장에서 성립하는가</b> — 아침에 부적절한 메뉴, 국물 없는 끼니, 조리 동선이
겹치는 조합(예: 한 끼에 튀김 2종) 등</li>
<li><b>메뉴 분류 오류</b> — 위 한계 4번</li>
<li><b>열량 배분(30·40·30)이 현실적인가</b> — 실제 급식 운영과 어긋나면 비율을 조정하겠습니다</li>
</ol>
</div>

<h2>루브릭 평가</h2>
"""
    return f"""<!DOCTYPE html>
<html lang="ko"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>OptiMeal 예시 식단표 — 영양사 검수용 ({today})</title>
<style>{CSS}</style></head><body><div class="wrap">
<h1>OptiMeal 예시 식단표 — 영양사 검수용</h1>
<div class="meta">생성일 {today} · 모듈 3 CSP Solver(OR-Tools CP-SAT) 자동 생성 ·
후보 메뉴 {n_menus}종(식품안전나라 실측 영양값) · 각 프로파일 {days}일 3끼</div>
<div class="box"><b>이 문서는 무엇인가</b><br>
영양사가 입력한 조건(인원 기준 열량)에 맞춰 시스템이 자동 편성한 식단입니다.
아래 <b>자동 검증 표</b>는 시스템이 스스로 확인한 제약 준수 결과이고,
사람이 판단해야 하는 부분(맛·조리 현실성·분류 적절성)을 검수받고자 합니다.</div>
{''.join(sections)}
{limits}
{RUBRIC}
</div></body></html>"""


def main() -> int:
    """생성 진입점."""
    ap = argparse.ArgumentParser(description="영양사 검수용 예시 식단표 HTML 생성")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--profiles", default="성인,학령기,노인")
    ap.add_argument("--time-limit", type=float, default=90.0)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    menus = cs.load_menus()
    by_name = {}
    for m in menus:
        by_name.setdefault(m.name, m)
    nutrients = load_nutrients([m.menu_id for m in menus])
    print(f"[후보] {len(menus)}종")

    sections = []
    for label in [p.strip() for p in args.profiles.split(",") if p.strip()]:
        prof = PROFILES.get(label)
        if prof is None:
            print(f"  [skip] 미정의 프로파일: {label}")
            continue
        res, cfg = solve_profile(menus, prof["kcal"], args.days, args.time_limit)
        print(f"  [{label}] {res.status} {res.wall_time:.1f}초")
        if not res.plan:
            sections.append(f"<h2>{esc(label)}</h2><div class='box warn'>"
                            f"해를 찾지 못했습니다 (status={esc(res.status)}).</div>")
            continue
        sections.append(
            f"<h2>{esc(label)} — {prof['kcal']:.0f}kcal/일</h2>"
            f"<p><small>{esc(prof['note'])}</small></p>"
            "<h3>자동 검증</h3>" + render_verification(res, cfg) +
            "<h3>식단표</h3><div class='tblwrap'>" + render_plan_table(res, by_name, cfg) +
            "</div><h3>일별 영양소 합계</h3><div class='tblwrap'>" +
            render_nutrient_table(res, by_name, nutrients) + "</div>")

    out_path = Path(args.out) if args.out else (
        REPO_ROOT / "reports" / f"menu_review_{date.today():%Y%m%d}.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(sections, args.days, len(menus)), encoding="utf-8")
    print(f"[완료] {out_path.relative_to(REPO_ROOT)} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
