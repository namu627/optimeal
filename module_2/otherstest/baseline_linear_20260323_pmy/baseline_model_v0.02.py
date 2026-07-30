"""
baseline_model.py (보완버전)
==============================
OptiMeal 프로젝트 — Baseline 선형 모델 코드 + 오차 분석 프레임워크
담당: 박미연
기준 문서: ADR-001 (멱함수 독립변수 정의), FR-04 v1.3 (ratio 이상치 사전 임계값)

[Baseline 수식 — ADR-001 Option A]
    ratio = Y / (base_amount × N) = 1 고정 (완전 선형)
    Y_predicted = base_amount_g × target_serving_size
    b = 1 동치

[비정상 계산 방지 기준 — FR-04 v1.3]
    1) base_amount_g <= 0        → MAPE 무한대 / 예측값 왜곡 방지
    2) target_serving_size <= 0  → 예측값 음수 또는 0 방지
    3) ratio < 0.2 또는 > 3.0   → ADR 확정 임계값 (2026-03-20, 도메인 기준 채택, 변경 금지)

[역할]
    4월 3종 비교(선형 vs Rule-based vs 하이브리드)의 기준선(Baseline)
"""

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ──────────────────────────────────────────────
# 0. 파일 경로 설정 (필요 시 수정)
# ──────────────────────────────────────────────

PATH_PAIRS  = "final_submission_recipe_similarity_pairs_20260317.csv"
PATH_SMALL  = "소규모_레시피_DB_남유찬_v0_10.xlsx"
PATH_LARGE  = "대규모_레시피_DB_남유찬_v0_08.xlsx"
PATH_OUTPUT = "baseline_result.csv"

SHEET_NAME    = "대용량_레시피_입력템플릿"
N_INGREDIENTS = 28  # 재료 컬럼 최대 개수

# FR-04 v1.3 사전 임계값 (EDA 착수 전 선언, 데이터 확인 후 변경 금지)
# ADR 확정 임계값 (2026-03-20): ratio ∈ [0.2, 3.0] — 도메인 기준 채택, 변경 금지
RATIO_MIN = 0.2
RATIO_MAX = 3.0


# ──────────────────────────────────────────────
# 1. 데이터 로딩
# ──────────────────────────────────────────────

