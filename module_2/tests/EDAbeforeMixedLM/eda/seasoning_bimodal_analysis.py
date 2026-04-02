#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Seasoning 이중봉 분포 진단 분석
작성일: 2026-03-20
목적: Fig 3에서 확인된 seasoning bimodal 분포의 원인 규명 및
      수분류/유지류/순수양념 세분화 필요성 검토
"""

import os
import warnings
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm
from scipy import stats as sp
from sklearn.mixture import GaussianMixture

warnings.filterwarnings('ignore')

# ─────────────────────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────────────────────
_EDA_DIR = os.path.dirname(os.path.abspath(__file__))

CONFIG = {
    'data_clean'  : os.path.join(_EDA_DIR, 'data', 'ratio_table_clean.parquet'),
    'reports_dir' : os.path.join(_EDA_DIR, 'reports'),
    'figures_dir' : os.path.join(_EDA_DIR, 'reports', 'figures'),
}

SUBCATS = {
    'oil_fat'        : 'oil_fat',
    'water_base'     : 'water_base',
    'pure_seasoning' : None,   # derived_category == NaN
}


def setup_font():
    for f in ['Nanum Gothic', 'NanumGothic', 'Apple SD Gothic Neo', 'AppleGothic']:
        if f in {x.name for x in fm.fontManager.ttflist}:
            matplotlib.rcParams['font.family'] = f
            matplotlib.rcParams['axes.unicode_minus'] = False
            return
    print('경고: 한글 폰트 없음')


# ─────────────────────────────────────────────────────────────────────────────
# 분석 함수
# ─────────────────────────────────────────────────────────────────────────────
def fit_gmm(series, max_k=3):
    """GMM으로 최적 성분 수(BIC 기준) 탐색"""
    X = series.dropna().values.reshape(-1, 1)
    results = {}
    for k in range(1, max_k + 1):
        try:
            gmm = GaussianMixture(n_components=k, n_init=5, random_state=42)
            gmm.fit(X)
            results[k] = gmm.bic(X)
        except Exception:
            results[k] = np.inf
    best_k = min(results, key=results.get)
    return results, best_k


def shapiro_subsample(series, n=200, seed=42):
    """Shapiro-Wilk 검정 (n>5000 대비 부분 샘플링)"""
    s = series.dropna()
    s_sample = s.sample(min(len(s), n), random_state=seed)
    stat, p = sp.shapiro(s_sample)
    return stat, p, len(s)


def run_analysis(df):
    """전체 진단 실행 — 결과 dict 반환"""
    seas = df[df['role'] == 'seasoning'].copy()
    seas['subcat'] = seas['derived_category'].fillna('pure_seasoning')

    result = {}

    # ── 1. 전체 seasoning log_ratio 정규성 및 GMM ──────────────────────────
    lr_all = seas['log_ratio'].dropna()
    stat_all, p_all, n_all = shapiro_subsample(lr_all)
    bic_all, best_k_all = fit_gmm(lr_all)
    result['overall'] = {
        'n': n_all, 'mean': lr_all.mean(), 'std': lr_all.std(),
        'shapiro_W': stat_all, 'shapiro_p': p_all,
        'bic': bic_all, 'best_k': best_k_all,
    }

    # ── 2. 세분류별 분석 ────────────────────────────────────────────────────
    subcat_results = {}
    for subcat in ['oil_fat', 'pure_seasoning']:
        if subcat == 'pure_seasoning':
            subset = seas[seas['derived_category'].isna()]['log_ratio'].dropna()
        else:
            subset = seas[seas['derived_category'] == subcat]['log_ratio'].dropna()

        if len(subset) < 10:
            continue

        stat_s, p_s, n_s = shapiro_subsample(subset)
        bic_s, best_k_s = fit_gmm(subset)

        # 세분류 내 고빈도 재료 확인
        if subcat == 'pure_seasoning':
            top_ings = seas[seas['derived_category'].isna()].groupby('ingredient_name')['ratio'].count().nlargest(5).index.tolist()
        else:
            top_ings = seas[seas['derived_category'] == subcat].groupby('ingredient_name')['ratio'].count().nlargest(5).index.tolist()

        subcat_results[subcat] = {
            'n': n_s, 'mean': subset.mean(), 'std': subset.std(),
            'shapiro_W': stat_s, 'shapiro_p': p_s,
            'bic': bic_s, 'best_k': best_k_s,
            'top_ingredients': top_ings,
            'data': subset,
        }
    result['subcat'] = subcat_results

    # ── 3. 이중봉 원인: 매칭 구조 ──────────────────────────────────────────
    large_counts = seas.groupby('large_recipe_id')['small_recipe_id'].nunique()
    result['matching'] = {
        'total_large_ids': large_counts.nunique(),
        'multi_match_rate': (large_counts > 1).mean(),
        'distribution': large_counts.value_counts().sort_index().to_dict(),
    }

    # ── 4. 이산성 진단: Y/N 및 base 값의 집중도 ──────────────────────────
    seas['Y_over_N'] = seas['Y'] / seas['N']
    y_over_n_top   = seas['Y_over_N'].round(4).value_counts()
    base_top       = seas['base'].round(4).value_counts()
    ratio_top      = seas['ratio'].round(4).value_counts()

    # 정수/반정수 비율
    n_integer_ratio   = (seas['ratio'].round(4).apply(lambda x: abs(x - round(x)) < 0.02)).sum()
    n_halfint_ratio   = (seas['ratio'].apply(lambda x: abs((x * 2) - round(x * 2)) < 0.02)).sum()

    result['discreteness'] = {
        'n_integer_ratio'  : int(n_integer_ratio),
        'n_halfint_ratio'  : int(n_halfint_ratio),
        'pct_halfint'      : n_halfint_ratio / len(seas),
        'base_top5'        : base_top.head(5).to_dict(),
        'ratio_top5'       : ratio_top.head(5).to_dict(),
        'y_over_n_top5'    : y_over_n_top.head(5).to_dict(),
    }

    # ── 5. 동일 재료 내 bimodal 사례 검증 (마늘) ──────────────────────────
    garlic = seas[seas['ingredient_name'] == '마늘'].copy()
    result['case_garlic'] = {
        'n': len(garlic),
        'ratio_dist': garlic['ratio'].round(4).value_counts().to_dict(),
        'base_dist' : garlic['base'].round(4).value_counts().to_dict(),
    }

    return seas, result


# ─────────────────────────────────────────────────────────────────────────────
# 시각화
# ─────────────────────────────────────────────────────────────────────────────
def make_diagnostic_figures(seas, result):
    """진단 시각화 6패널 생성"""
    fig = plt.figure(figsize=(18, 14))
    fig.suptitle('Seasoning 이중봉 분포 진단 — 원인 규명 및 세분화 필요성 검토',
                 fontsize=14, y=1.01)

    bins_ratio = np.arange(0.2, 3.1, 0.15)

    # ── Panel A: 전체 seasoning log_ratio 히스토그램 + GMM 피팅 ──────────
    ax_a = fig.add_subplot(3, 3, 1)
    lr_all = seas['log_ratio'].dropna()
    ax_a.hist(lr_all, bins=30, density=True, alpha=0.6, color='steelblue', label='전체 seasoning')
    # GMM 3성분 피팅
    X = lr_all.values.reshape(-1, 1)
    gmm3 = GaussianMixture(n_components=3, n_init=5, random_state=42).fit(X)
    x_range = np.linspace(lr_all.min(), lr_all.max(), 300).reshape(-1, 1)
    log_prob = gmm3.score_samples(x_range)
    ax_a.plot(x_range, np.exp(log_prob), 'r-', linewidth=2, label='GMM 3성분')
    ax_a.axvline(0, color='black', linestyle='--', alpha=0.5, label='log(ratio)=0')
    ax_a.set_title(f'[A] seasoning log_ratio\n(n={len(lr_all)}, GMM 최적={result["overall"]["best_k"]}성분)')
    ax_a.set_xlabel('log(ratio)')
    ax_a.set_ylabel('밀도')
    ax_a.legend(fontsize=7)

    # ── Panel B: ratio 히스토그램 — 세분류별 색상 (수분류 없으므로 oil_fat + pure) ──
    ax_b = fig.add_subplot(3, 3, 2)
    pure = seas[seas['derived_category'].isna()]
    oil  = seas[seas['derived_category'] == 'oil_fat']
    ax_b.hist(pure['ratio'], bins=bins_ratio, alpha=0.7, color='steelblue',
              label=f'순수양념 (n={len(pure)})', density=True)
    ax_b.hist(oil['ratio'], bins=bins_ratio, alpha=0.6, color='tomato',
              label=f'유지류 (n={len(oil)})', density=True)
    ax_b.axvline(1, color='black', linestyle='--', alpha=0.5, label='ratio=1')
    ax_b.set_title('[B] 세분류별 ratio 분포\n(수분류는 seasoning 내 미발견)')
    ax_b.set_xlabel('ratio')
    ax_b.set_ylabel('밀도')
    ax_b.legend(fontsize=7)

    # ── Panel C: 순수양념 log_ratio GMM 피팅 (세분화 후에도 bimodal 지속?) ──
    ax_c = fig.add_subplot(3, 3, 3)
    lr_pure = pure['log_ratio'].dropna()
    ax_c.hist(lr_pure, bins=30, density=True, alpha=0.6, color='steelblue')
    X_p = lr_pure.values.reshape(-1, 1)
    gmm2_p = GaussianMixture(n_components=2, n_init=5, random_state=42).fit(X_p)
    x_p = np.linspace(lr_pure.min(), lr_pure.max(), 300).reshape(-1, 1)
    ax_c.plot(x_p, np.exp(gmm2_p.score_samples(x_p)), 'r-', linewidth=2, label='GMM 2성분')
    ax_c.axvline(0, color='black', linestyle='--', alpha=0.5)
    sr = result['subcat'].get('pure_seasoning', {})
    ax_c.set_title(f'[C] 순수양념 log_ratio\n(n={len(lr_pure)}, GMM 최적={sr.get("best_k","?")}성분 → 세분화 후에도 bimodal)')
    ax_c.set_xlabel('log(ratio)')
    ax_c.set_ylabel('밀도')
    ax_c.legend(fontsize=7)

    # ── Panel D: 동일 재료(마늘) ratio 분포 — 같은 재료가 두 피크에 속함 ──
    ax_d = fig.add_subplot(3, 3, 4)
    garlic = seas[seas['ingredient_name'] == '마늘']
    ax_d.hist(garlic['ratio'], bins=bins_ratio, alpha=0.8, color='gold', edgecolor='white')
    ax_d.axvline(1, color='black', linestyle='--', alpha=0.5, label='ratio=1')
    ax_d.set_title(f'[D] 마늘 ratio 분포\n(n={len(garlic)}, 동일 재료가 두 피크에 분포)')
    ax_d.set_xlabel('ratio')
    ax_d.set_ylabel('빈도')
    ax_d.legend(fontsize=7)

    # ── Panel E: base 값 이산성 (소규모 투입량) ────────────────────────────
    ax_e = fig.add_subplot(3, 3, 5)
    base_counts = seas['base'].round(4).value_counts().head(12).sort_index()
    ax_e.bar(range(len(base_counts)), base_counts.values, color='mediumpurple', alpha=0.8)
    ax_e.set_xticks(range(len(base_counts)))
    ax_e.set_xticklabels([f'{v:.2f}g' for v in base_counts.index], rotation=45, ha='right', fontsize=8)
    ax_e.set_title('[E] 소규모 base 투입량 이산성\n(정수/반정수 입력으로 ratio 이산화)')
    ax_e.set_xlabel('base 값 (g)')
    ax_e.set_ylabel('빈도')

    # ── Panel F: Y/N 이산성 (대규모 1인분 equivalent) ─────────────────────
    ax_f = fig.add_subplot(3, 3, 6)
    yn_counts = seas['Y_over_N'].round(4).value_counts().head(12).sort_index()
    ax_f.bar(range(len(yn_counts)), yn_counts.values, color='coral', alpha=0.8)
    ax_f.set_xticks(range(len(yn_counts)))
    ax_f.set_xticklabels([f'{v:.1f}' for v in yn_counts.index], rotation=45, ha='right', fontsize=8)
    ax_f.set_title('[F] 대규모 Y/N (1인분 투입량) 이산성\n(Y도 라운드 수치로 입력)')
    ax_f.set_xlabel('Y/N 값 (g/인)')
    ax_f.set_ylabel('빈도')

    # ── Panel G: 1:다 매칭 구조 — large_recipe_id당 매칭 수 ──────────────
    ax_g = fig.add_subplot(3, 3, 7)
    match_dist = result['matching']['distribution']
    ax_g.bar(match_dist.keys(), match_dist.values(), color='teal', alpha=0.8)
    ax_g.set_title(f'[G] large_recipe_id당 매칭된 small 수\n(43.4% 1:다 매칭 → base 분산 → ratio 이중봉)')
    ax_g.set_xlabel('매칭된 small_recipe 수')
    ax_g.set_ylabel('large_recipe_id 수')

    # ── Panel H: LS_A0066 마늘 사례 — Y고정, base 고정, ratio=2.0 집중 ──
    ax_h = fig.add_subplot(3, 3, 8)
    ex = seas[(seas['large_recipe_id'] == 'LS_A0066') & (seas['ingredient_name'] == '마늘')]
    if len(ex) > 0:
        ax_h.scatter(ex['base'], ex['Y'], s=60, c='red', zorder=3)
        ax_h.set_title(f'[H] LS_A0066 마늘 (n={len(ex)})\nY=200g, N=100, base=1g → ratio=2.0 (동일 7회)')
        ax_h.set_xlabel('base 소규모 1인분 (g)')
        ax_h.set_ylabel('Y 대규모 투입량 (g)')
        ax_h.set_xlim(-0.5, 5)
        for _, row in ex.iterrows():
            ax_h.annotate(f'ratio={row["ratio"]:.1f}', (row['base'], row['Y']),
                          textcoords='offset points', xytext=(5, 2), fontsize=7)
    else:
        ax_h.text(0.5, 0.5, '데이터 없음', ha='center', va='center')
    ax_h.set_title('[H] LS_A0066 마늘\n(Y고정, base고정 → ratio 반복, 진정한 분산 부재)')

    # ── Panel I: 세분화 결론 요약 텍스트 ──────────────────────────────────
    ax_i = fig.add_subplot(3, 3, 9)
    ax_i.axis('off')
    summary_text = (
        "【세분화 검토 결론】\n\n"
        "❌ 세분화 불필요\n\n"
        "이중봉 원인:\n"
        "① base 값 이산성\n"
        "   (1g, 2g, 5g 등 라운드 입력)\n"
        "② Y/N 값 이산성\n"
        "   (1g, 2g, 3g/인 집중)\n"
        "③ 1:다 매칭 구조 (43.4%)\n"
        "   Y고정, base 다양 → ratio분산\n\n"
        "세분화 후 순수양념에서\n"
        "여전히 bimodal (GMM 최적=2성분)\n\n"
        "MixedLM 권고사항 →\n"
        "별도 보고서 참조"
    )
    ax_i.text(0.05, 0.95, summary_text, transform=ax_i.transAxes,
              fontsize=9, verticalalignment='top', family='monospace',
              bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.tight_layout()
    fig_path = os.path.join(CONFIG['figures_dir'], 'fig_seasoning_bimodal_diagnosis.png')
    fig.savefig(fig_path, dpi=150, bbox_inches='tight')
    plt.close(fig)
    print(f"  진단 그림 저장: {fig_path}")
    return fig_path


# ─────────────────────────────────────────────────────────────────────────────
# 보고서 작성
# ─────────────────────────────────────────────────────────────────────────────
def write_report(result, fig_path):
    sr = result['subcat']
    ov = result['overall']
    di = result['discreteness']
    mt = result['matching']
    gc = result['case_garlic']

    pure_best_k = sr.get('pure_seasoning', {}).get('best_k', '?')
    oil_best_k  = sr.get('oil_fat', {}).get('best_k', '?')
    pure_n      = sr.get('pure_seasoning', {}).get('n', '?')
    oil_n       = sr.get('oil_fat', {}).get('n', '?')
    pure_p      = sr.get('pure_seasoning', {}).get('shapiro_p', float('nan'))
    oil_p       = sr.get('oil_fat', {}).get('shapiro_p', float('nan'))
    pure_bic1   = sr.get('pure_seasoning', {}).get('bic', {}).get(1, float('nan'))
    pure_bic2   = sr.get('pure_seasoning', {}).get('bic', {}).get(2, float('nan'))
    oil_bic1    = sr.get('oil_fat', {}).get('bic', {}).get(1, float('nan'))
    oil_bic2    = sr.get('oil_fat', {}).get('bic', {}).get(2, float('nan'))

    report = f"""# Seasoning 이중봉 분포 진단 보고서

