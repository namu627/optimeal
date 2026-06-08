"""
AIC 기반 비선형성 검증 스크립트
==============================
목적: b=1 고정(선형) vs b 추정(비선형) 모델의 AIC 직접 비교
     → "비선형성이 데이터에 통계적으로 지지되는가?" 검증

모델 A (선형, b=1 고정):
    log(ratio) ~ C(role),         groups=group_type  [ML]
    log_N 항 없음 → b=1 암묵적 고정

모델 B (비선형, b 추정):
    log(ratio) ~ log_N + C(role), groups=group_type  [ML]
    log_N 계수 = b-1 → b 자유 추정

주의: AIC 모델 간 비교는 반드시 ML (reml=False) 사용.
     REML AIC는 고정효과가 다른 모델 간 비교에 사용 불가.

결과 파일: aic_linearity_test_result.md (스크립트와 같은 폴더)
실행: python module_2/otherstest/aic_linearity_test.py
"""

import sys
from pathlib import Path
import warnings

import numpy as np
import pandas as pd
import statsmodels.formula.api as smf
from scipy import stats

warnings.filterwarnings("ignore")

# ── 경로 설정 ──────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent.parent
DF_B_PATH = PROJECT_ROOT / "module_2" / "df_B.csv"
RESULT_PATH = SCRIPT_DIR / "aic_linearity_test_result.md"


def load_data() -> pd.DataFrame:
    """df_B.csv 로드 및 기본 검증."""
    if not DF_B_PATH.exists():
        sys.exit(f"[오류] df_B.csv를 찾을 수 없음: {DF_B_PATH}")
    df = pd.read_csv(DF_B_PATH)
    required = {"log_ratio", "log_N", "role", "group_type"}
    missing = required - set(df.columns)
    if missing:
        sys.exit(f"[오류] 필수 컬럼 없음: {missing}")
    print(f"데이터 로드 완료: n={len(df)}, group_type={df['group_type'].unique().tolist()}")
    return df


def fit_model(formula: str, df: pd.DataFrame, label: str) -> tuple:
    """
    MixedLM 모델 적합 (ML, groups=group_type).

    Returns:
        result: 적합 결과 객체
        aic: AIC 값
        loglik: 로그우도 값
    """
    model = smf.mixedlm(formula, data=df, groups=df["group_type"])

    # ML 적합 (AIC 모델 간 비교 필수)
    try:
        result = model.fit(reml=False, method="lbfgs")
        print(f"[{label}] 수렴 성공 (ML, lbfgs)")
    except Exception as e:
        print(f"[{label}] lbfgs 실패: {e} → bfgs 재시도")
        try:
            result = model.fit(reml=False, method="bfgs")
            print(f"[{label}] 수렴 성공 (ML, bfgs)")
        except Exception as e2:
            sys.exit(f"[{label}] 수렴 실패: {e2}")

    aic = result.aic
    loglik = result.llf
    return result, aic, loglik


def run_lrt(loglik_a: float, loglik_b: float) -> tuple[float, float]:
    """
    Likelihood Ratio Test.
    H0: 모델 A (b=1 고정)가 충분, H1: 모델 B (b 추정)가 더 적합
    df=1 (log_N 계수 1개 추가)
    """
    lrt_stat = 2 * (loglik_b - loglik_a)
    p_value = stats.chi2.sf(lrt_stat, df=1)
    return lrt_stat, p_value


