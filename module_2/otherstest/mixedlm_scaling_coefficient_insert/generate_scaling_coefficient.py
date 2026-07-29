import os
from datetime import datetime
import pandas as pd

# =========================================
# scaling_coefficient 산출물 자동 생성 스크립트
# 기준 스키마: optimized_schema_v4_3_20260316.sql
# 생성 산출물:
#   1) scaling_coefficient_v1.csv
#   2) insert_scaling_coefficient.sql
#
# [수정 배경]
# - 채택 모델: B_simple MixedLM
# - b는 전역 단일값 0.6163 사용
# - a는 카테고리별 intercept 사용
# - 기존 버그: A_GLOBAL=7.9677 + 카테고리별 b(interaction) 혼합 사용
# =========================================

# -----------------------------
# 1. 기본 설정
# -----------------------------
OUTPUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs")
os.makedirs(OUTPUT_DIR, exist_ok=True)

CSV_PATH = os.path.join(OUTPUT_DIR, "scaling_coefficient_v1.csv")
SQL_PATH = os.path.join(OUTPUT_DIR, "insert_scaling_coefficient.sql")

# TIMESTAMP 형식
CREATED_AT = "2026-03-28 00:00:00"

# 최신 스키마 v4.3 기준 group_type 값
GROUP_TYPES = ["dry_heat", "moist_heat", "no_heat"]

# -----------------------------
# 2. 채택 모델(B_simple) 파라미터
# -----------------------------
# B_simple 고정효과 계수:
# Intercept = 2.0754
# seasoning(vs main) = -0.2344
# sub(vs main) = -0.3262
# log_N = -0.3837  → b = 1 - 0.3837 = 0.6163
#
# 카테고리별 a:
# - 주재료(main): exp(2.0754) = 7.9677
# - 양념류(seasoning): exp(2.0754 - 0.2344) = 6.3028
# - 부재료(sub): exp(2.0754 - 0.3262) = 5.7500
# - 수분류: sub 계열 fallback
# - 유지류: seasoning 계열 fallback

B_SIMPLE_B = 0.6163
B_SIMPLE_SE_B = 0.1814
B_SIMPLE_P_VALUE = 0.034000  # statsmodels Wald 검정: H0: b=1 (선형 스케일링) 기각 여부
                               # z = |b-1| / SE = 0.3837 / 0.1814 = 2.116 → p ≈ 0.034 (검증 완료)
                               # [cf.] z=b/SE=3.40(p≈0.0007)은 H0: b=0 검정값으로 의미 다름
B_SIMPLE_SAMPLE_SIZE = 607

CATEGORY_PARAMS = {
    "주재료": {
        "a": 7.9677,
        "b": B_SIMPLE_B,
        "se_b": B_SIMPLE_SE_B,
        "p_value": B_SIMPLE_P_VALUE,
        "ci_low": 0.2600,
        "ci_high": 0.9700,
        "sample_size": B_SIMPLE_SAMPLE_SIZE,
        "r_squared": None,       # MixedLM pseudo R² 미산정 — 원본 결과 파일 확인 후 채울 것
        "estimation_method": "mixedlm",
        "notes": None,
    },
    "부재료": {
        "a": 5.7500,
        "b": B_SIMPLE_B,
        "se_b": B_SIMPLE_SE_B,
        "p_value": B_SIMPLE_P_VALUE,
        "ci_low": 0.2600,
        "ci_high": 0.9700,
        "sample_size": B_SIMPLE_SAMPLE_SIZE,
        "r_squared": None,
        "estimation_method": "mixedlm",
        "notes": None,
    },
    "양념류": {
        "a": 6.3028,
        "b": B_SIMPLE_B,
        "se_b": B_SIMPLE_SE_B,
        "p_value": B_SIMPLE_P_VALUE,
        "ci_low": 0.2600,
        "ci_high": 0.9700,
        "sample_size": B_SIMPLE_SAMPLE_SIZE,
        "r_squared": None,
        "estimation_method": "mixedlm",
        "notes": None,
    },
    "수분류": {
        "a": 5.7500,   # 부재료(sub) 계열 fallback — 독립 통계 도출 아님
        "b": B_SIMPLE_B,
        "se_b": B_SIMPLE_SE_B,
        "p_value": B_SIMPLE_P_VALUE,
        "ci_low": 0.2600,
        "ci_high": 0.9700,
        "sample_size": None,     # category_mean fallback: 셀 단위 n 미산정 → NULL
        "r_squared": None,
        "estimation_method": "category_mean",   # ADR-002 v3: fallback은 category_mean
        "notes": "부재료(sub) 계열 a값 fallback 적용. 독립 통계 도출 아님.",
    },
    "유지류": {
        "a": 6.3028,   # 양념류(seasoning) 계열 fallback — 독립 통계 도출 아님
        "b": B_SIMPLE_B,
        "se_b": B_SIMPLE_SE_B,
        "p_value": B_SIMPLE_P_VALUE,
        "ci_low": 0.2600,
        "ci_high": 0.9700,
        "sample_size": None,     # category_mean fallback: 셀 단위 n 미산정 → NULL
        "r_squared": None,
        "estimation_method": "category_mean",   # ADR-002 v3: fallback은 category_mean
        "notes": "양념류(seasoning) 계열 a값 fallback 적용. 독립 통계 도출 아님.",
    },
}