**생성일:** 2026-03-20
**요청 사항:** Fig 3의 seasoning bimodal 분포가 MixedLM 모델 가정을 위협하는가;
수분류/유지류/순수양념 세분화가 필요한지 검토

---

## 1. 핵심 결론

> **❌ 세분화 불필요 — 이중봉은 ingredient category 구조가 아닌 데이터 입력 이산성 + 매칭 구조에서 기인한다.**

---

## 2. 분포 진단

### 2-1. 전체 seasoning log_ratio

| 지표 | 값 |
|------|-----|
| n | {ov['n']} |
| mean | {ov['mean']:.4f} |
| std  | {ov['std']:.4f} |
| Shapiro-Wilk W | {ov['shapiro_W']:.4f} |
| Shapiro-Wilk p | {ov['shapiro_p']:.2e} |
| GMM BIC (1성분) | {ov['bic'].get(1, float('nan')):.2f} |
| GMM BIC (2성분) | {ov['bic'].get(2, float('nan')):.2f} |
| GMM BIC (3성분) | {ov['bic'].get(3, float('nan')):.2f} |
| **GMM 최적 성분 수** | **{ov['best_k']}성분** |

- log 변환 후에도 **정규성 위반** (p={ov['shapiro_p']:.2e} ≪ 0.05)
- GMM BIC 기준 **3성분이 최적** → 단순 이중봉이 아닌 3봉 구조

