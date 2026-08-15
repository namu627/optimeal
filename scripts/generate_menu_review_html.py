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
import menu_affinity as ma  # noqa: E402
import user_profiles as up  # noqa: E402
import menu_taxonomy as mt  # noqa: E402
import soft_constraints_diversity as scd  # noqa: E402
from load_nutrition_from_recipe_db import get_engine  # noqa: E402

# 검수용 프로파일 (user_group 테이블 미적재 → 생성 파라미터로 선언)
#   sodium: 1일 나트륨 상한(mg, H-2e Hard 제약). ⚠ 이 수치는 **검수로 확정할 설정값**이다.
#     · 2,000mg = WHO 성인 1일 권고 상한. 일반식 프로파일에 적용.
#     · 노인은 저염 대상이므로 1,500mg 로 강화. 실측상 달성 가능함을 확인하고 채택했으나,
#       연령·기저질환별 정확한 기준치는 영양사 검수에서 확정한다.
# 검수 자료에 실을 기본 프로파일(급식 대상). key 는 user_group_profiles.csv 의 profile_key.
#   ⚠ 2026-08-15 이전 판은 여기에 프로젝트가 임의로 정한 수치(성인 2,000 / 학령기 1,800 /
#     노인 1,700kcal)를 박아 두었다. 근거가 없어 검수 확정 대상으로 표기해 왔는데,
#     이제 공인 기준(2025 한국인 영양소 섭취기준 + 학교급식법 [별표3])에서 가져온다.
DEFAULT_PROFILE_KEYS = "middle_mix,office_mix,senior_mix"
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


def solve_profile(menus: list, kcal: float, days: int, time_limit: float,
                  sodium_max: float | None = None, sodium_by_idx: dict | None = None,
                  main_by_idx: dict | None = None,
                  affinity_table: list | None = None,
                  meals: tuple = MEAL_NAMES,
                  meal_ratios: tuple | None = None):
    """프로파일 1건을 풀이한다.

    Args:
        sodium_max: 1일 나트륨 상한(mg). None이면 미적용.
        sodium_by_idx: {메뉴인덱스: 나트륨mg}. 상한을 켤 때 필수 —
            H-2e 는 값이 없는 메뉴를 배제하므로 주입 없이 켜면 전 메뉴가 배제된다.
        main_by_idx: {메뉴인덱스: 대표 주재료명}. 주재료 중복 회피 항(B6 Phase 1)이
            읽는다. None이면 항 비활성.
        affinity_table: 어울림 근거표(B6 Phase 2). None이면 항 비활성.

    Returns:
        (MealPlanResult, HardConstraintConfig).
    """
    cfg = hc.HardConstraintConfig(
        target_kcal_per_day=kcal,
        budget_limit_per_person=None,   # 원가 0원 → 예산 제약 무의미하므로 비활성
        nutrient_max_per_day=({"sodium": sodium_max} if sodium_max else {}),
        enable_staple_main=True,        # H-4b 주식 슬롯은 밥·면·죽·빵만
        enable_menu_pairing=True,       # H-4c 찌개·전골·탕은 밥류와만
    )
    if meal_ratios:
        cfg.meal_energy_ratios = meal_ratios
    res = cs.build_and_solve(
        menus, cs.MealPlanRequest(
            days=days, meals=meals, hard=cfg, solver_time_limit=time_limit,
            hard_nutrient_by_idx=({"sodium": sodium_by_idx} if sodium_max else None),
            main_by_idx=main_by_idx, affinity_table=affinity_table))
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


def render_plan_table(res, by_name, cfg, meals_names=MEAL_NAMES) -> str:
    """일자 × 끼니 식단표 HTML.

    끼니 이름은 **실제 편성한 것**을 받는다(1식·2식 지원). 상수 MEAL_NAMES 를 그대로 쓰면
    점심만 편성한 식단표에 빈 아침·저녁 칸이 생긴다.
    """
    meal_ok = {(i["day"], i["meal_index"]): i for i in res.hard_breakdown["meal_kcal"]}
    ratios = cfg.meal_energy_ratios
    if len(ratios) != len(meals_names):      # 끼니 배분 제약이 꺼진 경우(1식 등)
        ratios = tuple(1.0 / len(meals_names) for _ in meals_names)
    out = ['<table><thead><tr><th>일자</th>']
    out += [f"<th>{esc(n)}<br><small>목표 {cfg.target_kcal_per_day * r:.0f}kcal</small></th>"
            for n, r in zip(meals_names, ratios)]
    out.append("<th>일 합계</th></tr></thead><tbody>")
    for day, meals in res.plan.items():
        out.append(f"<tr><td class='day'>{day}일</td>")
        for si, mname in enumerate(meals_names):
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


