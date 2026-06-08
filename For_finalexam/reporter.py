"""
reporter.py
===========
HTML 리포트 생성기. self-contained, inline CSS, lang=ko, UTF-8.

전역 규칙(~/.claude/CLAUDE.md): 사람 열람용 문서 = HTML.
발표 자료/슬라이드 첨부용으로 활용.

각 재료에 대해 다음을 시각적으로 노출:
  1. 입력값 (재료명, 1인분 g, 카테고리, group_type)
  2. 분기 사유 (학습 셀 → NN_v2 / 미커버 셀 → lookup)
  3. 사용한 b 값
  4. 계산 식: Y = base × N^b
  5. 결과 g

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

import html
from datetime import datetime
from pathlib import Path

from hybrid_engine import IngredientResult
from recipe_loader import RecipeBundle


CSS = """
* { box-sizing: border-box; }
body {
  font-family: -apple-system, "Apple SD Gothic Neo", "Noto Sans KR", sans-serif;
  margin: 0; padding: 24px 32px;
  background: #fafafa; color: #1f2328;
  line-height: 1.55;
}
header {
  border-bottom: 2px solid #1f2328;
  padding-bottom: 12px; margin-bottom: 20px;
}
h1 { font-size: 22px; margin: 0 0 4px 0; }
h2 { font-size: 16px; margin: 24px 0 10px; padding-bottom: 4px;
     border-bottom: 1px solid #d0d7de; }
.meta { color: #57606a; font-size: 13px; }
.meta b { color: #1f2328; }
.summary-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin: 16px 0;
}
.summary-card {
  background: #fff; border: 1px solid #d0d7de; border-radius: 6px;
  padding: 10px 14px;
}
.summary-card .label { font-size: 12px; color: #57606a; }
.summary-card .value { font-size: 18px; font-weight: 600; margin-top: 2px; }
table {
  width: 100%; border-collapse: collapse;
  background: #fff; border: 1px solid #d0d7de;
  font-size: 13px;
}
th, td {
  padding: 8px 10px;
  border-bottom: 1px solid #e6e9ec;
  text-align: left; vertical-align: top;
}
th {
  background: #f6f8fa; font-weight: 600;
  border-bottom: 2px solid #d0d7de;
}
td.num { text-align: right; font-variant-numeric: tabular-nums; }
.tag {
  display: inline-block; padding: 2px 8px; border-radius: 10px;
  font-size: 11px; font-weight: 600;
}
.tag-nn { background: #ddf4ff; color: #0969da; }
.tag-lk { background: #fff8c5; color: #9a6700; }
.tag-skip { background: #ffebe9; color: #cf222e; }
.formula {
  font-family: "SF Mono", Menlo, Consolas, monospace;
  font-size: 12px; color: #1f2328;
  background: #f6f8fa; padding: 1px 6px; border-radius: 3px;
}
.note {
  margin-top: 16px; padding: 12px 14px;
  background: #fff8c5; border-left: 4px solid #9a6700;
  border-radius: 4px; font-size: 12px; color: #57606a;
}
footer {
  margin-top: 32px; padding-top: 12px;
  border-top: 1px solid #d0d7de;
  font-size: 11px; color: #57606a;
}
.method-cell { white-space: nowrap; }
"""


def _tag(method: str) -> str:
    """method에 따른 태그 HTML 생성."""
    if method == "nn_v2":
        return '<span class="tag tag-nn">NN_v2</span>'
    if method == "lookup_category_mean":
        return '<span class="tag tag-lk">lookup</span>'
    return '<span class="tag tag-skip">skip</span>'


def _fmt(x: float, digits: int = 2) -> str:
    """NaN 안전 포맷."""
    if x != x:  # NaN check
        return "—"
    return f"{x:.{digits}f}"


def render_html(
    recipe: RecipeBundle,
    results: list[IngredientResult],
    n: int,
    out_path: Path,
) -> None:
    """HTML 리포트를 out_path에 저장.

    Args:
        recipe: 입력 1인분 레시피.
        results: 추론 결과.
        n: 목표 인원수.
        out_path: 출력 .html 경로.
    """
    nn_cnt = sum(1 for r in results if r.method == "nn_v2")
    lk_cnt = sum(1 for r in results if r.method == "lookup_category_mean")
    skip_cnt = sum(1 for r in results if r.method == "skip")
    total_base = sum(r.base for r in results)
    total_scaled = sum(r.scaled for r in results if r.scaled == r.scaled)

    rows_html = []
    for i, r in enumerate(results, 1):
        rows_html.append(f"""
      <tr>
        <td class="num">{i}</td>
        <td>{html.escape(r.name)}</td>
        <td>{html.escape(r.category)}</td>
        <td class="num">{_fmt(r.base)}</td>
        <td class="method-cell">{_tag(r.method)} {html.escape(r.reason)}</td>
        <td class="num">{_fmt(r.b_value, 4)}</td>
        <td><span class="formula">{html.escape(r.formula_str)}</span></td>
        <td class="num"><b>{_fmt(r.scaled)}</b></td>
      </tr>""")

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    doc = f"""<!doctype html>
<html lang="ko">
<head>
  <meta charset="UTF-8">
  <title>OptiMeal 데모 — {html.escape(recipe.menu_name)} × N={n}</title>
  <style>{CSS}</style>
</head>
<body>
  <header>
    <h1>OptiMeal 통합 데모 — 모듈 1 → 모듈 2 → N인분 스케일링</h1>
    <div class="meta">
      ADR-007 NN_v2 + lookup 하이브리드 ·
      기말발표용 (1-5월 작업 증명) ·
      생성 {ts}
    </div>
  </header>

  <h2>입력</h2>
  <div class="summary-grid">
    <div class="summary-card">
      <div class="label">메뉴 ({html.escape(recipe.recipe_id)}{" · 매칭됨" if recipe.matched_in_dfB else ""})</div>
      <div class="value">{html.escape(recipe.menu_name)}</div>
    </div>
    <div class="summary-card">
      <div class="label">조리 분류</div>
      <div class="value">{html.escape(recipe.group_type_kor)} / {html.escape(recipe.cooking_method)}</div>
    </div>
    <div class="summary-card">
      <div class="label">목표 인원수</div>
      <div class="value">N = {n}</div>
    </div>
    <div class="summary-card">
      <div class="label">1인분 재료 / 총량</div>
      <div class="value">{len(results)}개 / {_fmt(recipe.total_base_g, 0)} g</div>
    </div>
  </div>

  <h2>분기 요약</h2>
  <div class="summary-grid">
    <div class="summary-card">
      <div class="label">NN_v2 (학습 셀)</div>
      <div class="value">{nn_cnt}</div>
    </div>
    <div class="summary-card">
      <div class="label">lookup (미커버 셀)</div>
      <div class="value">{lk_cnt}</div>
    </div>
    <div class="summary-card">
      <div class="label">skip</div>
      <div class="value">{skip_cnt}</div>
    </div>
    <div class="summary-card">
      <div class="label">총 투입량 (g)</div>
      <div class="value">{_fmt(total_base)} → {_fmt(total_scaled)}</div>
    </div>
  </div>

  <h2>재료별 계산 과정</h2>
  <table>
    <thead>
      <tr>
        <th>#</th><th>재료명</th><th>카테고리</th>
        <th class="num">1인분 (g)</th><th>분기 사유</th>
        <th class="num">b</th><th>계산식</th>
        <th class="num">N인분 (g)</th>
      </tr>
    </thead>
    <tbody>{''.join(rows_html)}
    </tbody>
  </table>

  <h2>해설 — 분기 규칙 (ADR-007)</h2>
  <ol>
    <li><b>입력</b>: 1인분 레시피의 각 재료를
        <code>(group_type, ingredient_category, base_amount)</code> 3-튜플로 표현.</li>
    <li><b>셀 판정</b>: <code>(group_type, ingredient_category)</code> 조합이
        <code>scaling_coefficients.csv</code>의 <code>nutritionist_feedback</code>
        307행에 정의되어 있는가?
        <ul>
          <li><b>있음 → NN_v2</b>: per-slot one-hot feature를 DeepSets-context 모델에
              입력하여 <code>log(Y)</code>를 추론. 학습 시 노이즈 σ=0.05.</li>
          <li><b>없음 → lookup</b>: 같은 카테고리의 다른 셀들의 b 평균
              (<code>category_mean</code>)을 사용하여
              <code>Y = base × N^b</code>로 계산.</li>
        </ul>
    </li>
    <li><b>출력</b>: 재료별 g 단위 + 사용 분기·b·식을 함께 보고.</li>
  </ol>

  <div class="note">
    <b>면책</b>:
    학습 타겟 <code>Y = base × N^(b_feedback + ε)</code>는 식약처 1인분 데이터에
    영양사 50건 5회차 피드백을 적용한 <b>합성치</b>이다.
    20년 전 대규모 실측 데이터(df_B의 Y)는 현재 기준 영양사 피드백을 반영할 수 없는
    구조적 한계가 있어 학습 타겟으로 사용하지 않았다.
    절대 MAPE 수치는 노이즈 σ에 의존하며, 본 데모는 분기 동작과 계산 과정의
    시연을 목적으로 한다.
  </div>

  <footer>
    OptiMeal · 비선형 AI 스케일링 모델 · 기말발표 데모 (For_finalexam) ·
    관련 ADR: §ADR-001 (ratio 정의), §ADR-006 (NN 채택), §ADR-007 (하이브리드)
  </footer>
</body>
</html>
"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(doc, encoding="utf-8")