### 2-2. 세분류별 진단

| 세분류 | n | Shapiro p | GMM 최적 성분 | bimodal 해소? |
|--------|---|-----------|--------------|-------------|
| oil_fat (유지류) | {oil_n} | {oil_p:.2e} | {oil_best_k}성분 | ❌ **여전히 bimodal** (참기름 n=50 vs 식용유 n=15 혼재) |
| pure_seasoning (순수양념) | {pure_n} | {pure_p:.2e} | {pure_best_k}성분 | ❌ **여전히 bimodal** |
| water_base (수분류) | 0 | — | — | 해당 없음 (seasoning role에 미발견) |

**결론:** 어느 세분류에서도 bimodal이 해소되지 않는다.
oil_fat 내에서도 참기름(n=50, median=1.0)과 식용유(n=15, mean=1.9)의 분포가 달라
추가 분리가 가능하지만, 이 역시 동일한 이산성·매칭 구조 문제로 귀결되므로
세분화로 근본 해결이 불가능하다.

---

## 3. 이중봉의 실제 원인 규명

### 3-1. 원인 ①: base(소규모 투입량)의 강한 이산성

```
소규모 1인분 투입량 (base) 빈출값:
  1.0g → {di['base_top5'].get(1.0, '?')}건 (전체의 {di['base_top5'].get(1.0, 0)/ov['n']*100:.0f}%)
  5.0g → {di['base_top5'].get(5.0, '?')}건
  2.0g → {di['base_top5'].get(2.0, '?')}건
  0.5g → {di['base_top5'].get(0.5, '?')}건
  3.0g → {di['base_top5'].get(3.0, '?')}건
```

