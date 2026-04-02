"""
baseline_model.py
==================
OptiMeal 프로젝트 — Baseline 선형 모델 코드 + 오차 분석 프레임워크
담당: 박미연
기준 문서: ADR-001 (멱함수 독립변수 정의)

[Baseline 수식 — ADR-001 Option A]
    ratio = Y / (base_amount × N) = 1 고정 (완전 선형)
    Y_predicted = base_amount_g × target_serving_size
    b = 1 동치

[역할]
    4월 3종 비교(선형 vs Rule-based vs 하이브리드)의 기준선(Baseline)
"""

import pandas as pd
import numpy as np

# ──────────────────────────────────────────────
# 0. 파일 경로 설정 (필요 시 수정)
# ──────────────────────────────────────────────

PATH_PAIRS  = "final_submission_recipe_similarity_pairs_20260317.csv"
PATH_SMALL  = "소규모_레시피_DB_남유찬_v0_10.xlsx"
PATH_LARGE  = "대규모_레시피_DB_남유찬_v0_08.xlsx"
PATH_OUTPUT = "baseline_result.csv"

SHEET_NAME    = "대용량_레시피_입력템플릿"
N_INGREDIENTS = 28  # 재료 컬럼 최대 개수


# ──────────────────────────────────────────────
# 1. 데이터 로딩
# ──────────────────────────────────────────────

def load_pairs(path: str) -> pd.DataFrame:
    """
    매칭 쌍 CSV 로딩
    ADR-002 v6 B안: is_best_per_large_recommendation=True 기준
    """
    df = pd.read_csv(path)
    df = df[df["is_best_per_large_recommendation"] == True].copy()
    print(f"[pairs] 로딩 완료: {len(df)}쌍")
    return df


def load_recipe_db(path: str, sheet: str) -> pd.DataFrame:
    """소규모 / 대규모 레시피 엑셀 로딩"""
    df = pd.read_excel(path, sheet_name=sheet, header=1)
    df = df.dropna(subset=["recipe_id"])
    df = df[df["recipe_id"] != "레시피 ID(임시)"].copy()
    print(f"[recipe_db] 로딩 완료: {len(df)}개 레시피 ({path})")
    return df


# ──────────────────────────────────────────────
# 2. 전처리 — 가로 → 세로 변환
# ──────────────────────────────────────────────

def _safe_float(val):
    """비정형 단위(약간, 적당량 등) → None 처리"""
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def melt_ingredients(df: pd.DataFrame, n: int = N_INGREDIENTS) -> pd.DataFrame:
    """
    가로 형태(ingredient_1_name, ingredient_1_amount ...)를
    세로 형태(recipe_id, ingredient_name, amount_g, role)로 변환
    g 단위만 사용 (비정형 단위는 별도 환산 테이블 필요)
    """
    records = []
    for _, row in df.iterrows():
        for i in range(1, n + 1):
            name   = row.get(f"ingredient_{i}_name")
            amount = row.get(f"ingredient_{i}_amount")
            unit   = row.get(f"ingredient_{i}_unit")
            role   = row.get(f"ingredient_{i}_role")

            if pd.isna(name) or pd.isna(amount):
                continue
            amount = _safe_float(amount)
            if amount is None:
                continue

            records.append({
                "recipe_id":       row["recipe_id"],
                "serving_size":    row["serving_size"],
                "ingredient_name": str(name).strip(),
                "amount_g":        amount,
                "unit":            str(unit).strip() if pd.notna(unit) else "g",
                "role":            str(role).strip() if pd.notna(role) else "unknown",
            })

    result = pd.DataFrame(records)
    result = result[result["unit"] == "g"].copy()
    print(f"[melt] 변환 완료: {len(result)}개 재료 행 (g 단위 기준)")
    return result


# ──────────────────────────────────────────────
# 3. 매칭 쌍 + 분량 데이터 조인
# ──────────────────────────────────────────────

def build_analysis_df(
    df_pairs: pd.DataFrame,
    df_small_melted: pd.DataFrame,
    df_large_melted: pd.DataFrame,
) -> pd.DataFrame:
    """
    매칭 쌍 CSV + 소규모 분량 + 대규모 분량 조인
    핵심 컬럼:
        base_amount_g       — 소규모 1인분 재료 분량
        target_amount_g     — 대규모 실제 재료 분량
        target_serving_size — 대규모 인원수 N
    """
    # 소규모: 1인분 기준만 사용
    small = df_small_melted[df_small_melted["serving_size"] == 1].copy()
    small = small.rename(columns={"amount_g": "base_amount_g"})

    # 대규모
    large = df_large_melted.copy()
    large = large.rename(columns={
        "amount_g":     "target_amount_g",
        "serving_size": "target_serving_size",
    })

    # 매칭 쌍 기준 조인
    merged = df_pairs[[
        "pair_id", "group_type",
        "small_recipe_id", "large_recipe_id", "composite_score"
    ]].copy()

    # 소규모 분량 조인
    merged = merged.merge(
        small[["recipe_id", "ingredient_name", "base_amount_g", "role"]],
        left_on="small_recipe_id",
        right_on="recipe_id",
        how="inner",
    ).drop(columns=["recipe_id"])

    # 대규모 분량 조인 (재료명 기준)
    merged = merged.merge(
        large[["recipe_id", "ingredient_name", "target_amount_g", "target_serving_size"]],
        left_on=["large_recipe_id", "ingredient_name"],
        right_on=["recipe_id", "ingredient_name"],
        how="inner",
    ).drop(columns=["recipe_id"])

    # 1인분 환산 (ADR-001)
    merged["per_serving_grams"] = (
        merged["target_amount_g"] / merged["target_serving_size"]
    )

    print(f"[join] 조인 완료: {len(merged)}개 재료 행, {merged['pair_id'].nunique()}쌍")
    return merged


