#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
EDA 메인 파이프라인 — OptiMeal 모듈 2 비선형 스케일링 엔진
작성일: 2026-03-20
태스크 문서: TASK_EDA_ratio_analysis_20260320.md

⚠️ 파일 버전 불일치 (태스크 명세 vs 실제):
  - 소규모 레시피: 명세=v0_08 → 실제 사용=v0_10 (최신 버전 채택)
  - 정규화 테이블: 명세=v4_1 → 실제 사용=v4_0 (최신 버전 채택)
  - matched_pairs: 명세에 is_manually_verified=True → 실제=False (268쌍 전체 사용, 사람 검수 필요)
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')  # 헤드리스 환경 (화면 없이 파일로 저장)
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
import seaborn as sns
import statsmodels.formula.api as smf
from statsmodels.stats.power import FTestAnovaPower

warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────────────────────────────────────
# CONFIG: 파일 경로 및 분석 파라미터 (하드코딩 금지 — 여기서 일괄 관리)
# ─────────────────────────────────────────────────────────────────────────────
_EDA_DIR  = os.path.dirname(os.path.abspath(__file__))   # eda/
_ROOT_DIR = os.path.dirname(_EDA_DIR)                     # EDAbeforeMixedLM/

CONFIG = {
    # 입력 파일
    'small_recipe_file'  : os.path.join(_ROOT_DIR, '소규모_레시피_DB_남유찬_v0_10.xlsx'),
    'large_recipe_file'  : os.path.join(_ROOT_DIR, '대규모_레시피_DB_남유찬_v0_08.xlsx'),
    'matched_pairs_file' : '/Users/leticiacavalcanti/Develop/OptiMeal/RecipeMatching/output/matched_pairs_20260320.csv',
    'norm_table_file'    : os.path.join(_ROOT_DIR, '재료명정규화테이블_v4_0_20260314.xlsx'),
    'recipe_sheet'       : '대용량_레시피_입력템플릿',
    'norm_sheet'         : 'Jaccard검증용_정규화맵',
    # 출력 디렉토리
    'data_dir'    : os.path.join(_EDA_DIR, 'data'),
    'reports_dir' : os.path.join(_EDA_DIR, 'reports'),
    'figures_dir' : os.path.join(_EDA_DIR, 'reports', 'figures'),
    # 재료 블록 최대 인덱스
    'n_ingredients': 28,
    # 이상치 기준 (ADR-001 준수 — 2026-03-20 사전 확정, 데이터 확인 후 변경 금지)
    'outlier_ratio_min': 0.2,
    'outlier_ratio_max': 3.0,
}

# ─────────────────────────────────────────────────────────────────────────────
# 수분류 / 유지류 확정 목록 (2026-03-20 사전 확정 — 임의 수정 금지)
# 탐색 용도로만 사용하며, 분류 기준 변경은 사람에게 위임
# ─────────────────────────────────────────────────────────────────────────────
WATER_BASE_LIST = [
    '물', '쌀뜨물',
    '육수', '닭육수', '멸치육수', '멸치다시마육수', '사골육수', '소고기육수',
    '우유', '두유', '두유(검은콩)', '생크림', '요구르트', '코코넛밀크',
]

OIL_FAT_LIST = [
    '식용유', '참기름', '들기름',
    '올리브유', '올리브유(엑스트라버진)',
    '버터', '무염버터', '저염버터', '마가린',
    '고추기름', '마늘기름', '허브버터',
    '콩기름', '현미유', '포도씨유', '해바라기유', '땅콩기름', '코코넛오일',
]

WATER_SUFFIX = ['육수', '물', '우유', '크림', '두유', '요구르트']
OIL_SUFFIX   = ['기름', '버터', '마가린']
# '유' suffix는 두유·올리브유 혼재로 단독 사용 금지 — 목록 매칭 우선


# ─────────────────────────────────────────────────────────────────────────────
# 유틸리티
# ─────────────────────────────────────────────────────────────────────────────
def setup_korean_font():
    """시스템에서 사용 가능한 한글 폰트를 자동 감지하여 설정"""
    korean_candidates = [
        'Nanum Gothic', 'NanumGothic', 'Apple SD Gothic Neo',
        'AppleGothic', 'Malgun Gothic', 'NanumBarunGothic',
    ]
    available = {f.name for f in fm.fontManager.ttflist}
    for font in korean_candidates:
        if font in available:
            matplotlib.rcParams['font.family'] = font
            matplotlib.rcParams['axes.unicode_minus'] = False
            print(f"  한글 폰트 설정: {font}")
            return font
    print("  경고: 한글 폰트를 찾을 수 없습니다. 한글이 깨질 수 있습니다.")
    return None


def create_directories():
    """출력 디렉토리 일괄 생성"""
    for d in [CONFIG['data_dir'], CONFIG['reports_dir'], CONFIG['figures_dir']]:
        os.makedirs(d, exist_ok=True)


def classify_ingredient(norm_name):
    """
    수분류/유지류 파생 분류 — 탐색 전용 (2026-03-20 사전 확정 기준)
    우선순위: 정확 매칭 > suffix 패턴
    결정은 사람에게 위임 — 이 함수는 탐색 목적으로만 사용
    """
    if norm_name in WATER_BASE_LIST:
        return 'water_base'
    if norm_name in OIL_FAT_LIST:
        return 'oil_fat'
    for suf in WATER_SUFFIX:
        if norm_name.endswith(suf):
            return 'water_base'
    for suf in OIL_SUFFIX:
        if norm_name.endswith(suf):
            return 'oil_fat'
    return None  # 미분류 — 결정 위임