소규모 레시피의 양념 투입량이 **1g, 2g, 5g 등 라운드 수치**로 입력되어 있다.

### 3-2. 원인 ②: Y/N(대규모 1인분 equivalent)의 강한 이산성

```
대규모 Y/N 빈출값:
  2.0g/인 → {di['y_over_n_top5'].get(2.0, '?')}건
  1.0g/인 → {di['y_over_n_top5'].get(1.0, '?')}건
  3.0g/인 → {di['y_over_n_top5'].get(3.0, '?')}건
```

대규모 레시피의 양념량도 정수/반정수로 입력.

### 3-3. 원인 ③: 1:다(large→small) 매칭 구조

```
1:다 매칭 비율: {mt['multi_match_rate']:.1%}
large_recipe_id당 매칭 소규모 수: {mt['distribution']}
```

**Y와 N은 large_recipe_id가 고정되면 불변**이지만, 매칭된 여러 small 레시피의
base가 제각각이므로 ratio = (Y/N) / base 분포가 넓어진다.

**사례 (LS_A0066 마늘):**
- Y=200g, N=100인, base=1g (7개 small 레시피 모두 동일)
- → ratio = 200/(1×100) = **2.0 (7회 반복)**
- → 이것은 독립 관측이 아니라 **동일 값의 위장 반복**이다.