def load_pairs(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df[df["is_best_per_large_recommendation"] == True].copy()
    print(f"[pairs] 로딩 완료: {len(df)}쌍")
    return df


def load_recipe_db(path: str, sheet: str) -> pd.DataFrame:
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
    small = df_small_melted[df_small_melted["serving_size"] == 1].copy()
    small = small.rename(columns={"amount_g": "base_amount_g"})

    large = df_large_melted.copy()
    large = large.rename(columns={
        "amount_g":     "target_amount_g",
        "serving_size": "target_serving_size",
    })

    merged = df_pairs[[
        "pair_id", "group_type",
        "small_recipe_id", "large_recipe_id", "composite_score"
    ]].copy()

    merged = merged.merge(
        small[["recipe_id", "ingredient_name", "base_amount_g", "role"]],
        left_on="small_recipe_id",
        right_on="recipe_id",
        how="inner",
    ).drop(columns=["recipe_id"])

    merged = merged.merge(
        large[["recipe_id", "ingredient_name", "target_amount_g", "target_serving_size"]],
        left_on=["large_recipe_id", "ingredient_name"],
        right_on=["recipe_id", "ingredient_name"],
        how="inner",
    ).drop(columns=["recipe_id"])

    merged["per_serving_grams"] = (
        merged["target_amount_g"] / merged["target_serving_size"]
    )

    print(f"[join] 조인 완료: {len(merged)}개 재료 행, {merged['pair_id'].nunique()}쌍")
    return merged


# ──────────────────────────────────────────────
# 4. 비정상 계산 방지 필터 (FR-04 v1.3)
# ──────────────────────────────────────────────

def apply_validity_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    비정상적 계산을 유발하는 행을 분석에서 제외한다.
    DB 레코드는 보존 (FR-04 v1.3 처리 정책).

    필터 조건:
        1) base_amount_g <= 0       : 분모 0 또는 음수 → MAPE 무한대 / 예측값 왜곡
        2) target_serving_size <= 0 : 인원수 비정상 → 예측값 음수 또는 0
        3) ratio < RATIO_MIN 또는 ratio > RATIO_MAX : FR-04 v1.3 사전 임계값
    """
    df = df.copy()
    df["ratio_actual"] = df["target_amount_g"] / (
        df["base_amount_g"] * df["target_serving_size"]
    )

    cond_invalid = (
        (df["base_amount_g"] <= 0) |
        (df["target_serving_size"] <= 0) |
        (df["ratio_actual"] < RATIO_MIN) |
        (df["ratio_actual"] > RATIO_MAX)
    )

    df_valid = df[~cond_invalid].copy()

    print(f"\n[필터] 비정상 행 제외 결과 (FR-04 v1.3)")
    print(f"  전체 행:   {len(df):>4}개")
    print(f"  제외 행:   {cond_invalid.sum():>4}개  ({cond_invalid.mean()*100:.1f}%)")
    print(f"  분석 사용: {len(df_valid):>4}개")

    return df_valid


# ──────────────────────────────────────────────
# 5. Baseline 예측 — ADR-001 Option A
# ──────────────────────────────────────────────

def baseline_predict(df: pd.DataFrame) -> pd.DataFrame:
    """
    Baseline 선형 예측 — ADR-001 Option A
        Y_predicted  = base_amount_g × target_serving_size
        ratio_baseline = 1.0 (b=1 동치, 완전 선형)
    """
    df = df.copy()
    df["predicted_amount_g"] = df["base_amount_g"] * df["target_serving_size"]
    df["ratio_baseline"]     = 1.0
    return df


# ──────────────────────────────────────────────
# 6. 오차 분석 프레임워크
# ──────────────────────────────────────────────

def calc_mae(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def calc_mape(actual: pd.Series, predicted: pd.Series) -> float:
    """actual=0인 행 제외"""
    mask = actual != 0
    return float(np.mean(np.abs((actual[mask] - predicted[mask]) / actual[mask])) * 100)


def calc_rmse(actual: pd.Series, predicted: pd.Series) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def analyze_errors(df: pd.DataFrame, label: str = "전체") -> dict:
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

    results.append(analyze_errors(df, label="전체"))

    for g, gdf in df.groupby("group_type"):
        results.append(analyze_errors(gdf, label=f"group_type={g}"))

    for r, rdf in df.groupby("role"):
        results.append(analyze_errors(rdf, label=f"role={r}"))

    return pd.DataFrame(results)


# ──────────────────────────────────────────────
# 7. 시각화
# ──────────────────────────────────────────────

def _setup_font():
    """한글 폰트 설정 (없으면 영어 fallback)"""
    korean_fonts = ["NanumGothic", "Malgun Gothic", "AppleGothic", "Nanum Gothic"]
    available = {f.name for f in fm.fontManager.ttflist}
    for font in korean_fonts:
        if font in available:
            plt.rcParams["font.family"] = font
            return
    plt.rcParams["font.family"] = "DejaVu Sans"


def plot_mape_by_group(df: pd.DataFrame, path: str = "viz_mape_by_group.png"):
    """시각화 2 — group_type × role별 MAPE 막대 그래프"""
    _setup_font()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Baseline MAPE(%) by Group", fontsize=13, fontweight="bold")

    actual    = df["target_amount_g"]
    predicted = df["predicted_amount_g"]

    # group_type별
    gt_data = {
        g: calc_mape(gdf["target_amount_g"], gdf["predicted_amount_g"])
        for g, gdf in df.groupby("group_type")
    }
    axes[0].bar(gt_data.keys(), gt_data.values(), color="#3498db", alpha=0.85, edgecolor="white")
    axes[0].axhline(25, color="gray", linestyle="--", linewidth=1, label="PRD 목표 (~25%)")
    axes[0].set_title("group_type별 MAPE")
    axes[0].set_ylabel("MAPE (%)")
    axes[0].legend(fontsize=8)
    for i, (k, v) in enumerate(gt_data.items()):
        axes[0].text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=9)

    # role별
    role_data = {
        r: calc_mape(rdf["target_amount_g"], rdf["predicted_amount_g"])
        for r, rdf in df.groupby("role")
    }
    axes[1].bar(role_data.keys(), role_data.values(), color="#2ecc71", alpha=0.85, edgecolor="white")
    axes[1].axhline(25, color="gray", linestyle="--", linewidth=1, label="PRD 목표 (~25%)")
    axes[1].set_title("role별 MAPE")
    axes[1].set_ylabel("MAPE (%)")
    axes[1].legend(fontsize=8)
    axes[1].tick_params(axis="x", rotation=15)
    for i, (k, v) in enumerate(role_data.items()):
        axes[1].text(i, v + 0.5, f"{v:.1f}%", ha="center", fontsize=9)

    plt.tight_layout()
    plt.savefig(path, dpi=150, bbox_inches="tight")
    plt.close()
    print(f"[시각화] {path} 저장 완료")


# ──────────────────────────────────────────────
# 8. 메인 실행
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

    # 4. 비정상 계산 방지 필터 (FR-04 v1.3)
    df = apply_validity_filters(df)

    # 5. Baseline 예측
    df = baseline_predict(df)

    # 6. 오차 분석
    print("\n" + "=" * 50)
    print("오차 분석 결과 (Baseline 선형 모델 — ADR-001)")
    print("=" * 50)
    error_df = run_error_analysis(df)
    print(error_df.to_string(index=False))

    # 7. 결과 저장
    df.to_csv(PATH_OUTPUT, index=False, encoding="utf-8-sig")
    error_df.to_csv("baseline_error_analysis.csv", index=False, encoding="utf-8-sig")
    print(f"\n[완료] baseline_result.csv, baseline_error_analysis.csv 저장 완료")

    # 8. 시각화
    print("\n[시각화 생성 중...]")
    plot_mape_by_group(df)
    print("[완료] 시각화 생성 완료")


if __name__ == "__main__":
    main()