# ─────────────────────────────────────────────────────────────────────────────
# TASK-01: 데이터 로딩 및 wide → long 변환
# ─────────────────────────────────────────────────────────────────────────────
def task01_load_and_melt():
    """
    소규모/대규모 레시피 xlsx를 로드하고 재료 블록(ingredient_1~28)을 long 포맷으로 변환.
    matched_pairs CSV와 조인하여 분석 대상 268쌍만 추출.

    출력: data/long_ingredients.parquet
    """
    print("\n[TASK-01] 데이터 로딩 및 wide→long 변환")

    # xlsx 로드 (행 1=영문 헤더, 행 2=한글 설명→스킵, 행 3~=실제 데이터)
    df_small = pd.read_excel(
        CONFIG['small_recipe_file'],
        sheet_name=CONFIG['recipe_sheet'],
        header=1, skiprows=[2],
    )
    df_large = pd.read_excel(
        CONFIG['large_recipe_file'],
        sheet_name=CONFIG['recipe_sheet'],
        header=1, skiprows=[2],
    )
    df_pairs = pd.read_csv(CONFIG['matched_pairs_file'])

    print(f"  소규모 레시피 총 행수: {len(df_small)}")
    print(f"  대규모 레시피 총 행수: {len(df_large)}")
    print(f"  매칭 쌍: {len(df_pairs)}쌍")

    # matched_pairs 기준으로 레시피 필터링
    small_ids = set(df_pairs['small_recipe_id'])
    large_ids = set(df_pairs['large_recipe_id'])
    df_small = df_small[df_small['recipe_id'].isin(small_ids)].copy()
    df_large = df_large[df_large['recipe_id'].isin(large_ids)].copy()
    print(f"  매칭 쌍에 포함된 소규모: {len(df_small)}행, 대규모: {len(df_large)}행")

    # role 정규화 매핑: DB 원본값 → 분석 카테고리
    role_map = {
        '주재료': 'main',
        '부재료': 'sub',
        '양념'  : 'seasoning',  # '양념'과 '조미료' 동일 카테고리로 통합 (ADR 확정)
        '조미료': 'seasoning',
    }

    def wide_to_long(df, recipe_type):
        """재료 블록(wide)을 long 포맷으로 변환"""
        records = []
        for _, row in df.iterrows():
            for n in range(1, CONFIG['n_ingredients'] + 1):
                name_col   = f'ingredient_{n}_name'
                amount_col = f'ingredient_{n}_amount'
                unit_col   = f'ingredient_{n}_unit'
                role_col   = f'ingredient_{n}_role'

                if name_col not in df.columns:
                    break  # 해당 인덱스 이후 컬럼 없음

                ing_name = row.get(name_col)
                if pd.isna(ing_name) or str(ing_name).strip() == '':
                    continue

                amount_raw = row.get(amount_col)
                if pd.isna(amount_raw):
                    continue  # amount=NULL인 재료 제외 (DISCRETIONARY/UNKNOWN — ADR-004)
                # '적당량' 등 비수치 문자열 제외 (DISCRETIONARY — ADR-004)
                try:
                    amount = float(amount_raw)
                except (ValueError, TypeError):
                    continue

                unit_raw = row.get(unit_col)
                unit = str(unit_raw).strip().lower() if not pd.isna(unit_raw) else ''

                role_raw = row.get(role_col)
                role_orig = str(role_raw).strip() if not pd.isna(role_raw) else ''
                role = role_map.get(role_orig, role_orig)  # 매핑 안 되면 원본 유지

                records.append({
                    'recipe_id'                  : row['recipe_id'],
                    'recipe_name'                : row.get('recipe_name'),
                    'recipe_type'                : recipe_type,
                    'serving_size'               : row.get('serving_size'),
                    'cooking_method_group_type'  : row.get('cooking_method_group_type'),
                    'cooking_method_primary'     : row.get('cooking_method_primary'),
                    'ingredient_name'            : str(ing_name).strip(),
                    'amount'                     : float(amount),
                    'unit'                       : unit,
                    'role'                       : role,
                    'ingredient_num'             : n,
                })
        return pd.DataFrame(records)

    df_small_long = wide_to_long(df_small, '소규모')
    df_large_long = wide_to_long(df_large, '대규모')
    df_long = pd.concat([df_small_long, df_large_long], ignore_index=True)

    # unit 이슈 보고
    unit_counts = df_long['unit'].value_counts()
    non_gml = unit_counts[~unit_counts.index.isin(['g', 'ml'])]
    print(f"\n  [unit 이슈 보고] g/ml 외 단위:")
    if len(non_gml) > 0:
        for u, cnt in non_gml.items():
            print(f"    '{u}': {cnt}건")
    else:
        print("    없음 (모든 재료 g 또는 ml)")

    # 출력
    out_path = os.path.join(CONFIG['data_dir'], 'long_ingredients.parquet')
    df_long.to_parquet(out_path, index=False)
    print(f"\n  저장: {out_path}")
    print(f"  long 포맷 총 행수: {len(df_long)} "
          f"(소규모: {len(df_small_long)}, 대규모: {len(df_large_long)})")

    return df_long, df_pairs