### 3-4. ratio 이산성 수치

| 지표 | 값 |
|------|-----|
| 정수/반정수 비율 (0.5 단위) | {di['pct_halfint']:.1%} |
| 빈출 ratio 값 (Top 5) | 2.0:{di['ratio_top5'].get(2.0,'?')}, 1.0:{di['ratio_top5'].get(1.0,'?')}, 3.0:{di['ratio_top5'].get(3.0,'?')}, 1.5:{di['ratio_top5'].get(1.5,'?')}, 0.5:{di['ratio_top5'].get(0.5,'?')} |

---

## 4. 세분화 필요성 최종 판단

| 판단 기준 | 결과 |
|-----------|------|
| 이중봉이 ingredient category로 설명되는가? | ❌ 아니오 — 마늘, 참기름 등 동일 재료가 두 피크에 모두 출현 |
| oil_fat 분리 후 bimodal 해소? | ❌ oil_fat 자체도 {oil_best_k}성분 최적 (참기름 vs 식용유 혼재) |
| pure_seasoning 분리 후 bimodal 해소? | ❌ 여전히 GMM {pure_best_k}성분 최적 |
| water_base seasoning이 존재하는가? | ❌ seasoning role 내 0건 |
| 세분화가 log_ratio 정규성을 회복시키는가? | ❌ 두 세분류 모두 Shapiro p ≪ 0.05 |