def render_nutrient_table(res, by_name, nutrients, sodium_max) -> str:
    """일별 영양소 합계 표. 나트륨은 Hard 상한(H-2e) 대비 준수 여부를 표시한다.

    ⚠ 나트륨은 **제약이 실제로 쓴 값**(hard_breakdown)을 그대로 표시한다.
      여기서 메뉴명 키로 다시 합산하면 동명 메뉴(B-5) 때문에 제약과 1mg 단위로 어긋나
      "상한 준수인데 표는 초과"처럼 보일 수 있다(2026-08-10 칼로리 절단 오보와 동종).
      단백질·지방·탄수화물은 제약 대상이 아니라 기존 경로를 유지한다.
    """
    na_report = ((res.hard_breakdown or {}).get("nutrient_max") or {}).get("sodium")
    na_by_day = {i["day"]: i["amount"] for i in na_report["per_day"]} if na_report else {}
    rows, over = [], 0
    for day, meals in res.plan.items():
        n = day_nutrients(meals, by_name, nutrients)
        na = na_by_day.get(day, n.get("sodium", 0))
        exceeded = sodium_max is not None and na > sodium_max
        over += 1 if exceeded else 0
        rows.append(f"<tr><td>{day}일</td><td>{res.daily_kcal[day]:.0f}</td>"
                    f"<td>{n.get('protein',0)}</td><td>{n.get('fat',0)}</td>"
                    f"<td>{n.get('carbs',0)}</td>"
                    f"<td class='{'bad' if exceeded else 'ok'}'>{na:,.0f}</td></tr>")
    if sodium_max is None:
        note = "<p><small>나트륨 상한 미적용으로 생성했습니다.</small></p>"
    else:
        note = (f"<p><small>나트륨 상한 <b>{sodium_max:,.0f}mg/일</b>을 Hard 제약(H-2e)으로 걸어 "
                f"생성했습니다 — 초과 {over}/{len(rows)}일. 상한을 넘는 식단은 애초에 해로 "
                f"채택되지 않습니다.</small></p>")
    return ("<table><thead><tr><th>일자</th><th>열량(kcal)</th><th>단백질(g)</th>"
            "<th>지방(g)</th><th>탄수화물(g)</th><th>나트륨(mg)</th></tr></thead>"
            f"<tbody>{''.join(rows)}</tbody></table>{note}")


def _meal_categories(res, by_name) -> list:
    """[(day, meal, [카테고리, ...]), ...] — 편성 순서를 보존한다."""
    out = []
    for day, meals in res.plan.items():
        for meal, picks in meals.items():
            out.append((day, meal, [by_name[n].category for n in picks if n in by_name]))
    return out


def _composition_check(res, by_name) -> tuple:
    """반상 구성(주식1·국1·주찬1·부찬2~3·김치1) 충족 여부.

    ⚠ 접시 **개수**만 세면 부족하다 — 카테고리별로 세야 '부찬 3개·김치 0개'가 잡힌다.
    """
    want = cs.DEFAULT_COMPOSITION
    bad = []
    for day, meal, cats in _meal_categories(res, by_name):
        for cat, spec in want.items():
            lo, hi = cs._count_bounds(spec)
            n = cats.count(cat)
            if (lo is not None and n < lo) or (hi is not None and n > hi):
                bad.append(f"{day}일 {meal} {cat} {n}개")
    label = "끼니 구성 (주식1·국1·주찬1·부찬2~3·김치1)"
    return (label, "전 끼니 충족" if not bad else f"불충족 {len(bad)}건: {', '.join(bad[:3])}",
            not bad)


def _order_check(res, by_name) -> tuple:
    """식단표 표기 순서(주식→국→주찬→부찬→김치) 준수 여부."""
    bad = [f"{day}일 {meal}" for day, meal, cats in _meal_categories(res, by_name)
           if [mt.category_sort_key(c) for c in cats]
           != sorted(mt.category_sort_key(c) for c in cats)]
    return ("표기 순서 (주식→국→주찬→부찬→김치)",
            "전 끼니 준수" if not bad else f"어긋남 {len(bad)}건: {', '.join(bad[:3])}",
            not bad)


