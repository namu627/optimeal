"""
recipe_loader.py
================
1인분 레시피 로더 — 소규모 레시피 DB 엑셀 우선.

소스: data/raw/소규모_레시피_DB_남유찬_v0_10.xlsx
  - 시트: 대용량_레시피_입력템플릿
  - header=1 (영어 코드명), row 0은 한국어 라벨이라 drop
  - 1인분(serving_size=1) 메뉴 약 2,400건, 메뉴당 평균 8개 재료(최대 28)
  - cooking_method_group_type: 습열/건열/비가열
  - 재료 슬롯: ingredient_{1..28}_{name,amount,unit,role}
  - role: 주재료 / 부재료 / 양념 / 조미료
  - unit: g 또는 recipe (sub-recipe 참조)

본 로더는 발표용 통합 데모(For_finalexam)에서 사용된다. 한 메뉴의 모든 재료가
빠짐없이 스케일링되도록 엑셀 원본을 그대로 로드한다 (df_B는 매칭된 공통 재료만
보존하여 평균 3개로 축소된 결정체이므로 발표 풀세트 시연에는 부적합).

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from hybrid_engine import IngredientItem

_ROOT = Path(__file__).parent.parent
DEFAULT_XLSX = _ROOT / "data" / "raw" / "소규모_레시피_DB_남유찬_v0_10.xlsx"
SHEET_NAME = "대용량_레시피_입력템플릿"

# role 한국어 → derived_category/role 매핑 (모듈 2 카테고리 체계)
# 주재료/부재료는 role로, 양념/조미료는 양념류로, 유지류/수분류는 재료명 키워드로
ROLE_TO_INTERNAL: dict[str, dict] = {
    "주재료": {"role": "main", "derived_category": None},
    "부재료": {"role": "sub",  "derived_category": None},
    "양념":   {"role": "seasoning", "derived_category": None},
    "조미료": {"role": "seasoning", "derived_category": None},
}

# 재료명 키워드 → derived_category (수분류 / 유지류 추론)
OIL_FAT_KEYWORDS = (
    "참기름", "들기름", "식용유", "올리브유", "카놀라유", "현미유",
    "포도씨유", "옥수수유", "콩기름", "쇼트닝", "버터", "마가린", "기름", "유"
)
WATER_BASE_KEYWORDS = (
    "물", "맛국물", "육수", "다시마국물", "다시마육수", "멸치육수",
    "닭육수", "소고기육수", "사골육수", "국물", "쌀뜨물", "다시국물"
)


def _derive_category(ing_name: str, role_internal: dict) -> dict:
    """재료명 키워드 기반으로 유지류/수분류 자동 추론.

    role이 양념/조미료이거나 단순 부재료여도 재료명이 기름/물 계열이면
    derived_category로 격상한다. 이는 모듈 2의 카테고리 체계(5분류)와 정합.

    Args:
        ing_name: 재료명.
        role_internal: ROLE_TO_INTERNAL의 사본.

    Returns:
        role/derived_category가 채워진 dict.
    """
    nm = (ing_name or "").strip()
    # 정확 매칭 우선
    if any(kw == nm or nm.endswith(kw) for kw in OIL_FAT_KEYWORDS):
        return {"role": role_internal["role"], "derived_category": "oil_fat"}
    if any(kw == nm for kw in WATER_BASE_KEYWORDS) or nm == "물":
        return {"role": role_internal["role"], "derived_category": "water_base"}
    # 부분 매칭은 더 엄격하게 — '유'만 들어가도 매칭되면 오탐 폭증
    if nm.endswith("기름") or nm.endswith("버터"):
        return {"role": role_internal["role"], "derived_category": "oil_fat"}
    if "육수" in nm or "맛국물" in nm:
        return {"role": role_internal["role"], "derived_category": "water_base"}
    return role_internal


@dataclass
class RecipeBundle:
    """1인분 레시피 단위 (엑셀 기반).

    Attributes:
        recipe_id: 엑셀 recipe_id 값 (A0012, Y0001 등).
        menu_name: 한국어 메뉴명.
        group_type_kor: 한국어 group_type ('습열'/'건열'/'비가열').
        cooking_method: 주조리법.
        items: 재료 리스트 (IngredientItem).
        source_row_count: 엑셀 행수 (디버그용, 1).
        skipped_non_g: g 단위가 아닌 슬롯 수 (recipe/etc).
        total_base_g: 재료 base 합계 (plausibility 필터 보조 + 표시용).
        matched_in_dfB: df_B.csv 매칭 통과 여부 (1-5월 매칭 작업 흔적).
    """
    recipe_id: str
    menu_name: str
    group_type_kor: str
    cooking_method: str
    items: list[IngredientItem]
    source_row_count: int
    skipped_non_g: int = 0
    total_base_g: float = 0.0
    matched_in_dfB: bool = False


# 1인분 적정 범위 (g): 너무 작으면 양념 sub-recipe, 너무 크면 데이터 입력 오류 (예: M0001)
PLAUSIBLE_MIN_G: float = 50.0
PLAUSIBLE_MAX_G: float = 3000.0


def _parse_row(
    row: pd.Series,
    dfb_override: dict[str, tuple[str, str]] | None = None,
) -> RecipeBundle | None:
    """엑셀 한 행을 RecipeBundle로 변환.

    엑셀의 cooking_method_group_type이 NaN이면 dfb_override(매칭 메뉴 한정으로
    df_B에서 가져온 group_type/cooking_method 사전)에서 채운다. 둘 다 없으면 None.
    """
    rid = str(row.get("recipe_id", "")).strip()
    gt = row.get("cooking_method_group_type")
    cooking = row.get("cooking_method_primary")

    gt_str: str | None = None
    cooking_str: str | None = None
    if isinstance(gt, str) and gt.strip() in ("습열", "건열", "비가열"):
        gt_str = gt.strip()
        cooking_str = (
            str(cooking).strip() if isinstance(cooking, str) else gt_str
        )
    elif dfb_override and rid in dfb_override:
        gt_str, cooking_str = dfb_override[rid]
    else:
        return None

    items: list[IngredientItem] = []
    skipped = 0
    for n in range(1, 29):
        name = row.get(f"ingredient_{n}_name")
        if not isinstance(name, str) or not name.strip():
            continue
        amount = row.get(f"ingredient_{n}_amount")
        unit = row.get(f"ingredient_{n}_unit")
        role_kor = row.get(f"ingredient_{n}_role")
        try:
            amt = float(amount)
        except (TypeError, ValueError):
            continue
        if amt <= 0:
            continue
        unit_str = str(unit).strip().lower() if isinstance(unit, str) else "g"
        if unit_str != "g":
            skipped += 1
            continue
        if not isinstance(role_kor, str) or role_kor.strip() not in ROLE_TO_INTERNAL:
            # role 매핑 불가 → 부재료로 보수적 처리
            base = {"role": "sub", "derived_category": None}
        else:
            base = dict(ROLE_TO_INTERNAL[role_kor.strip()])
        cat = _derive_category(name, base)
        items.append(IngredientItem(
            name=name.strip(),
            base=amt,
            role=cat["role"],
            derived_category=cat["derived_category"],
        ))

    if not items:
        return None
    total_g = sum(it.base for it in items)
    return RecipeBundle(
        recipe_id=rid,
        menu_name=str(row["recipe_name"]).strip(),
        group_type_kor=gt_str,
        cooking_method=cooking_str or gt_str,
        items=items,
        source_row_count=1,
        skipped_non_g=skipped,
        total_base_g=total_g,
    )


def load_recipes(
    xlsx_path: str | Path = DEFAULT_XLSX,
    apply_plausibility_filter: bool = True,
    df_b_path: str | Path | None = None,
) -> dict[str, RecipeBundle]:
    """엑셀에서 1인분 레시피 전부 로드.

    Args:
        xlsx_path: 엑셀 경로.
        apply_plausibility_filter: True면 total_base_g 가
            [PLAUSIBLE_MIN_G, PLAUSIBLE_MAX_G] 범위 밖인 메뉴 제외 (데이터 입력
            오류 회피, 예: M0001은 1인분이라 표시되어 있으나 51kg 합계).
        df_b_path: 지정하면 그 small_recipe_id들을 matched_in_dfB=True로 표시.

    Returns:
        {recipe_id: RecipeBundle}.
    """
    df = pd.read_excel(xlsx_path, sheet_name=SHEET_NAME, header=1)
    df = df.iloc[1:].reset_index(drop=True)
    df = df[df["serving_size"] == 1]

    matched_ids: set[str] = set()
    dfb_override: dict[str, tuple[str, str]] = {}
    path = df_b_path or (_ROOT / "module_2" / "df_B.csv")
    if Path(path).exists():
        try:
            df_b = pd.read_csv(path)
            matched_ids = set(df_b["small_recipe_id"].astype(str).unique())
            # 메뉴별 group_type / cooking_method 첫 값으로 override
            first = df_b.drop_duplicates(subset=["small_recipe_id"])
            for _, r in first.iterrows():
                rid = str(r["small_recipe_id"])
                gt = str(r.get("group_type", "")).strip()
                cm = str(r.get("cooking_method", "")).strip()
                if gt in ("습열", "건열", "비가열"):
                    dfb_override[rid] = (gt, cm if cm and cm != "nan" else gt)
        except Exception:
            matched_ids = set()

    bundles: dict[str, RecipeBundle] = {}
    for _, row in df.iterrows():
        b = _parse_row(row, dfb_override=dfb_override)
        if b is None or not b.recipe_id or b.recipe_id == "nan":
            continue
        if apply_plausibility_filter and not (
            PLAUSIBLE_MIN_G <= b.total_base_g <= PLAUSIBLE_MAX_G
        ):
            continue
        b.matched_in_dfB = b.recipe_id in matched_ids
        bundles[b.recipe_id] = b
    return bundles


def pick_recipe(
    bundles: dict[str, RecipeBundle],
    recipe_id: str | None = None,
    seed: int | None = None,
) -> RecipeBundle:
    """recipe_id 지정 또는 랜덤 추출.

    Args:
        bundles: load_recipes 결과.
        recipe_id: 지정 ID. None이면 랜덤.
        seed: 랜덤 시드.

    Raises:
        KeyError: 지정 recipe_id 부재.
    """
    if recipe_id is not None:
        if recipe_id not in bundles:
            raise KeyError(f"recipe_id '{recipe_id}' not found in 소규모 DB")
        return bundles[recipe_id]
    rng = random.Random(seed)
    return bundles[rng.choice(sorted(bundles.keys()))]