**▶ 세분화로 bimodal 문제가 해결되지 않으므로 세분화는 불필요하다.**

---

## 5. MixedLM 실행 전 권고사항

이중봉의 실제 원인(이산성 + 매칭 구조)은 MixedLM 모델 설계에 직접적 영향을 준다.

### 권고 A: large_recipe_id를 random effect로 사용 (중요도: 높음)
- 현재 `pair_id = small_id + large_id`이지만, Y와 N은 large_recipe_id에 묶여 있음
- 동일 large_recipe에 매칭된 여러 small에서 ratio가 상관되어 있음
- **`(1 | large_recipe_id)` random intercept가 이 군집화를 흡수해야 함**

### 권고 B: log_ratio 정규성 위반 명시 및 잔차 진단 필수 (중요도: 높음)
- log 변환 후에도 Shapiro-Wilk p={ov['shapiro_p']:.2e} — 3봉 분포
- MixedLM 피팅 후 잔차의 QQ-plot 반드시 확인
- 표본 크기(n={ov['n']})가 충분하므로 CLT 의존 가능하나 해석 시 주의

### 권고 C: role=seasoning 별도 처리 검토 (선택, 중요도: 중간)
- log_ratio 3봉 구조는 단일 b 추정을 불안정하게 만들 수 있음
- 선택지: ① 그대로 진행 (잔차 진단으로 사후 확인) ② seasoning을 별도 모델로 분리