# ─────────────────────────────────────────────────────────────────────────────
# TASK-02: ratio 계산
# ─────────────────────────────────────────────────────────────────────────────
def task02_calculate_ratio(df_long, df_pairs):
    """
    매칭 쌍별·재료별 ratio = Y / (base × N) 산출 (ADR-001 Option A)

    Args:
        Y    : 대규모 레시피 재료 투입량 (g 또는 ml)
        base : 소규모 레시피 1인분 투입량 (소규모 serving_size ≠ 1이면 amount/serving_size)
        N    : 대규모 레시피 인원수 (serving_size)

    출력: data/ratio_table.parquet
    """
    print("\n[TASK-02] ratio 계산")

    # 정규화 맵 로드 (Jaccard검증용_정규화맵 시트, header=2)
    df_norm_raw = pd.read_excel(
        CONFIG['norm_table_file'],
        sheet_name=CONFIG['norm_sheet'],
        header=2,
    )
    orig_col = df_norm_raw.columns[0]  # '원본 표기 (synonym_name)'
    std_col  = df_norm_raw.columns[1]  # '표준명 (standard_name)'

    norm_map = {}
    for _, row in df_norm_raw.iterrows():
        k = str(row[orig_col]).strip().strip('"').strip("'")
        v = str(row[std_col]).strip()
        if k and k != 'nan' and v and v != 'nan':
            norm_map[k] = v
    print(f"  정규화 맵 로드: {len(norm_map)}건")

    def normalize_name(name):
        """재료명 정규화 — 정규화 맵 우선, 없으면 원본 반환"""
        name = str(name).strip()
        return norm_map.get(name, name)

    # 소규모/대규모 분리
    df_s = df_long[df_long['recipe_type'] == '소규모'].copy()
    df_l = df_long[df_long['recipe_type'] == '대규모'].copy()

    # 정규화 적용
    df_s['norm_name'] = df_s['ingredient_name'].apply(normalize_name)
    df_l['norm_name'] = df_l['ingredient_name'].apply(normalize_name)

    # 소규모 1인분 투입량 계산 (serving_size ≠ 1이면 나눔)
    df_s['serving_size_num'] = pd.to_numeric(df_s['serving_size'], errors='coerce').fillna(1).replace(0, np.nan)
    df_s['base_per_person']  = df_s['amount'] / df_s['serving_size_num']

    ratio_records = []
    n_excluded_zero       = 0
    n_excluded_unit_mismatch = 0
    n_excluded_non_gml    = 0

    for _, pair in df_pairs.iterrows():
        small_id   = pair['small_recipe_id']
        large_id   = pair['large_recipe_id']
        group_type = pair.get('group_type', '')

        s_ings = df_s[df_s['recipe_id'] == small_id]
        l_ings = df_l[df_l['recipe_id'] == large_id]

        if s_ings.empty or l_ings.empty:
            continue

        # N: 대규모 serving_size (레시피 단위로 단일값 — 첫 번째 행 기준)
        N_val = pd.to_numeric(l_ings.iloc[0]['serving_size'], errors='coerce')

        # 조리방법: 대규모 기준 (없으면 matched_pairs의 group_type)
        cooking_group = str(l_ings.iloc[0].get('cooking_method_group_type', '')).strip()
        if not cooking_group or cooking_group == 'nan':
            cooking_group = str(group_type).strip()
        cooking_method = str(l_ings.iloc[0].get('cooking_method_primary', '')).strip()

        # 정규화된 이름으로 소규모-대규모 재료 매칭
        # 동일 레시피 내 같은 이름이 여러 번 나올 경우 합산 (용도 분리 입력 케이스 처리)
        s_agg = df_s[df_s['recipe_id'] == small_id].groupby('norm_name').agg(
            base_per_person=('base_per_person', 'sum'),
            unit_s=('unit', 'first'),
            role=('role', 'first'),
        ).reset_index()
        l_agg = df_l[df_l['recipe_id'] == large_id].groupby('norm_name').agg(
            amount_l=('amount', 'sum'),
            unit_l=('unit', 'first'),
            role_l=('role', 'first'),
        ).reset_index()

        merged = pd.merge(s_agg, l_agg, on='norm_name')

        for _, m in merged.iterrows():
            base   = m['base_per_person']
            Y      = m['amount_l']
            unit_s = m['unit_s']
            unit_l = m['unit_l']

            # unit 불일치 제외 후 보고
            if unit_s != unit_l:
                n_excluded_unit_mismatch += 1
                continue

            # g 또는 ml 단위만 허용 (변환 불가 단위 제외)
            if unit_s not in ['g', 'ml']:
                n_excluded_non_gml += 1
                continue

            # base=0, Y=0, N=0 또는 결측 제외
            if (pd.isna(base) or base == 0 or
                    pd.isna(Y) or Y == 0 or
                    pd.isna(N_val) or N_val == 0):
                n_excluded_zero += 1
                continue

            ratio     = Y / (base * N_val)
            log_ratio = np.log(ratio)
            log_N     = np.log(N_val)

            # role: 대규모 기준, 없으면 소규모 기준
            role = m['role_l'] if (m['role_l'] and m['role_l'] not in ('', 'nan')) else m['role']

            # 수분류/유지류 파생 분류 탐색 (결정 위임 — 참고용)
            derived_category = classify_ingredient(m['norm_name'])

            ratio_records.append({
                'pair_id'          : f"{small_id}_{large_id}",
                'small_recipe_id'  : small_id,
                'large_recipe_id'  : large_id,
                'group_type'       : cooking_group,
                'cooking_method'   : cooking_method,
                'ingredient_name'  : m['norm_name'],
                'role'             : role,
                'derived_category' : derived_category,  # 탐색 참고용 (결정 위임)
                'N'                : N_val,
                'base'             : base,
                'Y'                : Y,
                'unit'             : unit_s,
                'ratio'            : ratio,
                'log_ratio'        : log_ratio,
                'log_N'            : log_N,
            })

    df_ratio = pd.DataFrame(ratio_records)

    print(f"  ratio 계산 완료: {len(df_ratio)}건")
    print(f"  제외 (base=0/Y=0/N=0 또는 결측): {n_excluded_zero}건")
    print(f"  제외 (unit 불일치): {n_excluded_unit_mismatch}건")
    print(f"  제외 (g/ml 외 단위): {n_excluded_non_gml}건")

    # 수분류/유지류 탐색 결과 보고 (결정 위임)
    cat_counts = df_ratio['derived_category'].value_counts(dropna=False)
    print(f"\n  [수분류/유지류 파생 분류 탐색 결과 — 결정 위임]")
    print(cat_counts.to_string())

    out_path = os.path.join(CONFIG['data_dir'], 'ratio_table.parquet')
    df_ratio.to_parquet(out_path, index=False)
    print(f"\n  저장: {out_path}")

    return df_ratio