# -----------------------------
# 3. 데이터프레임 생성
# -----------------------------
def build_scaling_coefficient_df() -> pd.DataFrame:
    rows = []

    for ingredient_category, vals in CATEGORY_PARAMS.items():
        for group_type in GROUP_TYPES:
            rows.append(
                {
                    "ingredient_category": ingredient_category,
                    "group_type": group_type,
                    "power_law_a": vals["a"],
                    "power_law_b": vals["b"],
                    "scaling_exponent": vals["b"],  # 하위호환 컬럼
                    "r_squared": vals["r_squared"],
                    "se_b": vals["se_b"],
                    "p_value": vals["p_value"],
                    "confidence_interval_lower": vals["ci_low"],
                    "confidence_interval_upper": vals["ci_high"],
                    "sample_size": vals["sample_size"],
                    "estimation_method": vals["estimation_method"],
                    "notes": vals["notes"],
                    "version": 1,
                    "source": "통계분석",
                    "is_active": True,
                    "created_at": CREATED_AT,
                }
            )

    return pd.DataFrame(rows)


# -----------------------------
# 4. CSV 저장
# -----------------------------
def save_csv(df: pd.DataFrame, path: str) -> None:
    df.to_csv(path, index=False, encoding="utf-8-sig")


# -----------------------------
# 5. SQL INSERT 생성
# -----------------------------
def bool_to_sql(value: bool) -> str:
    return "TRUE" if value else "FALSE"


def escape_sql_string(value: str) -> str:
    return value.replace("'", "''")


def to_sql_value(value) -> str:
    """Python None / pandas NaN을 SQL NULL로, 문자열은 따옴표 처리하여 반환."""
    if value is None:
        return "NULL"
    if isinstance(value, float) and pd.isna(value):
        return "NULL"
    if isinstance(value, str):
        return f"'{escape_sql_string(value)}'"
    return str(value)