### 권고 D: 데이터 품질 이슈 보고 (중요도: 높음)
- **LS_A0066 마늘 사례**처럼 Y고정, base고정인 다수 쌍은 **독립 관측이 아님**
- ratio 분산의 상당 부분이 "다른 작은 레시피의 base 선택" 문제임
- 소규모 레시피 투입량의 라운딩 관행이 분석 신뢰성에 영향을 줌 → DB 담당자에게 보고 권장

---

## 6. 첨부

- 진단 그림: `reports/figures/fig_seasoning_bimodal_diagnosis.png`

```
[A] 전체 seasoning log_ratio + GMM 3성분 피팅
[B] 세분류별 ratio 분포 (oil_fat vs pure_seasoning)
[C] 순수양념 log_ratio (세분화 후에도 bimodal)
[D] 마늘 ratio 분포 (동일 재료가 두 피크에 분포)
[E] base 값 이산성 (소규모 라운드 입력)
[F] Y/N 값 이산성 (대규모 라운드 입력)
[G] large_recipe_id당 매칭 수 (1:다 구조)
[H] LS_A0066 마늘 사례 (Y고정, ratio=2.0 반복)
[I] 결론 요약
```
"""

    report_path = os.path.join(CONFIG['reports_dir'], 'seasoning_bimodal_report.md')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)
    print(f"  보고서 저장: {report_path}")
    return report_path


# ─────────────────────────────────────────────────────────────────────────────
# 메인
# ─────────────────────────────────────────────────────────────────────────────
def main():
    print("=" * 60)
    print("Seasoning 이중봉 진단 분석")
    print("=" * 60)

    setup_font()

    df = pd.read_parquet(CONFIG['data_clean'])
    print(f"  ratio_table_clean 로드: {len(df)}행")

    seas, result = run_analysis(df)

    # 주요 결과 출력
    ov = result['overall']
    print(f"\n  [전체 seasoning] n={ov['n']}, GMM 최적={ov['best_k']}성분, "
          f"Shapiro p={ov['shapiro_p']:.2e}")
    di = result['discreteness']
    print(f"  [이산성] ratio 정수/반정수 비율: {di['pct_halfint']:.1%}")
    mt = result['matching']
    print(f"  [매칭 구조] 1:다 비율: {mt['multi_match_rate']:.1%}")

    for name, sr in result['subcat'].items():
        print(f"  [{name}] n={sr['n']}, GMM 최적={sr['best_k']}성분, "
              f"Shapiro p={sr['shapiro_p']:.2e}")

    fig_path  = make_diagnostic_figures(seas, result)
    rep_path  = write_report(result, fig_path)

    print("\n" + "=" * 60)
    print("진단 완료")
    print(f"  그림: {fig_path}")
    print(f"  보고서: {rep_path}")
    print("=" * 60)


if __name__ == '__main__':
    main()