def _affinity_check(res, by_name):
    """어울림(같은 조리법 중복) 독립 검증 — 편성 결과를 다시 세어 본다.

    ⚠ 감점 대상은 **근거가 충분한 조리법**뿐이다(관측 30건 미만 축은 계수 없음).
    그래서 "감점 축 위반"과 "그 외 축 중복"을 나눠 보고한다. 하나로 합치면
    근거 없는 축의 중복까지 시스템이 막은 것처럼 읽힌다.
    """
    ab = res.affinity_breakdown or {}
    penalized = set(ab.get("penalized_methods") or ())
    items = list(by_name.values())
    methods = scd.classify_cooking_methods(items)
    method_of = {m.name: methods.get(i) for i, m in enumerate(items)
                 if m.category in ma.SIDE_CATEGORIES}
    scored, other = 0, 0
    for _day, _meal, names in _meal_dishes(res, by_name):
        seen = defaultdict(int)
        for name in names:
            g = method_of.get(name)
            if g:
                seen[g] += 1
        for g, c in seen.items():
            if c >= 2:
                if g in penalized:
                    scored += 1
                else:
                    other += 1
    note = f"감점 축({len(penalized)}종) 위반 {scored}건"
    if other:
        note += f" · 근거 부족 축 중복 {other}건(감점 대상 아님)"
    return ("메뉴 어울림 (한 끼 같은 조리법 중복)", note, scored == 0)


def _meal_dishes(res, by_name):
    """(일, 끼니, 메뉴명 목록) 을 돌려준다."""
    for day, meals in res.plan.items():
        for meal, picks in meals.items():
            yield day, meal, picks


def render_verification(res, cfg, by_name) -> str:
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
        _composition_check(res, by_name),
        _order_check(res, by_name),
    ]
    if res.affinity_breakdown:
        items.append(_affinity_check(res, by_name))
    na = (hb.get("nutrient_max") or {}).get("sodium")
    if na:
        items.append((f"나트륨 1일 상한 ({na['limit']:,.0f}mg)",
                      f"최대 {na['max_day']:,.0f}mg", na["all_ok"]))
    pr = hb.get("pairing") or {}
    if pr:
        items.append(("주식 자격 (밥·면·죽·빵만)",
                      f"주식 없는 끼니 {len(pr['meals_without_staple'])}건",
                      not pr["meals_without_staple"]))
        items.append(("메뉴 궁합 (찌개·전골·탕은 밥과만)",
                      f"부적합 조합 {len(pr['incompatible_pairs'])}건",
                      not pr["incompatible_pairs"]))
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
<p><b>3. 나트륨 상한은 적용됐으나, 상한 수치 자체는 확정해 주셔야 합니다.</b>
직전 판(2026-08-10)은 나트륨 제약이 없어 일 1,665~4,352mg까지 올라갔습니다. 이번 판은
<b>나트륨 1일 상한을 Hard 제약으로 걸어</b> 생성했습니다 — 일반식 2,000mg(WHO 성인 권고),
노인은 저염 대상이므로 1,500mg으로 강화했습니다. <b>다만 이 두 수치는 저희가 정한 설정값</b>
이며, 연령·기저질환(고혈압·신장질환)별 적정 기준은 검수에서 확정해 주시면 그대로 반영합니다.
당류·칼륨·인 상한은 해당 영양소 데이터가 없어 <b>여전히 미구현</b>입니다.</p>
<p><b>4. 메뉴 분류를 개편했습니다 — 남은 오분류를 지적해 주십시오.</b>
직전 판은 <code>grouping_type</code>만 보고 <code>일품요리</code> 171종을 통째로 주식에
넣어, <b>스프가 단독 주식</b>으로 편성되거나 스테이크·탕수육이 주식 자리에 올랐습니다.
이번 판은 메뉴명까지 보고 <b>밥·면·죽·빵만 주식</b>으로 인정합니다(206종). 주식에서 빠진
81종은 버리지 않고 스프·탕·전골 22종은 <b>국</b>으로, 스테이크·만두·롤 등 59종은
반찬 계열로 옮겼습니다. 이어서 <b>반찬 629종을 주찬 258 · 부찬 326 · 김치 45</b>로
나눴습니다(단백질 주요리=주찬, 나물·무침·피클류=부찬, 김치·겉절이·깍두기류=김치).
원본 자료에도 주찬·부찬·김치 라벨이 있으나 <b>출처마다 기준이 달라</b>(김치찌개·김치전까지
'김치'로 라벨된 출처가 있습니다) 쓰지 않고 메뉴명으로 통일해 판정했습니다. 판정은 메뉴명
키워드 기반이라 <b>새로운 표기는 놓칠 수 있습니다</b> — 특히 <b>주찬/부찬 경계</b>는
현장 관행에 따라 다를 수 있으니 어색한 배치를 짚어 주시면 사전을 보강하겠습니다.</p>
<p><b>5. 메뉴 궁합은 규칙 1건 + 조리법 중복 회피까지입니다.</b>
"찌개·전골·탕은 밥과만"은 그대로 금지(국수 + 부대찌개 차단)이고, 이번 판에 <b>한 끼니 안
같은 조리법이 겹치는 것</b>을 피하는 항을 더했습니다(예: 한 끼에 나물·무침 3접시).
점수는 저희가 정한 것이 아니라 <b>실제 학교 급식 15,250끼니</b>(전국 36개교 3년치, NEIS
공개자료)에서 무엇이 실제로 함께 나오는지를 센 값입니다 — 조림·무침·찜·구이·튀김·볶음
6가지에 근거가 충분했고, <b>끓이기·부침은 관측이 적어 제외</b>했습니다(그래서 이 두 가지의
중복은 아직 막지 않습니다). 켜기 전에는 7일 식단에서 <b>같은 조리법 중복이 14~18끼니</b>
발생했고, 켠 뒤에는 0건입니다. 다만 <b>맛 계열 충돌</b>(단 것끼리·매운 것끼리)은 자료에서
가려낼 수 없어 <b>여전히 규칙이 없습니다</b> — 현장에서 쓰는 금기 조합을 알려 주시면
그대로 규칙에 넣겠습니다.</p>
<p><b>6. 김치는 주식 종류와 무관하게 1개씩 붙습니다(변경하지 않았습니다).</b> 끼니 구성은
<b>주식1·국1·주찬1·부찬 2~3개·김치1</b>이라, <b>빵·면 주식 끼니에도 김치가 편성됩니다</b>
(예: 클럽 샌드위치 + 배물김치). 실제 급식 자료에서는 김치 편성이 <b>밥 91.8% · 죽 99.0% ·
면 71.5% · 빵 47.5%</b>로 주식 종류에 따라 달랐습니다. 즉 자료대로라면 빵·면 끼니의 김치는
<b>선택</b>에 가깝습니다. 그럼에도 <b>영양사님이 확정하신 "김치 1개" 사양을 자료만 보고
바꾸지 않았습니다</b> — 이 판단은 검수에서 확정해 주십시오.</p>
<p><b>7. 1인분 기준입니다.</b> 대량 조리 환산은 별도 모듈이며, 현재 초기 추정은 선형(인원수 비례)
입니다(ADR-008). 이는 "예측"이 아니라 영양사 보정을 누적하기 위한 출발점입니다.</p>
</div>

