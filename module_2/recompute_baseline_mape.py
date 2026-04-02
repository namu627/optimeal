"""
recompute_baseline_mape.py
==========================
Baseline MAPE 재산출 — ADR 확정 ratio 필터 [0.2, 3.0] 적용
담당: 재산출 검증 (원본 파일 수정 없음)

[재산출 배경]
  - 공식 Baseline (baseline_model_v0.02.py): RATIO_MIN=0.5, RATIO_MAX=2.0 → n=342, MAPE=32.68%
  - ADR 확정 임계값 (2026-03-20): ratio ∈ [0.2, 3.0] ("변경 금지")
  - 두 기준 간 불일치로 인해 ADR 기준 Baseline MAPE 재산출 필요

[데이터]
  - 원본: final_submission_recipe_similarity_pairs_20260317.csv (파일 없음 — 대체 사용)
  - 대체: module_2/df_B.csv
    * df_B.csv는 [0.2, 3.0] 필터 + MixedLM 전처리(g 단위, derived_category 있는 행) 적용 후 n=607
    * 원본 pairs CSV 없이 계산하는 근사치임을 명시

[MAPE 수식 — baseline_model_v0.02.py와 동일]
  MAPE = mean(|actual - predicted| / actual * 100)  where actual != 0
  Baseline: predicted = base × N  →  |actual - predicted| / actual = |ratio - 1| / ratio
  따라서: MAPE = mean(|ratio - 1| / ratio * 100)
"""

import pandas as pd
import numpy as np
from pathlib import Path

# ─────────────────────────────────────────────
# 경로 설정
# ─────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
DF_B_PATH = BASE_DIR / "df_B.csv"
OFFICIAL_MAPE = 32.6836  # 공식 Baseline (ratio 필터 [0.5, 2.0], n=342)
OFFICIAL_N    = 342
TARGET_IMPROVEMENT = 0.80  # 20% 개선 목표

# ADR 확정 임계값
RATIO_MIN_ADR = 0.2
RATIO_MAX_ADR = 3.0
RATIO_MIN_OLD = 0.5
RATIO_MAX_OLD = 2.0


def calc_mape(actual: pd.Series, predicted: pd.Series) -> float:
    """baseline_model_v0.02.py와 동일한 수식"""
    mask = actual != 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


