"""
repositories.py
===============
읽기 전용 자산(df_B.csv, scaling_coefficients.csv) 조회 계층.

캘리브레이션 저장소(`calibration_store.CalibrationStore`)는 정수 id로 셀을 식별하는데
df_B.csv의 식별자는 문자열(`A1034`, `마늘`)이다. 이 모듈이 그 사이를 **결정론적으로**
매핑한다(정렬 순서 기반 1-based 인덱스). 같은 CSV면 항상 같은 id가 나온다.

⚠ 계수 CSV(331행)는 ADR-006/007 산출물이며 ADR-008 이후 **운영 미적용**(비교군).
   조회 엔드포인트는 학술 참조용으로만 노출하고 응답에 그 사실을 표기한다.
"""

from __future__ import annotations

import csv
import functools
from typing import Optional

from . import config


@functools.lru_cache(maxsize=1)
def _load_df_b() -> list[dict]:
    """df_B.csv 전체를 dict 리스트로 로드(캐시). pandas 의존 없이 표준 csv 사용."""
    with open(config.DF_B_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


@functools.lru_cache(maxsize=1)
def _registries() -> tuple[dict[str, int], dict[str, int]]:
    """레시피 키·재료명 → 정수 id 레지스트리를 만든다.

    Returns:
        (recipe_key→recipe_id, ingredient_name→ingredient_id). 둘 다 정렬 순서 1-based.
    """
    rows = _load_df_b()
    recipes = sorted({r["small_recipe_id"] for r in rows})
    ingredients = sorted({r["ingredient_name"] for r in rows})
    return (
        {k: i + 1 for i, k in enumerate(recipes)},
        {k: i + 1 for i, k in enumerate(ingredients)},
    )


def recipe_id_of(recipe_key: str) -> Optional[int]:
    """레시피 키(`A1034`) → 정수 recipe_id. 미등록이면 None."""
    return _registries()[0].get(recipe_key)


def ingredient_id_of(name: str) -> Optional[int]:
    """재료명 → 정수 ingredient_id. 미등록이면 None."""
    return _registries()[1].get(name)


def ingredient_name_of(ingredient_id: int) -> Optional[str]:
    """정수 ingredient_id → 재료명. 미등록이면 None."""
    for name, iid in _registries()[1].items():
        if iid == ingredient_id:
            return name
    return None


def list_recipes(limit: int = 50, offset: int = 0, q: str = "") -> list[dict]:
    """레시피 목록(키·정수 id·조리방법·재료 수). q가 주어지면 키 부분일치 필터.

    Args:
        limit: 최대 반환 수.
        offset: 시작 위치.
        q: 레시피 키 부분일치 검색어.

    Returns:
        [{recipe_key, recipe_id, group_type, cooking_method, n_ingredients}] 리스트.
    """
    grouped: dict[str, dict] = {}
    for r in _load_df_b():
        key = r["small_recipe_id"]
        item = grouped.setdefault(key, {
            "recipe_key": key,
            "recipe_id": recipe_id_of(key),
            "group_type": r["group_type"],
            "cooking_method": r["cooking_method"],
            "n_ingredients": 0,
        })
        item["n_ingredients"] += 1
    out = [v for k, v in sorted(grouped.items()) if not q or q in k]
    return out[offset:offset + limit]


def get_recipe_ingredients(recipe_key: str) -> list[dict]:
    """레시피의 재료별 1인분 투입량(base)을 반환한다.

    Args:
        recipe_key: df_B.csv의 small_recipe_id.

    Returns:
        [{ingredient_id, ingredient_name, base_amount_g, role, unit}] 리스트.
        레시피가 없으면 빈 리스트.
    """
    out = []
    for r in _load_df_b():
        if r["small_recipe_id"] != recipe_key:
            continue
        out.append({
            "ingredient_id": ingredient_id_of(r["ingredient_name"]),
            "ingredient_name": r["ingredient_name"],
            "base_amount_g": float(r["base"]),
            "role": r["role"],
            "unit": r["unit"],
        })
    return out


def get_recipe_meta(recipe_key: str) -> Optional[dict]:
    """레시피 메타(조리방법·3대분류). 없으면 None."""
    for r in _load_df_b():
        if r["small_recipe_id"] == recipe_key:
            return {
                "recipe_key": recipe_key,
                "recipe_id": recipe_id_of(recipe_key),
                "group_type": r["group_type"],
                "cooking_method": r["cooking_method"],
            }
    return None


@functools.lru_cache(maxsize=1)
def _load_coefficients() -> list[dict]:
    """scaling_coefficients.csv 로드(캐시). 331행, 수정 금지 자산."""
    with open(config.COEFFICIENTS_CSV, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh))


def list_coefficients(
    group_type: str = "", ingredient_category: str = "", limit: int = 100
) -> list[dict]:
    """스케일링 계수 조회(학술 참조용 — 운영 스케일링에는 적용하지 않음).

    Args:
        group_type: 3대분류 필터(dry_heat/moist_heat/no_heat 또는 CSV 표기).
        ingredient_category: 재료 카테고리 필터.
        limit: 최대 반환 수.

    Returns:
        계수 행 리스트(원본 컬럼 유지).
    """
    rows = _load_coefficients()
    if group_type:
        rows = [r for r in rows if r.get("group_type") == group_type]
    if ingredient_category:
        rows = [r for r in rows if r.get("ingredient_category") == ingredient_category]
    return rows[:limit]