# ──────────────────────────────────────────────
# 4. Baseline 예측 — ADR-001 Option A
# ──────────────────────────────────────────────

def baseline_predict(df: pd.DataFrame) -> pd.DataFrame:
    """
    Baseline 선형 예측 — ADR-001 Option A
        Y_predicted = base_amount_g × target_serving_size
        ratio_baseline = 1.0 (b=1 동치, 완전 선형)
        ratio_actual   = Y / (base × N) — 실제 비율 확인용
    """
    df = df.copy()
    df["predicted_amount_g"] = df["base_amount_g"] * df["target_serving_size"]
    df["ratio_actual"]       = (
        df["target_amount_g"] / (df["base_amount_g"] * df["target_serving_size"])
    )
    df["ratio_baseline"] = 1.0
    return df


# ──────────────────────────────────────────────
# 5. 오차 분석 프레임워크
# ──────────────────────────────────────────────

def calc_mae(actual: pd.Series, predicted: pd.Series) -> float:
    """MAE — 평균 절대 오차 (g)"""
    return float(np.mean(np.abs(actual - predicted)))


def calc_mape(actual: pd.Series, predicted: pd.Series) -> float:
    """
    MAPE — 평균 절대 퍼센트 오차 (%)
    PRD 목표: Baseline ~25% / Rule-based ~10% / 하이브리드 ~7%
    actual=0인 행은 제외
    """
    mask = actual != 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


def calc_rmse(actual: pd.Series, predicted: pd.Series) -> float:
    """RMSE — 큰 오차에 민감한 지표"""
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def analyze_errors(df: pd.DataFrame, label: str = "전체") -> dict:
    """단일 그룹 오차 지표 계산"""
    actual    = df["target_amount_g"]
    predicted = df["predicted_amount_g"]
    return {
        "label":   label,
        "n":       len(df),
        "MAE":     round(calc_mae(actual, predicted), 4),
        "MAPE(%)": round(calc_mape(actual, predicted), 4),
        "RMSE":    round(calc_rmse(actual, predicted), 4),
    }


def run_error_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """
    오차 분석 프레임워크
    Rule-based, 하이브리드 코드에서도 동일 함수 재사용 가능 (3종 비교 기준)
    분석 단위:
        1) 전체
        2) group_type별 (건열/습열/비가열)
        3) role별 (주재료/부재료/양념류 등)
    """
    results = []

    # 1) 전체
    results.append(analyze_errors(df, label="전체"))

    # 2) group_type별
    for g, gdf in df.groupby("group_type"):
        results.append(analyze_errors(gdf, label=f"group_type={g}"))

    # 3) role별
    for r, rdf in df.groupby("role"):
        results.append(analyze_errors(rdf, label=f"role={r}"))

    return pd.DataFrame(results)


# ──────────────────────────────────────────────
# 6. 메인 실행
# ──────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Baseline 선형 모델 실행 (ADR-001 기준)")
    print("=" * 50)

    # 1. 로딩
    df_pairs     = load_pairs(PATH_PAIRS)
    df_small_raw = load_recipe_db(PATH_SMALL, SHEET_NAME)
    df_large_raw = load_recipe_db(PATH_LARGE, SHEET_NAME)

    # 2. 전처리
    df_small_melted = melt_ingredients(df_small_raw)
    df_large_melted = melt_ingredients(df_large_raw)

    # 3. 조인
    df = build_analysis_df(df_pairs, df_small_melted, df_large_melted)

    # 4. Baseline 예측
    df = baseline_predict(df)

    # 5. 오차 분석
    print("\n" + "=" * 50)
    print("오차 분석 결과 (Baseline 선형 모델 — ADR-001)")
    print("=" * 50)
    error_df = run_error_analysis(df)
    print(error_df.to_string(index=False))

    # 6. 결과 저장
    df.to_csv(PATH_OUTPUT, index=False, encoding="utf-8-sig")
    error_df.to_csv("baseline_error_analysis.csv", index=False, encoding="utf-8-sig")
    print(f"\n[완료] baseline_result.csv, baseline_error_analysis.csv 저장 완료")


if __name__ == "__main__":
    main()