def calc_mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def calc_rmse(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def analyze_subset(df: pd.DataFrame, label: str) -> dict:
    actual    = df["Y"]
    predicted = df["base"] * df["N"]
    return {
        "구분": label,
        "n": len(df),
        "MAE": round(calc_mae(actual, predicted), 2),
        "MAPE(%)": round(calc_mape(actual, predicted), 4),
        "RMSE": round(calc_rmse(actual, predicted), 2),
    }


def main():
    print("=" * 60)
    print("Baseline MAPE 재산출 — ADR ratio 필터 [0.2, 3.0]")
    print("=" * 60)

    # ─── 1. 데이터 로딩 ───────────────────────────────────────────
    df = pd.read_csv(DF_B_PATH)
    print(f"\n[로딩] df_B.csv: {len(df)}행 x {len(df.columns)}열")
    print(f"  columns: {list(df.columns)}")

    # ratio 범위 확인
    print(f"\n[ratio 범위 확인]")
    print(f"  min={df['ratio'].min():.4f}, max={df['ratio'].max():.4f}")
    print(f"  ratio < {RATIO_MIN_ADR}: {(df['ratio'] < RATIO_MIN_ADR).sum()}행")
    print(f"  ratio > {RATIO_MAX_ADR}: {(df['ratio'] > RATIO_MAX_ADR).sum()}행")

    # ─── 2. [0.2, 3.0] 필터 적용 ─────────────────────────────────
    df_adr = df[
        (df["base"] > 0) &
        (df["N"] > 0) &
        (df["ratio"] >= RATIO_MIN_ADR) &
        (df["ratio"] <= RATIO_MAX_ADR)
    ].copy()
    print(f"\n[ADR 필터 적용 후] n={len(df_adr)} (원본 {len(df)}에서 {len(df)-len(df_adr)}행 제외)")

    # ─── 3. [0.5, 2.0] 필터로도 계산 (공식 기준과 비교) ─────────
    df_old = df[
        (df["base"] > 0) &
        (df["N"] > 0) &
        (df["ratio"] >= RATIO_MIN_OLD) &
        (df["ratio"] <= RATIO_MAX_OLD)
    ].copy()
    print(f"[구 필터 적용 후]  n={len(df_old)} (공식 Baseline n=342와 비교)")

    # ─── 4. MAPE 계산 ─────────────────────────────────────────────
    results_adr = []
    results_adr.append(analyze_subset(df_adr, "전체 (ADR [0.2,3.0])"))
    for g, gdf in df_adr.groupby("group_type"):
        results_adr.append(analyze_subset(gdf, f"group_type={g}"))
    for r, rdf in df_adr.groupby("role"):
        results_adr.append(analyze_subset(rdf, f"role={r}"))

    results_old = []
    results_old.append(analyze_subset(df_old, "전체 (구 [0.5,2.0])"))
    for g, gdf in df_old.groupby("group_type"):
        results_old.append(analyze_subset(gdf, f"group_type={g}"))
    for r, rdf in df_old.groupby("role"):
        results_old.append(analyze_subset(rdf, f"role={r}"))

    df_adr_result = pd.DataFrame(results_adr)
    df_old_result = pd.DataFrame(results_old)

    # ─── 5. 결과 출력 ─────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("◆ ADR 기준 [0.2, 3.0] Baseline MAPE")
    print("=" * 60)
    print(df_adr_result.to_string(index=False))

    print("\n" + "=" * 60)
    print("◆ 구 기준 [0.5, 2.0] Baseline MAPE (df_B.csv 서브셋)")
    print("=" * 60)
    print(df_old_result.to_string(index=False))

    # ─── 6. 요약 비교 ─────────────────────────────────────────────
    new_mape = df_adr_result.loc[df_adr_result["구분"] == "전체 (ADR [0.2,3.0])", "MAPE(%)"].values[0]
    old_mape_recomp = df_old_result.loc[df_old_result["구분"] == "전체 (구 [0.5,2.0])", "MAPE(%)"].values[0]
    new_n    = df_adr_result.loc[df_adr_result["구분"] == "전체 (ADR [0.2,3.0])", "n"].values[0]

    new_target = new_mape * TARGET_IMPROVEMENT
    old_target = OFFICIAL_MAPE * TARGET_IMPROVEMENT

    print("\n" + "=" * 60)
    print("◆ 최종 비교 요약")
    print("=" * 60)
    print(f"  [공식 Baseline]    MAPE={OFFICIAL_MAPE:.4f}%  (n={OFFICIAL_N}, 필터=[0.5,2.0])")
    print(f"  [df_B 재산출]      MAPE={old_mape_recomp:.4f}%  (n={len(df_old)}, 필터=[0.5,2.0])")
    print(f"  [ADR 기준 재산출]  MAPE={new_mape:.4f}%  (n={new_n}, 필터=[0.2,3.0])")
    print()
    print(f"  ─── 완료 기준 (20% 개선) ───")
    print(f"  공식 기준 목표:   MAPE ≤ {old_target:.4f}%  (32.68% × 0.80)")
    print(f"  ADR 기준 목표:   MAPE ≤ {new_target:.4f}%  ({new_mape:.4f}% × 0.80)")
    print()

    # ratio 분포 통계
    print("  ─── ratio 분포 (ADR [0.2,3.0] 서브셋) ───")
    print(f"  mean={df_adr['ratio'].mean():.4f}, median={df_adr['ratio'].median():.4f}")
    print(f"  std={df_adr['ratio'].std():.4f}")
    print(f"  ratio < 1.0: {(df_adr['ratio'] < 1.0).sum()}행 ({(df_adr['ratio'] < 1.0).mean()*100:.1f}%)")
    print(f"  ratio >= 1.0: {(df_adr['ratio'] >= 1.0).sum()}행 ({(df_adr['ratio'] >= 1.0).mean()*100:.1f}%)")

    print("\n" + "=" * 60)
    print("◆ 주의사항")
    print("=" * 60)
    print("  1. df_B.csv는 MixedLM 전처리(g단위, derived_category 있는 행만)를")
    print("     추가로 거쳐 n=607임. 원본 pairs CSV 기반 계산과 다를 수 있음.")
    print("  2. 공식 Baseline(n=342)과 df_B 구 필터 재산출(n 확인 필요)의 차이는")
    print("     pairs CSV + Excel 조인 단계에서의 재료 매칭 차이에 기인.")
    print("  3. ADR 기준 완료 기준은 팀 회의 후 todo.md에 반영 필요.")


if __name__ == "__main__":
    main()
