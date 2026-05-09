#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Step 2 — FR-07 ML 보조 모듈 2: Random Forest / XGBoost 학습 및 5-Fold GroupKFold CV
실행: docker exec optimeal_app python module_2/src/ml/train_rf_model.py
입력: data/ml/ml_train_dataset.csv
산출물: data/ml/rf_cv_results.json
"""

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import RandomForestRegressor
from sklearn.model_selection import GroupKFold, cross_val_score
from sklearn.preprocessing import LabelEncoder
from xgboost import XGBRegressor

BASE_DIR = Path(__file__).parent.parent.parent.parent  # project root
INPUT_PATH = BASE_DIR / "data" / "ml" / "ml_train_dataset.csv"
OUTPUT_JSON = BASE_DIR / "data" / "ml" / "rf_cv_results.json"

CATEGORY_MEAN_FALLBACK_B = 0.6163
RANDOM_STATE = 42

# category_mean fallback MAE 기준 (Step 1에서 계산한 값)
BASELINE_MAE = 0.1894


def load_and_preprocess(path: Path) -> tuple[pd.DataFrame, np.ndarray, np.ndarray, np.ndarray]:
    """
    CSV 로드 후 Feature Engineering 적용.

    Returns:
        df       : 원본 DataFrame
        X        : feature matrix (numpy)
        y        : label array (numpy)
        groups   : GroupKFold용 그룹 키 array
    """
    df = pd.read_csv(path)
    print(f"데이터 로드: {len(df)}행, {list(df.columns)}")

    # ── Feature Engineering ──────────────────────────────────────
    # 1) ingredient_category → Label Encoding
    le_cat = LabelEncoder()
    df["cat_enc"] = le_cat.fit_transform(df["ingredient_category"])
    print(f"\ningredient_category 인코딩: {dict(zip(le_cat.classes_, le_cat.transform(le_cat.classes_)))}")

    # 2) group_type → Label Encoding (고정 순서)
    le_gt = LabelEncoder()
    le_gt.fit(["dry_heat", "moist_heat", "no_heat"])
    df["gt_enc"] = le_gt.transform(df["group_type"])
    print(f"group_type 인코딩: {dict(zip(le_gt.classes_, le_gt.transform(le_gt.classes_)))}")

    # 3) base_amount_g → log1p (우분포 보정)
    df["log1p_base_amount"] = np.log1p(df["base_amount_g"].astype(float))

    # 4) target_serving_n → log (ADR-001 기반)
    df["log_target_serving"] = np.log(df["target_serving_n"].astype(float))

    # 5) ratio → 그대로
    feature_cols = ["cat_enc", "gt_enc", "log1p_base_amount", "log_target_serving", "ratio"]
    X = df[feature_cols].values
    y = df["label_b"].astype(float).values

    # GroupKFold 그룹 키: base_recipe_id + '_' + target_recipe_id
    groups = (
        df["base_recipe_id"].astype(str) + "_" + df["target_recipe_id"].astype(str)
    ).values

    print(f"\n[Feature 목록]: {feature_cols}")
    print(f"고유 그룹(레시피 쌍) 수: {len(np.unique(groups))}")
    print(f"X shape: {X.shape}, y shape: {y.shape}")
    return df, X, y, groups, le_cat, le_gt


def run_cv(model, X: np.ndarray, y: np.ndarray, groups: np.ndarray, model_name: str) -> dict:
    """5-Fold GroupKFold CV 실행, MAE mean/std 반환."""
    cv = GroupKFold(n_splits=5)
    scores = cross_val_score(
        model, X, y,
        cv=cv,
        groups=groups,
        scoring="neg_mean_absolute_error",
        n_jobs=-1,
    )
    mae_scores = -scores
    result = {
        "model": model_name,
        "cv_mae_per_fold": mae_scores.tolist(),
        "cv_mae_mean": float(mae_scores.mean()),
        "cv_mae_std":  float(mae_scores.std()),
    }
    print(f"\n[{model_name}] 5-Fold GroupKFold CV MAE")
    for i, m in enumerate(mae_scores, 1):
        print(f"  Fold {i}: {m:.4f}")
    print(f"  Mean: {mae_scores.mean():.4f} ± {mae_scores.std():.4f}")
    return result


def feature_importance_report(model, feature_cols: list[str]) -> list[dict]:
    """RF feature importance 상위 5개 반환."""
    importances = model.feature_importances_
    fi = sorted(
        zip(feature_cols, importances),
        key=lambda x: x[1],
        reverse=True,
    )[:5]
    print("\n[RF Feature Importance 상위 5개]")
    for feat, imp in fi:
        print(f"  {feat}: {imp:.4f}")
    return [{"feature": f, "importance": float(imp)} for f, imp in fi]


def main():
    if not INPUT_PATH.exists():
        print(f"[오류] 입력 파일 없음: {INPUT_PATH}")
        print("Step 1 (prepare_rf_training_data.py) 를 먼저 실행하세요.")
        sys.exit(1)

    df, X, y, groups, le_cat, le_gt = load_and_preprocess(INPUT_PATH)
    feature_cols = ["cat_enc", "gt_enc", "log1p_base_amount", "log_target_serving", "ratio"]

    # ── 모델 정의 ─────────────────────────────────────────────────
    rf = RandomForestRegressor(n_estimators=100, random_state=RANDOM_STATE)
    xgb = XGBRegressor(
        n_estimators=100,
        random_state=RANDOM_STATE,
        verbosity=0,
        eval_metric="mae",
    )

    # ── CV 실행 ───────────────────────────────────────────────────
    print("\n" + "="*55)
    print("5-Fold GroupKFold 교차 검증 시작")
    print("="*55)
    rf_result  = run_cv(rf,  X, y, groups, "RandomForest")
    xgb_result = run_cv(xgb, X, y, groups, "XGBoost")

    # ── baseline MAE 재계산 (label_b 기준) ───────────────────────
    baseline_mae = float(np.abs(y - CATEGORY_MEAN_FALLBACK_B).mean())
    print(f"\n[category_mean fallback(b={CATEGORY_MEAN_FALLBACK_B}) 기준 MAE]: {baseline_mae:.4f}")

    # ── 개선율 계산 ───────────────────────────────────────────────
    rf_improvement  = (baseline_mae - rf_result["cv_mae_mean"])  / baseline_mae * 100
    xgb_improvement = (baseline_mae - xgb_result["cv_mae_mean"]) / baseline_mae * 100

    print(f"\n[baseline 대비 MAE 개선율]")
    print(f"  RandomForest:  {rf_improvement:+.1f}%")
    print(f"  XGBoost:       {xgb_improvement:+.1f}%")

    # ── 전체 학습 후 feature importance ──────────────────────────
    rf.fit(X, y)
    fi_report = feature_importance_report(rf, feature_cols)

    # ── 최우수 모델 결정 ─────────────────────────────────────────
    best_name = (
        "RandomForest" if rf_result["cv_mae_mean"] <= xgb_result["cv_mae_mean"]
        else "XGBoost"
    )
    best_mae = min(rf_result["cv_mae_mean"], xgb_result["cv_mae_mean"])
    fr07_valid = best_mae < baseline_mae

    print(f"\n{'='*55}")
    print(f"최우수 모델: {best_name} (MAE {best_mae:.4f})")
    print(f"FR-07 유효 판정: {'✅ 유효 (ML이 fallback 대비 개선)' if fr07_valid else '❌ 제외 (ML이 fallback 대비 개선 없음)'}")
    print(f"{'='*55}")

    # ── 결과 저장 ─────────────────────────────────────────────────
    results = {
        "random_forest": rf_result,
        "xgboost":       xgb_result,
        "baseline": {
            "model":    "category_mean_fallback",
            "b_value":  CATEGORY_MEAN_FALLBACK_B,
            "mae":      baseline_mae,
        },
        "improvement": {
            "random_forest_pct": float(rf_improvement),
            "xgboost_pct":       float(xgb_improvement),
        },
        "feature_importance_rf_top5": fi_report,
        "best_model":  best_name,
        "fr07_valid":  fr07_valid,
        "feature_cols": feature_cols,
        "category_encoding": {
            cat: int(enc)
            for cat, enc in zip(le_cat.classes_, le_cat.transform(le_cat.classes_))
        },
        "group_type_encoding": {
            gt: int(enc)
            for gt, enc in zip(le_gt.classes_, le_gt.transform(le_gt.classes_))
        },
    }

    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n[완료] 저장: {OUTPUT_JSON}")


if __name__ == "__main__":
    main()
