"""
baseline_model.py
==================
OptiMeal 프로젝트 — Baseline 선형 모델 코드 + 오차 분석 프레임워크
담당: 박미연
기준 문서: ADR-001 (멱함수 독립변수 정의), FR-04 v1.3 (ratio 이상치 사전 임계값)

[Baseline 수식 — ADR-001 Option A]
    ratio = Y / (base_amount × N) = 1 고정 (완전 선형)
    Y_predicted = base_amount_g × target_serving_size
    b = 1 동치

[데이터 소스]
    ml_training_dataset 테이블에서 직접 읽기 (recipe, ingredient JOIN)
    DB 연결: 환경변수(POSTGRES_*) 또는 .env 파일 참고

[비정상 계산 방지 기준 — FR-04 v1.3]
    1) base_amount_g <= 0        → MAPE 무한대 / 예측값 왜곡 방지
    2) target_serving_size <= 0  → 예측값 음수 또는 0 방지
    3) ratio < 0.2 또는 > 3.0   → ADR 확정 임계값 (2026-03-20, 도메인 기준 채택, 변경 금지)

[역할]
    4월 3종 비교(선형 vs Rule-based vs 하이브리드)의 기준선(Baseline)
"""

import os
import warnings

import pandas as pd
import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# python-dotenv가 설치되어 있으면 .env 자동 로드
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import psycopg2


# ──────────────────────────────────────────────
# 0. 상수
# ──────────────────────────────────────────────

PATH_OUTPUT = "baseline_result.csv"

# FR-04 v1.3 사전 임계값 (EDA 착수 전 선언, 데이터 확인 후 변경 금지)
# ADR 확정 임계값 (2026-03-20): ratio ∈ [0.2, 3.0] — 도메인 기준 채택, 변경 금지
RATIO_MIN = 0.2
RATIO_MAX = 3.0


# ──────────────────────────────────────────────
# 1. DB 연결 헬퍼
# ──────────────────────────────────────────────

def _get_db_conn():
    """
    환경변수 기반 PostgreSQL 연결 생성.

    환경변수 (python-dotenv 또는 OS 환경):
        POSTGRES_HOST     (기본: localhost)
        POSTGRES_PORT     (기본: 5432)
        POSTGRES_DB       (기본: optimeal)
        POSTGRES_USER     (기본: optimeal)
        POSTGRES_PASSWORD (기본: "")
    """
    return psycopg2.connect(
        host=os.getenv("POSTGRES_HOST", "localhost"),
        port=int(os.getenv("POSTGRES_PORT", 5432)),
        dbname=os.getenv("POSTGRES_DB", "optimeal"),
        user=os.getenv("POSTGRES_USER", "optimeal"),
        password=os.getenv("POSTGRES_PASSWORD", ""),
    )


# ──────────────────────────────────────────────
# 2. 데이터 로딩 — DB에서 직접 읽기
# ──────────────────────────────────────────────

# ml_training_dataset + recipe + ingredient 조인 쿼리
_SQL_LOAD = """
    SELECT
        mlt.base_recipe_id                              AS small_recipe_id,
        mlt.target_recipe_id                            AS large_recipe_id,
        mlt.base_serving_size,
        mlt.target_serving_size,
        mlt.base_amount_g,
        mlt.target_amount_g,
        -- ratio: DB 저장값 우선, NULL이면 amount에서 재계산
        COALESCE(
            mlt.ratio,
            mlt.target_amount_g / NULLIF(mlt.base_amount_g * mlt.target_serving_size, 0)
        )                                               AS ratio_actual,
        -- ingredient_role → role (오차 분석 그룹 키)
        mlt.ingredient_role                             AS role,
        mlt.ingredient_category,
        mlt.group_type,
        r_base.recipe_name                              AS small_recipe_name,
        r_large.recipe_name                             AS large_recipe_name,
        i.ingredient_name,
        -- 1인분당 대규모 분량 (참조용)
        mlt.target_amount_g / NULLIF(mlt.target_serving_size, 0) AS per_serving_grams
    FROM ml_training_dataset mlt
    JOIN recipe r_base  ON mlt.base_recipe_id   = r_base.recipe_id
    JOIN recipe r_large ON mlt.target_recipe_id  = r_large.recipe_id
    JOIN ingredient i   ON mlt.ingredient_id    = i.ingredient_id
    WHERE mlt.data_quality = '검증완료'
      AND mlt.is_training_set = TRUE
"""


