#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""급식 대상 프로파일을 `user_group` 테이블에 적재한다 (멱등).

왜 CSV 와 DB 를 둘 다 두는가:
    **정본은 CSV**(`data/processed/user_group_profiles.csv`)다 — 영양사 검수 결과로
    덮어쓸 수 있어야 하고, 스키마 v4.3 의 `user_group` 에는 성별·법정 1식 기준·출처 열이
    없기 때문이다. 그럼에도 적재하는 이유는 **다른 모듈이 SQL 로도 조회**할 수 있어야
    하기 때문(FR-11 입력, 프론트 드롭다운 등). CSV 에만 있는 열은 `description` 에
    사람이 읽을 문장으로 보존한다.

멱등: `group_name` 기준으로 있으면 UPDATE, 없으면 INSERT. 기존 행을 지우지 않는다.

실행:
    docker exec optimeal_app python scripts/load_user_groups.py --dry-run
    docker exec optimeal_app python scripts/load_user_groups.py --apply
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "module_3" / "src"))

import csp_solver as cs  # noqa: E402
import user_profiles as up  # noqa: E402

UPSERT = """
INSERT INTO user_group (group_name, group_type, daily_calories, daily_protein_g,
                        daily_sodium_mg, activity_multiplier, has_restrictions, description)
VALUES (:group_name, :group_type, :kcal, :protein, :sodium, 1.0, :restricted, :description)
"""
UPDATE = """
UPDATE user_group SET group_type = :group_type, daily_calories = :kcal,
       daily_protein_g = :protein, daily_sodium_mg = :sodium, description = :description
WHERE group_name = :group_name
"""


def describe(p: up.UserProfile) -> str:
    """CSV 에만 있는 열(성별·연령대·법정 1식 기준·출처·주의)을 문장으로 보존한다."""
    parts = [f"key={p.profile_key}", f"성별={p.sex}", f"연령={p.age_band}",
             f"기본끼니={p.default_meals}식"]
    if p.legal_meal_kcal is not None:
        parts.append(f"법정1식={p.legal_meal_kcal:g}kcal/{p.legal_meal_protein_g:g}g")
    if p.sodium_ai_mg is not None:
        parts.append(f"Na충분섭취량={p.sodium_ai_mg:g}mg")
    parts.append(f"출처={p.source}")
    if p.note:
        parts.append(p.note)
    return " · ".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser(description="급식 대상 프로파일 → user_group 적재")
    ap.add_argument("--apply", action="store_true", help="실제 적재(기본은 dry-run)")
    ap.add_argument("--dry-run", action="store_true", help="적재 없이 미리보기")
    args = ap.parse_args()

    profiles = up.load_profiles()
    if not profiles:
        print("[중단] 프로파일 표를 읽지 못했습니다 — "
              "data/processed/user_group_profiles.csv 확인")
        return 1
    print(f"[입력] 프로파일 {len(profiles)}종")
    if not args.apply:
        for p in profiles.values():
            na = f"{p.sodium_cdrr_mg:,.0f}mg" if p.sodium_cdrr_mg else "-"
            print(f"  · {p.group_name:26s} {p.group_type:3s} "
                  f"{p.daily_kcal:6,.0f}kcal · Na {na}")
        print("\n(dry-run) --apply 로 실제 적재")
        return 0

    from sqlalchemy import text

    engine = cs.get_engine()
    ins = upd = 0
    with engine.begin() as conn:
        existing = {r[0] for r in conn.execute(text("SELECT group_name FROM user_group"))}
        for p in profiles.values():
            params = {
                "group_name": p.group_name, "group_type": p.group_type,
                "kcal": int(round(p.daily_kcal)), "protein": p.protein_g,
                "sodium": p.sodium_cdrr_mg,
                "restricted": p.group_type in ("환자", "노인"),
                "description": describe(p),
            }
            if p.group_name in existing:
                conn.execute(text(UPDATE), params)
                upd += 1
            else:
                conn.execute(text(UPSERT), params)
                ins += 1
    print(f"[완료] INSERT {ins}건 · UPDATE {upd}건")
    return 0


if __name__ == "__main__":
    sys.exit(main())