<h2>검수 요청 사항</h2>
<div class="box">
<p>아래 세 가지를 중심으로 보아 주시면 가장 도움이 됩니다.</p>
<ol>
<li><b>끼니 조합이 현장에서 성립하는가</b> — 아침에 부적절한 메뉴, 국물 없는 끼니, 조리 동선이
겹치는 조합 등. 같은 조리법 중복은 이번 판에서 막았으므로(위 한계 5번), 이제 <b>맛 계열이
겹치는 조합</b>과 <b>어울리지 않는 메뉴 쌍</b>을 짚어 주시면 그대로 규칙에 넣겠습니다</li>
<li><b>메뉴 분류 오류</b> — 위 한계 4번. 특히 (a) 주식으로 인정한 206종과 주식에서 빼서
국·반찬 계열로 옮긴 81종, (b) <b>주찬(258)과 부찬(326)의 경계</b>가 현장 감각과 맞는지.
"이건 주찬이 아니라 부찬" 같은 지적이 가장 도움이 됩니다</li>
<li><b>빵·면 끼니의 김치를 어떻게 할지</b> — 위 한계 6번. 실제 급식에서는 빵 끼니의
47.5%·면 끼니의 71.5%에만 김치가 나옵니다. (a) 지금처럼 <b>모든 끼니 김치 1개</b>를 유지할지,
(b) <b>밥·죽만 필수, 빵·면은 선택</b>으로 바꿀지 결정해 주십시오. 자료는 (b)를 가리키지만
확정 사양이라 임의로 바꾸지 않았습니다</li>
<li><b>열량 배분(30·40·30)이 현실적인가</b> — 실제 급식 운영과 어긋나면 비율을 조정하겠습니다</li>
<li><b>나트륨 상한 수치가 적절한가</b> — 위 한계 3번. 일반식 2,000mg·노인 1,500mg으로
두었습니다. 상한을 낮출수록 저염 메뉴 위주로 편성되므로, <b>맛·간이 급식으로 성립하는
하한선</b>을 함께 봐 주시면 좋겠습니다</li>
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
    ap.add_argument("--profiles", default=DEFAULT_PROFILE_KEYS,
                    help="급식 대상 profile_key 를 쉼표로. "
                         "목록은 `python module_3/src/csp_solver.py --list-profiles`")
    ap.add_argument("--meals", default=None,
                    help="끼니 이름을 쉼표로 (예: '점심'). 미지정 시 프로파일 기본값")
    ap.add_argument("--time-limit", type=float, default=90.0)
    ap.add_argument("--no-sodium-limit", action="store_true",
                    help="나트륨 상한(H-2e)을 끄고 생성 — 상한 적용 전후 비교용")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    menus = cs.load_menus()
    by_name = {}
    for m in menus:
        by_name.setdefault(m.name, m)
    nutrients = load_nutrients([m.menu_id for m in menus])
    # H-2e 나트륨 상한이 읽을 값(메뉴 인덱스 기준). 적재율이 낮으면 그만큼 후보가 배제된다.
    sodium_by_idx, _ = scd.load_nutrition_fields(get_engine(), menus)
    # 주재료 축(B6 Phase 1) — 얻은 메뉴만 중복 회피 대상, 나머지는 중립.
    main_by_idx = scd.load_main_ingredients(get_engine(), menus)
    # 어울림 근거표(B6 Phase 2) — 없으면 빈 목록이라 항이 자동 비활성.
    affinity_table = ma.load_affinity_table()
    print(f"[후보] {len(menus)}종 · 나트륨 적재 {len(sodium_by_idx)}종"
          f" · 주재료 확보 {len(main_by_idx)}종 · 어울림 근거 {len(affinity_table)}행")

    profiles = up.load_profiles()
    sections = []
    for key in [p.strip() for p in args.profiles.split(",") if p.strip()]:
        prof = profiles.get(key)
        if prof is None:
            print(f"  [skip] 미정의 프로파일: {key} (--list-profiles 로 확인)")
            continue
        meals = (tuple(m.strip() for m in args.meals.split(",")) if args.meals
                 else {1: ("점심",), 2: ("점심", "저녁")}.get(prof.default_meals, MEAL_NAMES))
        tg = up.targets_for(prof, meals)
        na_max = None if args.no_sodium_limit else tg["sodium_max_mg_per_day"]
        res, cfg = solve_profile(menus, tg["target_kcal_per_day"], args.days,
                                 args.time_limit,
                                 sodium_max=na_max, sodium_by_idx=sodium_by_idx,
                                 main_by_idx=main_by_idx,
                                 affinity_table=affinity_table,
                                 meals=meals,
                                 meal_ratios=tg["meal_energy_ratios"])
        label = f"{prof.group_name} · {len(meals)}식"
        print(f"  [{label}] {res.status} {res.wall_time:.1f}초 "
              f"· {tg['target_kcal_per_day']:,.0f}kcal"
              f"{f' · Na≤{na_max:,.0f}mg' if na_max else ''}")
        if not res.plan:
            sections.append(f"<h2>{esc(label)}</h2><div class='box warn'>"
                            f"해를 찾지 못했습니다 (status={esc(res.status)}).</div>")
            continue
        sections.append(
            f"<h2>{esc(label)} — {tg['target_kcal_per_day']:,.0f}kcal"
            f"{f' · 나트륨 ≤{na_max:,.0f}mg' if na_max else ''}</h2>"
            f"<p><small>대상 {esc(prof.age_band)} · {esc('/'.join(meals))} 편성 · "
            f"근거 {esc(tg['basis'])} · 출처 {esc(prof.source)}"
            f"{' · ' + esc(prof.note) if prof.note else ''}</small></p>"
            "<h3>자동 검증</h3>" + render_verification(res, cfg, by_name) +
            "<h3>식단표</h3><div class='tblwrap'>" + render_plan_table(res, by_name, cfg, meals) +
            "</div><h3>일별 영양소 합계</h3><div class='tblwrap'>" +
            render_nutrient_table(res, by_name, nutrients, na_max) + "</div>")

    out_path = Path(args.out) if args.out else (
        REPO_ROOT / "reports" / f"menu_review_{date.today():%Y%m%d}.html")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(build_html(sections, args.days, len(menus)), encoding="utf-8")
    print(f"[완료] {out_path.relative_to(REPO_ROOT)} ({out_path.stat().st_size:,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