def load_from_db() -> pd.DataFrame:
    """
    ml_training_dataset 테이블에서 Baseline 분석 데이터를 로드한다.

    recipe, ingredient 테이블과 JOIN하여 이름 정보를 포함한다.
    DB 적재 전 실행 시 빈 DataFrame이 반환된다 (오류 없음).

    Returns:
        분석에 필요한 컬럼이 포함된 DataFrame.
        주요 컬럼: small_recipe_id, large_recipe_id, group_type,
                   base_amount_g, target_amount_g, target_serving_size,
                   role, ingredient_name, ratio_actual, per_serving_grams

    Raises:
        psycopg2.OperationalError: DB 연결 실패 시
    """
    conn = _get_db_conn()
    try:
        df = pd.read_sql(_SQL_LOAD, conn)
    finally:
        conn.close()

    pair_count = df[["small_recipe_id", "large_recipe_id"]].drop_duplicates().shape[0]
    print(f"[DB] 로드 완료: {len(df)}개 재료 행, {pair_count}쌍")
    return df


# ──────────────────────────────────────────────
# 3. 비정상 계산 방지 필터 (FR-04 v1.3)
# ──────────────────────────────────────────────

def apply_validity_filters(df: pd.DataFrame) -> pd.DataFrame:
    """
    비정상적 계산을 유발하는 행을 분석에서 제외한다.
    DB 레코드는 보존 (FR-04 v1.3 처리 정책).

    필터 조건:
        1) base_amount_g <= 0       : 분모 0 또는 음수 → MAPE 무한대 / 예측값 왜곡
        2) target_serving_size <= 0 : 인원수 비정상 → 예측값 음수 또는 0
        3) ratio < RATIO_MIN 또는 ratio > RATIO_MAX : FR-04 v1.3 사전 임계값
           RATIO_MIN=0.2, RATIO_MAX=3.0 — ADR 확정 임계값 (변경 금지)
    """
    df = df.copy()

    # ratio_actual이 DB에서 이미 왔으면 재계산 없이 그대로 사용
    if "ratio_actual" not in df.columns:
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
# 4. Baseline 예측 — ADR-001 Option A
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
    오차 분석 프레임워크.
    Rule-based, 하이브리드 코드에서도 동일 함수 재사용 가능 (3종 비교 기준).

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
# 6. 시각화
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
    """시각화 — group_type × role별 MAPE 막대 그래프"""
    _setup_font()
    fig, axes = plt.subplots(1, 2, figsize=(13, 5))
    fig.suptitle("Baseline MAPE(%) by Group", fontsize=13, fontweight="bold")

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
# 7. 메인 실행
# ──────────────────────────────────────────────

def main():
    print("=" * 50)
    print("Baseline 선형 모델 실행 (ADR-001 기준)")
    print("=" * 50)

    # 1. DB에서 데이터 로드
    df = load_from_db()

    if df.empty:
        print("[경고] ml_training_dataset이 비어 있습니다. DB 적재 후 실행하세요.")
        return

    # 2. 비정상 계산 방지 필터 (FR-04 v1.3)
    df = apply_validity_filters(df)

    # 3. Baseline 예측
    df = baseline_predict(df)

    # 4. 오차 분석
    print("\n" + "=" * 50)
    print("오차 분석 결과 (Baseline 선형 모델 — ADR-001)")
    print("=" * 50)
    error_df = run_error_analysis(df)
    print(error_df.to_string(index=False))

    # 5. 결과 저장
    df.to_csv(PATH_OUTPUT, index=False, encoding="utf-8-sig")
    error_df.to_csv("baseline_error_analysis.csv", index=False, encoding="utf-8-sig")
    print(f"\n[완료] {PATH_OUTPUT}, baseline_error_analysis.csv 저장 완료")

    # 6. 시각화
    print("\n[시각화 생성 중...]")
    plot_mape_by_group(df)
    print("[완료] 시각화 생성 완료")


if __name__ == "__main__":
    main()