# ─────────────────────────────────────────────────────────────────────────────
# TASK-03: 이상치 탐지 및 필터링
# ─────────────────────────────────────────────────────────────────────────────
def task03_outlier_detection(df_ratio):
    """
    이상치 탐지 및 필터링 기준 정의 (ADR-001 준수)

    ⚠️ 최종 채택 기준: 도메인 기준 (ratio ∈ [0.2, 3.0])
    이 기준은 2026-03-20 사전 확정 — 데이터 확인 후 변경 금지 (p-hacking 방지)

    출력:
        reports/outlier_report.md
        data/ratio_table_clean.parquet
    """
    print("\n[TASK-03] 이상치 탐지 및 필터링")

    n_before = len(df_ratio)

    # ── 방법 1: IQR 방법 (비교용) ─────────────────────────────────────────
    Q1  = df_ratio['ratio'].quantile(0.25)
    Q3  = df_ratio['ratio'].quantile(0.75)
    IQR = Q3 - Q1
    iqr_lower = Q1 - 1.5 * IQR
    iqr_upper = Q3 + 1.5 * IQR
    mask_iqr      = (df_ratio['ratio'] >= iqr_lower) & (df_ratio['ratio'] <= iqr_upper)
    n_iqr_removed = (~mask_iqr).sum()
    n_iqr_remain  = mask_iqr.sum()

    # ── 방법 2: 도메인 기준 (최종 채택) ──────────────────────────────────
    # ⚠️ 이 기준은 2026-03-20에 사전 확정 — 데이터 확인 후 변경 금지
    outlier_min = CONFIG['outlier_ratio_min']  # 0.2: 물리적으로 불가능한 하한
    outlier_max = CONFIG['outlier_ratio_max']  # 3.0: 물리적으로 불가능한 상한
    mask_domain      = (df_ratio['ratio'] >= outlier_min) & (df_ratio['ratio'] <= outlier_max)
    n_domain_removed = (~mask_domain).sum()
    n_domain_remain  = mask_domain.sum()

    print(f"  전체 ratio 분포: min={df_ratio['ratio'].min():.4f}, "
          f"max={df_ratio['ratio'].max():.4f}, median={df_ratio['ratio'].median():.4f}")
    print(f"  IQR 방법 [{iqr_lower:.4f}, {iqr_upper:.4f}]: 제거 {n_iqr_removed}건, 잔존 {n_iqr_remain}건")
    print(f"  도메인 기준 [{outlier_min}, {outlier_max}]: 제거 {n_domain_removed}건, 잔존 {n_domain_remain}건")
    print(f"\n  ✅ 최종 채택: 도메인 기준 (ADR-001 확정, 변경 금지)")

    # 도메인 기준으로 필터링
    df_clean  = df_ratio[mask_domain].copy()
    n_after   = len(df_clean)

    # 리포트 작성
    report = f"""# 이상치 탐지 보고서

**생성일:** 2026-03-20
**기준 문서:** TASK_EDA_ratio_analysis_20260320.md
**ADR:** ADR-001 Option A

---

## 이상치 탐지 방법 비교

| 방법 | 하한 | 상한 | 제거 건수 | 잔존 건수 |
|------|------|------|----------|---------|
| IQR 방법 (Q1-1.5×IQR ~ Q3+1.5×IQR) | {iqr_lower:.4f} | {iqr_upper:.4f} | {n_iqr_removed} | {n_iqr_remain} |
| **도메인 기준 (최종 채택)** | **{outlier_min}** | **{outlier_max}** | **{n_domain_removed}** | **{n_domain_remain}** |

---

## 최종 채택 기준

**도메인 기준: ratio ∈ [{outlier_min}, {outlier_max}]**

- `ratio < {outlier_min}`: 물리적으로 불가능 — 대량 조리 시 1인분의 1/5 이하 사용은 입력 오류로 간주
- `ratio > {outlier_max}`: 물리적으로 불가능 — 대량 조리 시 선형 예상량의 3배 이상은 입력 오류로 간주
- IQR 방법 **미채택** 이유: 데이터 분포에 따라 기준이 달라져 재현성이 낮음; p-hacking 위험

⚠️ **이 기준은 2026-03-20에 사전 확정되었으며, 데이터 확인 후 변경 금지 (p-hacking 방지)**

---

## 필터링 전후 비교

| 구분 | 건수 |
|------|------|
| 필터링 전 | {n_before} |
| 제거 건수 | {n_before - n_after} |
| **필터링 후** | **{n_after}** |

---

## ratio 기술통계 (필터링 전)

| 통계량 | 값 |
|--------|-----|
| min | {df_ratio['ratio'].min():.4f} |
| Q1  | {Q1:.4f} |
| median | {df_ratio['ratio'].median():.4f} |
| Q3  | {Q3:.4f} |
| max | {df_ratio['ratio'].max():.4f} |
| IQR | {IQR:.4f} |
"""

    report_path = os.path.join(CONFIG['reports_dir'], 'outlier_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  리포트 저장: {report_path}")

    out_path = os.path.join(CONFIG['data_dir'], 'ratio_table_clean.parquet')
    df_clean.to_parquet(out_path, index=False)
    print(f"  저장: {out_path}")

    return df_clean


# ─────────────────────────────────────────────────────────────────────────────
# TASK-04: 기술통계 및 분포 시각화
# ─────────────────────────────────────────────────────────────────────────────
def task04_descriptive_stats_and_viz(df_clean):
    """
    group_type × role 셀별 분포 파악 및 ADR-002 v2 SEPARATE/MERGE 결정

    출력:
        reports/cell_count_table.md
        reports/descriptive_stats.csv
        reports/figures/fig1_boxplot_group_role.png
        reports/figures/fig2_scatter_logN_logratio.png
        reports/figures/fig3_histogram_by_role.png
        reports/figures/fig4_violin_cooking_method.png
    """
    print("\n[TASK-04] 기술통계 및 시각화")

    # ── 4-1. 셀별 관측 수 집계 (SEPARATE/MERGE 결정용) ───────────────────
    cell_counts = (
        df_clean
        .groupby(['cooking_method', 'role'])['ratio']
        .count()
        .reset_index()
        .rename(columns={'ratio': 'n'})
    )
    # 기계적 적용: n ≥ 10 → SEPARATE, n < 10 → MERGE (3대분류 유지)
    cell_counts['decision'] = cell_counts['n'].apply(
        lambda x: 'SEPARATE' if x >= 10 else 'MERGE'
    )

    pivot = cell_counts.pivot_table(
        values='n', index='cooking_method', columns='role',
        aggfunc='sum', fill_value=0
    )
    pivot['TOTAL'] = pivot.sum(axis=1)

    print("\n  [셀별 n 집계 (cooking_method × role)]")
    print(pivot.to_string())

    n_separate = (cell_counts['n'] >= 10).sum()
    n_merge    = (cell_counts['n'] < 10).sum()

    # 피벗 마크다운 변환
    pivot_md = pivot.reset_index().to_markdown(index=False)

    cell_report = f"""# 셀별 관측 수 집계 — ADR-002 v2 §4.2

**생성일:** 2026-03-20
**기준:** 셀당 n ≥ 10 → SEPARATE, n < 10 → MERGE (3대분류 유지)
⚠️ 기계적 적용 — 주관적 조정 금지 (TASK 명세 §5.2 준수)

---

## 조리방법 세분류 × role 교차표 (n)

{pivot_md}

---

## SEPARATE / MERGE 결정

| cooking_method | role | n | 결정 |
|----------------|------|---|------|
"""
    for _, row in cell_counts.sort_values(['cooking_method', 'role']).iterrows():
        cell_report += f"| {row['cooking_method']} | {row['role']} | {row['n']} | {row['decision']} |\n"

    cell_report += f"""
---

## ADR-002 v2 §4.2 결론

- SEPARATE 셀 수: **{n_separate}개** (n ≥ 10)
- MERGE 대상 셀 수: **{n_merge}개** (n < 10)
- **결론:** n < 10인 조리방법 세분류는 3대분류(건열/습열/비가열)로 통합
"""

    report_path = os.path.join(CONFIG['reports_dir'], 'cell_count_table.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(cell_report)
    print(f"  셀 집계 리포트 저장: {report_path}")

    # ── 4-2. ratio 기술통계 ───────────────────────────────────────────────
    stats_list = []
    for (group_type, role), grp in df_clean.groupby(['group_type', 'role']):
        r = grp['ratio']
        q1, q3 = r.quantile(0.25), r.quantile(0.75)
        stats_list.append({
            'group_type': group_type,
            'role'      : role,
            'n'         : len(r),
            'mean'      : round(r.mean(), 4),
            'median'    : round(r.median(), 4),
            'std'       : round(r.std(), 4),
            'min'       : round(r.min(), 4),
            'max'       : round(r.max(), 4),
            'Q1'        : round(q1, 4),
            'Q3'        : round(q3, 4),
            'IQR'       : round(q3 - q1, 4),
        })
    df_stats = pd.DataFrame(stats_list)
    stats_path = os.path.join(CONFIG['reports_dir'], 'descriptive_stats.csv')
    df_stats.to_csv(stats_path, index=False, encoding='utf-8-sig')
    print(f"\n  기술통계 저장: {stats_path}")
    print(df_stats.to_string(index=False))

    # ── 4-3. 시각화 ──────────────────────────────────────────────────────
    roles       = ['main', 'sub', 'seasoning']
    group_types = sorted(df_clean['group_type'].dropna().unique())

    # Figure 1: group_type × role 박스플롯 (3×n_groups grid)
    n_rows = max(len(group_types), 1)
    fig1, axes1 = plt.subplots(n_rows, 3, figsize=(15, 5 * n_rows))
    if n_rows == 1:
        axes1 = axes1.reshape(1, 3)
    fig1.suptitle('group_type × role 조합별 ratio 박스플롯', fontsize=14, y=1.01)

    for i, gt in enumerate(group_types):
        for j, role in enumerate(roles):
            ax = axes1[i][j]
            subset = df_clean[
                (df_clean['group_type'] == gt) & (df_clean['role'] == role)
            ]['ratio'].dropna()
            if len(subset) > 0:
                ax.boxplot(subset.values, vert=True, patch_artist=True,
                           boxprops=dict(facecolor='lightblue'))
                ax.axhline(y=1, color='red', linestyle='--', alpha=0.7, label='ratio=1')
                ax.legend(fontsize=7)
            ax.set_title(f'{gt} / {role}\n(n={len(subset)})', fontsize=9)
            ax.set_ylabel('ratio')
            ax.set_xticks([])

    # 빈 그리드 숨기기
    for i in range(len(group_types), n_rows):
        for j in range(3):
            axes1[i][j].set_visible(False)

    plt.tight_layout()
    fig1_path = os.path.join(CONFIG['figures_dir'], 'fig1_boxplot_group_role.png')
    fig1.savefig(fig1_path, dpi=150, bbox_inches='tight')
    plt.close(fig1)
    print(f"\n  Figure 1 저장: {fig1_path}")

    # Figure 2: log(ratio) ~ log(N) 산점도 (group_type별 색상)
    palette = {'건열': 'tab:red', '습열': 'tab:blue', '비가열': 'tab:green'}
    fig2, ax2 = plt.subplots(figsize=(10, 7))
    for gt, grp in df_clean.groupby('group_type'):
        color = palette.get(gt, 'gray')
        ax2.scatter(grp['log_N'], grp['log_ratio'],
                    alpha=0.4, s=20, label=f'{gt} (n={len(grp)})', color=color)
    ax2.axhline(y=0, color='black', linestyle='--', alpha=0.5, label='log(ratio)=0 (ratio=1)')
    ax2.set_xlabel('log(N) — 대규모 인원수 로그')
    ax2.set_ylabel('log(ratio)')
    ax2.set_title('log(ratio) ~ log(N) 산점도 (group_type별 색상)')
    ax2.legend()
    fig2_path = os.path.join(CONFIG['figures_dir'], 'fig2_scatter_logN_logratio.png')
    fig2.savefig(fig2_path, dpi=150, bbox_inches='tight')
    plt.close(fig2)
    print(f"  Figure 2 저장: {fig2_path}")

    # Figure 3: ratio 히스토그램 (role별 서브플롯, 기준선 ratio=1)
    fig3, axes3 = plt.subplots(1, 3, figsize=(15, 5))
    fig3.suptitle('ratio 히스토그램 (role별, 기준선 ratio=1)', fontsize=13)
    for ax, role in zip(axes3, roles):
        subset = df_clean[df_clean['role'] == role]['ratio'].dropna()
        ax.hist(subset, bins=30, edgecolor='white', alpha=0.8, color='steelblue')
        ax.axvline(x=1, color='red', linestyle='--', linewidth=1.5, label='ratio=1 (선형)')
        ax.set_title(f'{role} (n={len(subset)})')
        ax.set_xlabel('ratio')
        ax.set_ylabel('빈도')
        ax.legend()
    plt.tight_layout()
    fig3_path = os.path.join(CONFIG['figures_dir'], 'fig3_histogram_by_role.png')
    fig3.savefig(fig3_path, dpi=150, bbox_inches='tight')
    plt.close(fig3)
    print(f"  Figure 3 저장: {fig3_path}")

    # Figure 4: 조리방법 세분류별 ratio violin plot
    methods_ordered = (
        df_clean.groupby('cooking_method')['ratio']
        .median()
        .sort_values()
        .index.tolist()
    )
    valid_pairs = [
        (m, df_clean[df_clean['cooking_method'] == m]['ratio'].dropna().values)
        for m in methods_ordered
        if len(df_clean[df_clean['cooking_method'] == m]['ratio'].dropna()) > 1
    ]

    fig4, ax4 = plt.subplots(figsize=(max(12, len(valid_pairs) * 1.5), 7))
    if valid_pairs:
        v_labels, v_data = zip(*valid_pairs)
        parts = ax4.violinplot(v_data, positions=range(len(v_labels)),
                               showmedians=True, showextrema=True)
        for pc in parts['bodies']:
            pc.set_facecolor('lightcoral')
            pc.set_alpha(0.7)
        ax4.set_xticks(range(len(v_labels)))
        ax4.set_xticklabels(v_labels, rotation=45, ha='right', fontsize=9)
        ax4.axhline(y=1, color='red', linestyle='--', alpha=0.7, label='ratio=1 (선형)')
        ax4.set_ylabel('ratio')
        ax4.set_title('조리방법 세분류별 ratio 분포 (Violin Plot, 중앙값 정렬)')
        ax4.legend()
    plt.tight_layout()
    fig4_path = os.path.join(CONFIG['figures_dir'], 'fig4_violin_cooking_method.png')
    fig4.savefig(fig4_path, dpi=150, bbox_inches='tight')
    plt.close(fig4)
    print(f"  Figure 4 저장: {fig4_path}")

    return df_stats


# ─────────────────────────────────────────────────────────────────────────────
# TASK-05: 비가열 그룹 Power Analysis
# ─────────────────────────────────────────────────────────────────────────────
def task05_power_analysis(df_clean):
    """
    ADR-002 v5 준수사항 15항 — n=비가열 그룹 검정력 산출
    statsmodels FTestAnovaPower 사용
    α = 0.05, 효과크기 f² = 0.15 (소), 0.25 (중)

    출력: reports/power_analysis.md
    """
    print("\n[TASK-05] Power Analysis (비가열 그룹)")

    df_noheat = df_clean[df_clean['group_type'] == '비가열']
    n_obs = len(df_noheat)
    print(f"  비가열 그룹 관측 수 (TASK-03 필터링 후): {n_obs}")

    power_calc = FTestAnovaPower()

    results = []
    for f2, size_label in [(0.15, '소(f²=0.15)'), (0.25, '중(f²=0.25)')]:
        f = np.sqrt(f2)
        try:
            # 양측 검정, k_groups=3 (main/sub/seasoning)
            power_val = power_calc.solve_power(
                effect_size=f, nobs=n_obs, alpha=0.05, k_groups=3
            )
        except Exception as e:
            print(f"  power 계산 오류 ({size_label}): {e}")
            power_val = np.nan
        results.append({
            '효과크기'   : size_label,
            'f'         : round(f, 4),
            'f²'        : f2,
            'α'         : 0.05,
            'n'         : n_obs,
            '1-β (검정력)': round(power_val, 4) if not np.isnan(power_val) else 'N/A',
        })

    df_power = pd.DataFrame(results)
    print(df_power.to_string(index=False))

    # 최소 효과크기 역산 (검정력 0.8 기준)
    try:
        min_f  = power_calc.solve_power(nobs=n_obs, alpha=0.05, power=0.8, k_groups=3)
        min_f2 = min_f ** 2
        min_f_str  = f"{min_f:.4f}"
        min_f2_str = f"{min_f2:.4f}"
    except Exception as e:
        print(f"  최소 효과크기 역산 오류: {e}")
        min_f_str = min_f2_str = 'N/A'

    print(f"\n  검정력 0.8 달성 최소 효과크기: f={min_f_str} (f²={min_f2_str})")

    power_md = df_power.to_markdown(index=False)

    report = f"""# Power Analysis — 비가열 그룹

**생성일:** 2026-03-20
**ADR:** ADR-002 v5 준수사항 15항
**검정 대상:** 비가열 그룹 (group_type='비가열')

---

## 분석 조건

- α = 0.05 (양측)
- 효과크기: f² = 0.15 (소), f² = 0.25 (중)
- 비가열 실제 n: **{n_obs}** (TASK-03 이상치 필터링 후 재료 관측 수)
- k_groups = 3 (main / sub / seasoning)
- 소프트웨어: statsmodels.stats.power.FTestAnovaPower

---

## 검정력 산출 결과

{power_md}

---

## 최소 효과크기 역산 (검정력 1-β = 0.8 기준)

- 필요 f = **{min_f_str}** (f² = {min_f2_str})
- 현재 n={n_obs}에서 1-β=0.8 달성을 위한 최소 효과크기

---

## 해석

- 멱함수 가설: ratio = a × N^(b-1), 귀무가설 H₀: b=1 (ratio≈1, 완전 선형)
- 비가열 그룹은 상대적으로 샘플 수가 적어 소 효과크기 검출력이 낮을 수 있음
- f² < 0.15인 소 효과크기는 현재 표본 수로 검출 어려울 수 있음 — 표본 확보 검토 필요
"""

    report_path = os.path.join(CONFIG['reports_dir'], 'power_analysis.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  리포트 저장: {report_path}")


# ─────────────────────────────────────────────────────────────────────────────
# TASK-06: OLS 기준선 회귀 (MixedLM AIC 비교 준비)
# ─────────────────────────────────────────────────────────────────────────────
def task06_ols_baseline(df_clean):
    """
    ADR-002 v4 준수사항 6항 — OLS 단순회귀로 기준선 확보
    모델: log(ratio) ~ log(N) + C(role)

    ⚠️ MixedLM은 이 태스크의 범위 외 — OLS까지만 실행

    출력:
        reports/ols_baseline.md
        reports/figures/fig5_ols_residuals.png
    """
    print("\n[TASK-06] OLS 기준선 회귀")

    df_reg = df_clean[['log_ratio', 'log_N', 'role', 'group_type', 'pair_id']].dropna()
    print(f"  회귀 투입 행수: {len(df_reg)}")

    results_list = []
    model_all = None

    # 전체 데이터 OLS
    try:
        model_all = smf.ols('log_ratio ~ log_N + C(role)', data=df_reg).fit()
        results_list.append({
            'model'       : '전체',
            'n'           : int(model_all.nobs),
            'AIC'         : round(model_all.aic, 2),
            'BIC'         : round(model_all.bic, 2),
            'R²'          : round(model_all.rsquared, 4),
            'Adj_R²'      : round(model_all.rsquared_adj, 4),
            'log_N_coef'  : round(model_all.params.get('log_N', np.nan), 4),
            'log_N_pval'  : round(model_all.pvalues.get('log_N', np.nan), 4),
        })
        print(f"\n  [전체 OLS 요약]\n{model_all.summary().tables[1]}")
    except Exception as e:
        print(f"  전체 OLS 오류: {e}")

    # group_type별 OLS
    for gt, grp in df_reg.groupby('group_type'):
        if len(grp) < 10:
            print(f"  {gt}: n={len(grp)} < 10, OLS 건너뜀")
            continue
        try:
            model_gt = smf.ols('log_ratio ~ log_N + C(role)', data=grp).fit()
            results_list.append({
                'model'      : gt,
                'n'          : int(model_gt.nobs),
                'AIC'        : round(model_gt.aic, 2),
                'BIC'        : round(model_gt.bic, 2),
                'R²'         : round(model_gt.rsquared, 4),
                'Adj_R²'     : round(model_gt.rsquared_adj, 4),
                'log_N_coef' : round(model_gt.params.get('log_N', np.nan), 4),
                'log_N_pval' : round(model_gt.pvalues.get('log_N', np.nan), 4),
            })
        except Exception as e:
            print(f"  {gt} OLS 오류: {e}")

    df_results = pd.DataFrame(results_list)
    print(f"\n  OLS 결과 요약:")
    print(df_results.to_string(index=False))

    # Figure 5: 잔차 플롯 (랜덤효과 필요성 시각적 확인)
    if model_all is not None:
        color_map = {
            gt: c for gt, c in zip(
                df_reg['group_type'].unique(),
                ['tab:red', 'tab:blue', 'tab:green', 'gray', 'orange']
            )
        }
        fitted = model_all.fittedvalues
        resid  = model_all.resid

        fig5, axes5 = plt.subplots(1, 2, figsize=(14, 6))
        fig5.suptitle('OLS 잔차 플롯 — log(ratio) ~ log(N) + C(role)', fontsize=13)

        # 왼쪽: fitted vs residuals (group_type별 색상)
        ax_left = axes5[0]
        for gt, idx_grp in df_reg.groupby('group_type').groups.items():
            mask = df_reg.index.isin(idx_grp)
            ax_left.scatter(fitted[mask], resid[mask],
                            alpha=0.4, s=15, label=f'{gt} (n={mask.sum()})',
                            color=color_map.get(gt, 'gray'))
        ax_left.axhline(y=0, color='black', linestyle='--', linewidth=1)
        ax_left.set_xlabel('적합값 (Fitted values)')
        ax_left.set_ylabel('잔차 (Residuals)')
        ax_left.set_title('잔차 vs 적합값 (group_type별 색상)')
        ax_left.legend(fontsize=8)

        # 오른쪽: log_N vs residuals
        ax_right = axes5[1]
        for gt, idx_grp in df_reg.groupby('group_type').groups.items():
            mask = df_reg.index.isin(idx_grp)
            ax_right.scatter(df_reg.loc[mask, 'log_N'], resid[mask],
                             alpha=0.4, s=15, label=f'{gt}',
                             color=color_map.get(gt, 'gray'))
        ax_right.axhline(y=0, color='black', linestyle='--', linewidth=1)
        ax_right.set_xlabel('log(N)')
        ax_right.set_ylabel('잔차 (Residuals)')
        ax_right.set_title('잔차 vs log(N)\n(군집 패턴이 있으면 MixedLM 권장)')
        ax_right.legend(fontsize=8)

        plt.tight_layout()
        fig5_path = os.path.join(CONFIG['figures_dir'], 'fig5_ols_residuals.png')
        fig5.savefig(fig5_path, dpi=150, bbox_inches='tight')
        plt.close(fig5)
        print(f"  Figure 5 저장: {fig5_path}")

    # OLS 리포트 작성
    summary_str = str(model_all.summary()) if model_all is not None else '회귀 실패'
    results_md  = df_results.to_markdown(index=False) if len(df_results) > 0 else '결과 없음'

    report = f"""# OLS 기준선 회귀 — MixedLM AIC 비교 준비

**생성일:** 2026-03-20
**ADR:** ADR-002 v4 준수사항 6항
**모델:** `log(ratio) ~ log(N) + C(role)`

⚠️ MixedLM은 이 태스크의 범위 외 — OLS까지만 실행

---

## 회귀계수 및 적합도 요약

{results_md}

---

## 전체 모델 요약

```
{summary_str}
```

---

## 해석

- `log_N` 계수 ≈ (b-1): 멱함수 `ratio = a × N^(b-1)`에서 b 추정
  - b = 1 → log_N 계수 = 0 → 완전 선형 (ratio≈1)
  - b < 1 → log_N 계수 < 0 → 규모의 경제 (대량 시 덜 필요 — 양념류 예상)
  - b > 1 → log_N 계수 > 0 → 규모 비례 이상 증가
- 잔차 플롯에서 group_type별 군집 패턴 → MixedLM 필요성 근거
- MixedLM 비교는 이 태스크 범위 외
"""

    report_path = os.path.join(CONFIG['reports_dir'], 'ols_baseline.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  리포트 저장: {report_path}")

    return df_results


# ─────────────────────────────────────────────────────────────────────────────
# 메인 파이프라인
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("EDA 파이프라인 시작 — OptiMeal 모듈 2 비선형 스케일링 엔진")
    print("=" * 60)

    print("\n⚠️ 파일 버전 불일치 주의 (태스크 명세 vs 실제 사용):")
    print("  소규모 레시피: 명세=v0_08 → 실제=v0_10 (최신 버전 채택)")
    print("  정규화 테이블: 명세=v4_1 → 실제=v4_0 (최신 버전 채택)")
    print("  matched_pairs: is_manually_verified=False (268쌍 전체 포함, 사람 검수 필요)")

    create_directories()
    setup_korean_font()

    # TASK-01: wide → long 변환
    df_long, df_pairs = task01_load_and_melt()

    # TASK-02: ratio 계산
    df_ratio = task02_calculate_ratio(df_long, df_pairs)

    # TASK-03: 이상치 탐지 및 필터링
    df_clean = task03_outlier_detection(df_ratio)

    # TASK-04: 기술통계 및 시각화
    df_stats = task04_descriptive_stats_and_viz(df_clean)

    # TASK-05: Power Analysis (비가열 그룹)
    task05_power_analysis(df_clean)

    # TASK-06: OLS 기준선 회귀
    task06_ols_baseline(df_clean)

    print("\n" + "=" * 60)
    print("EDA 파이프라인 완료")
    print(f"  데이터: {CONFIG['data_dir']}")
    print(f"  리포트: {CONFIG['reports_dir']}")
    print("=" * 60)


if __name__ == '__main__':
    main()
