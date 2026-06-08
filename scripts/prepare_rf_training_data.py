#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 1 — FR-07 ML 보조 모듈 2 학습 데이터 추출
실행: docker exec optimeal_app python scripts/prepare_rf_training_data.py
산출물: data/ml/ml_train_dataset.csv
"""

import os
import sys
from pathlib import Path

import pandas as pd
from sqlalchemy import create_engine, text

BASE_DIR = Path(__file__).parent.parent
OUTPUT_PATH = BASE_DIR / "data" / "ml" / "ml_train_dataset.csv"

# ingredient_category(영문) → scaling_coefficient.ingredient_category(한국어) 매핑
CATEGORY_MAP = {
    "oil_fat":    "유지류",
    "water_base": "수분류",
}

# ingredient_role(한국어) → ingredient_category(한국어) 폴백 매핑
ROLE_TO_CATEGORY = {
    "주재료": "주재료",
    "부재료": "부재료",
    "조미료": "양념류",
    "양념":   "양념류",
}

# scaling_coefficient.ingredient_category 기준 카테고리 평균 b (category_mean fallback)
CATEGORY_MEAN_FALLBACK_B = 0.6163

# SQL 쿼리: ml_training_dataset × scaling_coefficient(id 10~24)
# - ingredient_category 영문→한국어 변환 후 JOIN
# - NULL인 경우 ingredient_role로 카테고리 결정
QUERY = text("""
SELECT
    mld.ingredient_category  AS raw_ingredient_category,
    mld.ingredient_role,
    mld.group_type,
    mld.base_amount_g,
    mld.target_serving_size  AS target_serving_n,
    mld.ratio,
    mld.base_recipe_id,
    mld.target_recipe_id,
    CASE
        WHEN mld.ingredient_category = 'oil_fat'          THEN '유지류'
        WHEN mld.ingredient_category = 'water_base'       THEN '수분류'
        WHEN mld.ingredient_role = '주재료'               THEN '주재료'
        WHEN mld.ingredient_role = '부재료'               THEN '부재료'
        WHEN mld.ingredient_role IN ('조미료', '양념')    THEN '양념류'
        ELSE NULL
    END                      AS ingredient_category,
    sc.power_law_b           AS label_b
FROM ml_training_dataset mld
LEFT JOIN scaling_coefficient sc
    ON sc.group_type = mld.group_type
    AND sc.ingredient_category = CASE
        WHEN mld.ingredient_category = 'oil_fat'          THEN '유지류'
        WHEN mld.ingredient_category = 'water_base'       THEN '수분류'
        WHEN mld.ingredient_role = '주재료'               THEN '주재료'
        WHEN mld.ingredient_role = '부재료'               THEN '부재료'
        WHEN mld.ingredient_role IN ('조미료', '양념')    THEN '양념류'
        ELSE NULL
    END
    AND sc.coefficient_id BETWEEN 10 AND 24
WHERE mld.ratio BETWEEN 0.2 AND 3.0
""")


def get_engine():
    host     = os.getenv("POSTGRES_HOST", "db")
    port     = os.getenv("POSTGRES_PORT", "5432")
    db       = os.getenv("POSTGRES_DB",   "optimeal")
    user     = os.getenv("POSTGRES_USER", "optimeal")
    password = os.getenv("POSTGRES_PASSWORD", "optimeal_dev_pw")
    url = f"postgresql+psycopg2://{user}:{password}@{host}:{port}/{db}"
    return create_engine(url)


def print_stats(df: pd.DataFrame, stage: str) -> None:
    print(f"\n{'='*50}")
    print(f"[{stage}] 행 수: {len(df)}")
    print(f"{'='*50}")


def main():
    print("DB 연결 중...")
    engine = get_engine()

    with engine.connect() as conn:
        df = pd.read_sql(QUERY, conn)

    total = len(df)
    print(f"총 추출 행 수: {total}")

    # label_b NULL 비율 확인 — 50% 이상이면 중단
    null_b = df["label_b"].isna().sum()
    null_pct = null_b / total * 100
    print(f"label_b NULL 행: {null_b} ({null_pct:.1f}%)")

    if null_pct >= 50.0:
        print("\n[중단] label_b NULL 행이 50% 이상입니다. DB 또는 JOIN 조건 확인 필요.")
        sys.exit(1)

    # label_b NULL 제거
    df = df.dropna(subset=["label_b"]).reset_index(drop=True)
    df["label_b"] = df["label_b"].astype(float)
    print(f"label_b NULL 제거 후 행 수: {len(df)}")

    # ── 카테고리별 행 수 ──────────────────────────────────────────
    print("\n[카테고리별 행 수]")
    cat_counts = df["ingredient_category"].value_counts()
    for cat, cnt in cat_counts.items():
        pct = cnt / len(df) * 100
        print(f"  {cat}: {cnt}건 ({pct:.1f}%)")

    # ── group_type 분포 ───────────────────────────────────────────
    print("\n[group_type별 행 수]")
    for gt, cnt in df["group_type"].value_counts().items():
        print(f"  {gt}: {cnt}건")

    # ── 카테고리 × group_type 교차표 ─────────────────────────────
    print("\n[카테고리 × group_type 교차표]")
    cross = pd.crosstab(
        df["ingredient_category"],
        df["group_type"],
        margins=True,
        margins_name="합계"
    )
    print(cross.to_string())

    # ── label_b 분포 ─────────────────────────────────────────────
    print("\n[label_b 기술통계]")
    print(df["label_b"].describe().to_string())

    print("\n[카테고리별 label_b 평균]")
    cat_b_mean = df.groupby("ingredient_category")["label_b"].mean().sort_index()
    for cat, b_val in cat_b_mean.items():
        print(f"  {cat}: {b_val:.4f}")

    # ── category_mean fallback 대비 기준 MAE ─────────────────────
    # 기준: 항상 b=0.6163을 예측할 때의 MAE (label_b 기준)
    baseline_mae = (df["label_b"] - CATEGORY_MEAN_FALLBACK_B).abs().mean()
    print(f"\n[category_mean fallback(b={CATEGORY_MEAN_FALLBACK_B}) 기준 MAE(label_b 기준)]: {baseline_mae:.4f}")
    print("(Step 2 ML 모델 MAE가 이 값보다 낮아야 FR-07 유효)")

    # ── 저장 ─────────────────────────────────────────────────────
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)

    # 최종 출력 컬럼 순서 정의
    output_cols = [
        "ingredient_category",
        "ingredient_role",
        "group_type",
        "base_amount_g",
        "target_serving_n",
        "ratio",
        "base_recipe_id",
        "target_recipe_id",
        "label_b",
    ]
    df[output_cols].to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n[완료] 저장: {OUTPUT_PATH}")
    print(f"최종 행 수: {len(df)}, 컬럼: {output_cols}")


if __name__ == "__main__":
    main()