def generate_insert_sql(df: pd.DataFrame) -> str:
    lines = []
    lines.append("-- scaling_coefficient INSERT")
    lines.append(f"-- 생성일: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append("-- 기준 스키마: optimized_schema_v4_3_20260316.sql")
    lines.append("-- 채택 모델: B_simple MixedLM")
    lines.append("-- group_type: dry_heat(건열) / moist_heat(습열) / no_heat(비가열)")
    lines.append("-- power_law_b = 0.6163 (전역 단일값)")
    lines.append("-- power_law_a = 카테고리별 intercept (수분류·유지류는 category_mean fallback)")
    lines.append("-- scaling_exponent = power_law_b (하위호환 컬럼, 동일값)")
    lines.append("-- p_value=0.034: statsmodels Wald z=|(b-1)/SE|=0.3837/0.1814=2.116 → p≈0.034 (검증 완료)")
    lines.append("-- H0: b=1(선형 스케일링) 기각. p<0.05로 비선형 스케일링 유의.")
    lines.append("")
    lines.append("INSERT INTO scaling_coefficient")
    lines.append("  (ingredient_category, group_type, power_law_a, power_law_b, scaling_exponent,")
    lines.append("   r_squared, se_b, p_value, confidence_interval_lower, confidence_interval_upper,")
    lines.append("   sample_size, estimation_method, notes, version, source, is_active, created_at)")
    lines.append("VALUES")

    value_lines = []
    for _, row in df.iterrows():
        value_lines.append(
            "  ("
            f"'{escape_sql_string(row['ingredient_category'])}', "
            f"'{escape_sql_string(row['group_type'])}', "
            f"{row['power_law_a']:.4f}, "
            f"{row['power_law_b']:.4f}, "
            f"{row['scaling_exponent']:.4f}, "
            f"{to_sql_value(row['r_squared'])}, "
            f"{row['se_b']:.4f}, "
            f"{row['p_value']:.6f}, "
            f"{row['confidence_interval_lower']:.4f}, "
            f"{row['confidence_interval_upper']:.4f}, "
            f"{to_sql_value(row['sample_size'])}, "
            f"'{escape_sql_string(row['estimation_method'])}', "
            f"{to_sql_value(row['notes'])}, "
            f"{int(row['version'])}, "
            f"'{escape_sql_string(row['source'])}', "
            f"{bool_to_sql(bool(row['is_active']))}, "
            f"'{escape_sql_string(row['created_at'])}'"
            ")"
        )

    lines.append(",\n".join(value_lines) + ";")
    return "\n".join(lines)


def save_sql(sql_text: str, path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(sql_text)


# -----------------------------
# 6. 검증
# -----------------------------
def validate_df(df: pd.DataFrame) -> None:
    required_columns = [
        "ingredient_category",
        "group_type",
        "power_law_a",
        "power_law_b",
        "scaling_exponent",
        "r_squared",
        "se_b",
        "p_value",
        "confidence_interval_lower",
        "confidence_interval_upper",
        "sample_size",
        "estimation_method",
        "notes",
        "version",
        "source",
        "is_active",
        "created_at",
    ]

    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"필수 컬럼 누락: {missing}")

    if len(df) != 15:
        raise ValueError(f"레코드 수 오류: 예상 15행, 실제 {len(df)}행")

    if not (df["power_law_b"] == df["scaling_exponent"]).all():
        raise ValueError("power_law_b와 scaling_exponent 값이 일치하지 않습니다.")

    if df["group_type"].nunique() != 3:
        raise ValueError("group_type 개수가 3개가 아닙니다.")

    if df["ingredient_category"].nunique() != 5:
        raise ValueError("ingredient_category 개수가 5개가 아닙니다.")

    expected_b = {round(B_SIMPLE_B, 4)}
    actual_b = set(df["power_law_b"].round(4).unique())
    if actual_b != expected_b:
        raise ValueError(
            f"power_law_b 오류: 예상 {expected_b}, 실제 {actual_b}"
        )

    expected_a = {
        "주재료": 7.9677,
        "부재료": 5.7500,
        "양념류": 6.3028,
        "수분류": 5.7500,
        "유지류": 6.3028,
    }
    actual_a = (
        df.groupby("ingredient_category")["power_law_a"]
        .first()
        .round(4)
        .to_dict()
    )

    if actual_a != expected_a:
        raise ValueError(
            f"카테고리별 power_law_a 오류:\n예상={expected_a}\n실제={actual_a}"
        )

    # estimation_method: 통계 도출 vs fallback 구분 검증 [ADR-002 v3]
    expected_estimation = {
        "주재료": "mixedlm",
        "부재료": "mixedlm",
        "양념류": "mixedlm",
        "수분류": "category_mean",
        "유지류": "category_mean",
    }
    actual_estimation = (
        df.groupby("ingredient_category")["estimation_method"]
        .first()
        .to_dict()
    )
    if actual_estimation != expected_estimation:
        raise ValueError(
            f"estimation_method 오류:\n예상={expected_estimation}\n실제={actual_estimation}"
        )

    # category_mean fallback 행의 sample_size는 NULL이어야 함
    fallback_mask = df["estimation_method"] == "category_mean"
    if df.loc[fallback_mask, "sample_size"].notna().any():
        raise ValueError(
            "category_mean 행(수분류·유지류)의 sample_size는 NULL이어야 합니다."
        )


# -----------------------------
# 7. 실행
# -----------------------------
def main() -> None:
    df = build_scaling_coefficient_df()
    validate_df(df)

    save_csv(df, CSV_PATH)
    sql_text = generate_insert_sql(df)
    save_sql(sql_text, SQL_PATH)

    print("✅ scaling_coefficient 산출물 생성 완료")
    print(f" - CSV: {CSV_PATH}")
    print(f" - SQL: {SQL_PATH}")
    print(f" - 총 레코드 수: {len(df)}")

    print("\n[카테고리별 a 값]")
    print(df.groupby("ingredient_category")["power_law_a"].first())

    print("\n[고유 b 값]")
    print(df["power_law_b"].unique())

    print("\n[미리보기]")
    print(df.head())


if __name__ == "__main__":
    main()