def build_report(
    result_a, aic_a, loglik_a,
    result_b, aic_b, loglik_b,
    lrt_stat, p_value, n
) -> str:
    """결과 마크다운 리포트 작성."""
    b_est = 1 + result_b.params["log_N"]
    b_se = result_b.bse["log_N"]
    delta_aic = aic_b - aic_a

    interpretation = (
        "비선형성 통계적으로 지지됨 (p < 0.05)"
        if p_value < 0.05
        else "비선형성 통계적으로 유의하지 않음 (p ≥ 0.05)"
    )

    report = f"""# AIC 기반 비선형성 검증 결과

**실행일**: 2026-05-13
**데이터**: df_B.csv (n={n})
**비교**: 모델 A (b=1 고정, 선형) vs 모델 B (b 추정, 비선형)
**적합 방법**: ML (reml=False) — AIC 모델 간 비교를 위한 필수 조건

---

## 모델 정의

| 모델 | 수식 | 의미 |
|------|------|------|
| **A (선형)** | `log(ratio) ~ C(role)` | log_N 항 없음, b=1 고정 (선형 스케일링) |
| **B (비선형)** | `log(ratio) ~ log_N + C(role)` | b 자유 추정 |

두 모델 모두 `groups=group_type` (3대분류: 건열/습열/비가열) 랜덤효과 포함.

---

## AIC 비교 결과

| 모델 | AIC | 로그우도 |
|------|-----|---------|
| A (b=1 고정, 선형) | {aic_a:.4f} | {loglik_a:.4f} |
| B (b 추정, 비선형) | {aic_b:.4f} | {loglik_b:.4f} |
| **ΔAIC = AIC(B) - AIC(A)** | **{delta_aic:.4f}** | — |

> ΔAIC < 0이면 모델 B(비선형)가 더 적합. 관례적으로 |ΔAIC| > 2 이면 유의미한 차이로 판단.

---

## Likelihood Ratio Test (LRT)

| 항목 | 값 |
|------|-----|
| LRT 통계량 | {lrt_stat:.4f} |
| 자유도 | 1 (log_N 계수 1개) |
| p-value | {p_value:.6f} |
| **판정** | **{interpretation}** |

---

## 모델 B 추정 결과 (ML)

| 파라미터 | 값 |
|---------|-----|
| b (스케일링 지수) | {b_est:.4f} |
| SE(b) | {b_se:.4f} |
| 95% CI | [{b_est - 1.96*b_se:.4f}, {b_est + 1.96*b_se:.4f}] |

> 참고: 기존 REML 추정값 b=0.6163. ML과 REML은 목적함수가 달라 수치 차이 발생 가능.

---

## 해석

- **ΔAIC 해석**: AIC(B) - AIC(A) = {delta_aic:.4f}
  - ΔAIC < -2: 비선형 모델(B)이 선형 모델(A)보다 유의미하게 적합함
  - ΔAIC ≥ -2: 두 모델 간 실질적 차이 없음

- **LRT 해석**: log_N 항 추가가 모델 적합도를 통계적으로 유의하게 개선하는지 검증
  - p < 0.05 → 비선형성 통계적으로 지지됨
  - p ≥ 0.05 → 비선형성 통계적으로 유의하지 않음

---

## 선행 결과와의 관계

| 분석 | 비교 대상 | 결론 |
|------|----------|------|
| 기존 OLS vs MixedLM (ΔAIC=-67.13) | 고정효과 only vs 랜덤효과 포함 | 랜덤효과 정당성 확보 |
| **본 분석** | **b=1 고정 vs b 추정** | **비선형성 자체 검증** |

기존 AIC 비교는 랜덤효과 도입 정당성을 검증했으며,
본 분석은 "N에 따른 ratio 변화(비선형성)가 통계적으로 존재하는가"를 직접 검증한다.
"""
    return report


def main():
    df = load_data()
    n = len(df)

    print("\n── 모델 A 적합 (b=1 고정, log_N 항 없음) ──")
    result_a, aic_a, loglik_a = fit_model(
        "log_ratio ~ C(role)", df, "모델A"
    )

    print("\n── 모델 B 적합 (b 추정, log_N 항 포함) ──")
    result_b, aic_b, loglik_b = fit_model(
        "log_ratio ~ log_N + C(role)", df, "모델B"
    )

    lrt_stat, p_value = run_lrt(loglik_a, loglik_b)

    b_est = 1 + result_b.params["log_N"]
    delta_aic = aic_b - aic_a

    print(f"\n── 결과 요약 ──")
    print(f"AIC(A, 선형):   {aic_a:.4f}")
    print(f"AIC(B, 비선형): {aic_b:.4f}")
    print(f"ΔAIC:           {delta_aic:.4f}")
    print(f"LRT 통계량:     {lrt_stat:.4f}")
    print(f"p-value:        {p_value:.6f}")
    print(f"추정 b (ML):    {b_est:.4f}")

    report = build_report(
        result_a, aic_a, loglik_a,
        result_b, aic_b, loglik_b,
        lrt_stat, p_value, n
    )

    RESULT_PATH.write_text(report, encoding="utf-8")
    print(f"\n결과 저장 완료: {RESULT_PATH}")


if __name__ == "__main__":
    main()
