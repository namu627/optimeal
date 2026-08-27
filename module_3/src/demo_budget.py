# -*- coding: utf-8 -*-
"""식단가 로직 데모 — 실제 DB 메뉴·실제 원가로 예산 Hard 상한 + Soft 하한을 시연.

목적: 완전한 반상(주찬/부찬/김치) 태깅이 아직 안 된 DB에서도, 지금 있는 카테고리
      (주식/국/반찬)로 '식단가 반영 로직'이 실제 데이터로 작동하는 것을 보여준다.

실행:
  docker exec optimeal_app python module_3/src/demo_budget.py
  docker exec optimeal_app python module_3/src/demo_budget.py --cap 2500 --floor 1500 --days 7
"""
import argparse

try:
    from . import csp_hard_constraints as hc
    from . import csp_solver as cs
except ImportError:
    import csp_hard_constraints as hc
    import csp_solver as cs


def main():
    ap = argparse.ArgumentParser(description="식단가 로직 데모(실제 데이터)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--cap", type=float, default=3500.0, help="식단가 Hard 상한(1인 1일, 원)")
    ap.add_argument("--floor", type=float, default=1500.0, help="식단가 Soft 하한(1인 1일, 원). 0이면 끔")
    args = ap.parse_args()

    menus = cs.load_menus()
    cats = {m.category for m in menus}
    print(f"[실제 메뉴 {len(menus)}종 로드 · 카테고리 {sorted(cats)}]")
    priced = [m for m in menus if m.cost_won > 0]
    print(f"[원가>0 메뉴 {len(priced)}종 · 평균 {sum(m.cost_won for m in priced)/max(len(priced),1):,.0f}원]")

    # 현재 존재하는 카테고리로 끼니 구성(반상 세분 전이라 '반찬' 사용)
    comp = {}
    for c, n in (("주식", 1), ("국", 1), ("반찬", 3)):
        if c in cats:
            comp[c] = n
    print(f"[끼니 구성(데모용): {comp}]  ※ 주찬/부찬/김치 세분은 데이터 보강 후 별도 과제")

    # 예산 외 제약은 꺼서 '식단가 로직'만 또렷이 본다
    cfg = hc.HardConstraintConfig(
        budget_limit_per_person=args.cap, budget_period="day",
        enable_energy=False, enable_meal_ratio=False,
        enable_staple_main=False, enable_menu_pairing=False,
        menu_repeat_window_days=0,
    )
    req = cs.MealPlanRequest(
        days=args.days, meals=("점심",), composition=comp,
        hard=cfg,
        budget_floor_won=(args.floor if args.floor > 0 else None),
        warm_start=False, solver_time_limit=15.0,
    )

    res = cs.build_and_solve(menus, req)
    print("=" * 60)
    print(f"풀이 상태: {res.status}")
    if not res.plan:
        print("해 없음 — 상한을 너무 낮게 잡았을 수 있음. --cap 을 올려보세요.")
        return

    print(f"식단가 Hard 상한: {args.cap:,.0f}원/일" +
          (f" · Soft 하한: {args.floor:,.0f}원/일" if args.floor > 0 else " · 하한 미적용"))
    print("=" * 60)

    hb = {d["day"]: d for d in (res.hard_breakdown or {}).get("per_day", [])}
    fb = {f["day"]: f for f in ((res.soft_breakdown or {}).get("budget_floor") or [])}
    for day, meals in res.plan.items():
        dishes = ", ".join(sum(meals.values(), []))
        cost = hb.get(day, {}).get("cost", "?")
        bok = hb.get(day, {}).get("budget_ok")
        frow = fb.get(day, {})
        fok = frow.get("floor_ok")                # 솔버(100원 단위) 판정
        fok_exact = frow.get("floor_ok_exact")    # 정확값(원) 판정
        short_won = frow.get("shortfall_won", 0)
        tag = []
        if bok is not None:
            tag.append("상한OK" if bok else "상한위반")
        if fok_exact is not None:
            if fok_exact:
                tag.append("하한OK(적정)")
            elif fok:   # 100원 단위론 충족, 정확값은 소폭 미달
                tag.append(f"하한근접(정확값 -{short_won}원, 100원단위 내 충족)")
            else:
                tag.append(f"하한미달(너무쌈, -{short_won}원)")
        print(f"  {day}일차 · {cost:>5}원 [{' · '.join(tag)}]  {dishes}")

    print("-" * 60)
    print(f"1인 {args.days}일 총 식재료비: {res.total_cost:,}원  "
          f"(하루 평균 {res.total_cost/args.days:,.0f}원)")
    print("\n※ 원가는 가락시장(청과·수산) 가격만 반영돼 실제보다 낮게 잡힘 — 로직 시연용.")


if __name__ == "__main__":
    main()
