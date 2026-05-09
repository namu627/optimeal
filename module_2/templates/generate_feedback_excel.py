"""
generate_feedback_excel.py
==========================
Rule-based 엔진 결과를 영양사 피드백용 Excel 파일로 출력한다.

CLI:
    python generate_feedback_excel.py recipe.json
    python generate_feedback_excel.py recipe.json --output /path/to/folder

레시피 JSON 구조:
    {
      "recipe_id": 201,
      "recipe_name": "제육볶음",
      "cooking_method": "볶음",
      "group_type": "dry_heat",        ← 'dry_heat' | 'moist_heat' | 'no_heat'
      "base_serving_n": 1,
      "target_serving_n": 100,
      "feedback_round": 1,             ← 1~5
      "ingredients": [
        {"name": "돼지고기", "category": "주재료", "base_amount_g": 80.0},
        {"name": "고추장",   "category": "양념류", "base_amount_g": 20.0}
      ]
    }

    ※ scaled_amount_g 없으면 Rule-based 엔진으로 자동 계산.
    ※ sample_recipe.json 참고.

생성 파일:
    feedback_R01_201_제육볶음_20260504.xlsx
    ├ Sheet "레시피"      : 스케일링 결과 (읽기 전용, 영양사 참조용)
    └ Sheet "영양사 평가" : 영양사가 작성하는 시트

─────────────────────────────────────────────────────────────────────────────
레시피 선정 기준 (ADR-002 v3 + feedback_rubric_v1.0.md §5)
─────────────────────────────────────────────────────────────────────────────
회차별 조리방법 제약 (각 10개):
  Round 1 (5/4):  볶음                → group_type = dry_heat
  Round 2 (5/8):  구이·부침 + 끓이기  → group_type = dry_heat 또는 moist_heat
  Round 3 (5/12): 찜·조림 + 비가열   → group_type = moist_heat 또는 no_heat
  Round 4 (5/15): 혼합 조리법         → 복수 group_type 레시피 (secondary_method 존재)
  Round 5 (5/19): 전체 재검증         → 이전 회차 대표 레시피 각 2~3개 (총 10개)

재료 구성 제약 (b값 업데이트 범위 최대화):
  - 5개 ingredient_category 모두 포함하는 레시피 우선
    (주재료·부재료·양념류·수분류·유지류)
  - 최소 3개 category 포함 필수
  - 양념류 반드시 포함 (b 차이가 가장 큰 카테고리, b≈0.60~0.65)

매칭 품질 제약:
  - composite_score ≥ 0.7 자동태깅 쌍 우선
  - 0.6~0.7 수동검토 쌍은 is_manually_verified=True인 경우만 허용

중복 금지:
  - 이전 회차에서 사용한 recipe_id 제외
  - 동일 메뉴명 반복 금지 (예: 김치찌개 Round 2·3 중복 불가)

선정 도우미 함수: select_recipes_for_round() 참고
─────────────────────────────────────────────────────────────────────────────

기준 문서: ADR-002 v3, ADR-003, feedback_rubric_v1.0.md, feedback_json_schema.md
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional

import openpyxl
from openpyxl.styles import Alignment, Border, Font, PatternFill, Protection, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

# src/engine 경로 추가 (scale_recipe 임포트)
_ENGINE_DIR = Path(__file__).parent.parent / "src" / "engine"
if str(_ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(_ENGINE_DIR))

from fallback import scale_recipe  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 레이아웃 상수 — parse_feedback_excel.py와 반드시 동기화할 것
# ─────────────────────────────────────────────────────────────────

# Sheet1 "레시피"
S1_TITLE_ROW = 1
S1_META: dict[str, tuple[int, str]] = {  # key: (행, 레이블)
    "recipe_name":    (3,  "레시피명"),
    "cooking_method": (4,  "조리방법"),
    "target_serving": (5,  "목표 인원수"),
    "feedback_round": (6,  "피드백 회차"),
    "recipe_id":      (7,  "레시피 ID"),
    "generated_date": (8,  "생성일"),
}
S1_ING_HEADER_ROW = 10
S1_ING_START_ROW  = 11
# Sheet1 열: A=No, B=재료명, C=카테고리, D=1인분(g), E=N인분 계산량(g), F=신뢰도
S1_COL_NO     = 1
S1_COL_NAME   = 2
S1_COL_CAT    = 3
S1_COL_BASE   = 4
S1_COL_SCALED = 5
S1_COL_CONF   = 6

# Sheet2 "영양사 평가"
S2_TITLE_ROW       = 1
S2_EVAL_NAME_ROW   = 4
S2_EVAL_ID_ROW     = 5
S2_EVAL_DATE_ROW   = 6
S2_RUBRIC_HDR_ROW  = 9
S2_TASTE_ROW       = 10
S2_FEASIBILITY_ROW = 11
S2_FIELD_ROW       = 12
S2_AVG_ROW         = 14
S2_RATING_ROW      = 15
S2_ING_SECTION_ROW = 17   # "▶ 재료별 수정 의견" 섹션 헤더
S2_ING_HEADER_ROW  = 18
S2_ING_START_ROW   = 19
# Sheet2 재료 열: A=No, B=재료명, C=카테고리, D=계산량, E=조정량(입력), F=최종량(formula), G=비고
S2_COL_NO     = 1
S2_COL_NAME   = 2
S2_COL_CAT    = 3
S2_COL_SCALED = 4
S2_COL_ADJ    = 5   # 영양사 입력 (숫자: 양수=추가, 음수=감소, 0=변경없음)
S2_COL_FINAL  = 6   # 자동계산: =D+E
S2_COL_NOTE   = 7
S2_COMMENT_OFFSET = 3   # 재료 마지막 행 + 3행 뒤에 종합의견 섹션

# ─────────────────────────────────────────────────────────────────
# 통합 시트 앵커 상수 — parse_feedback_excel.py와 반드시 동기화할 것
# 앵커: 통합 시트 A열에 기록되는 섹션 헤더 문자열. startswith() 로 탐색.
# ─────────────────────────────────────────────────────────────────
ANCHOR_SCALING   = "▶ 스케일링 결과"
ANCHOR_EVAL_INFO = "▶ 평가자 정보"
ANCHOR_RUBRIC    = "▶ 루브릭 평가"
ANCHOR_ING_ADJ   = "▶ 재료별 수정 의견"
ANCHOR_COMMENT   = "▶ 종합 의견"

_C_LAST = 7   # 통합 시트 사용 열 수 (A:G)

# ─────────────────────────────────────────────────────────────────
# 스타일 헬퍼
# ─────────────────────────────────────────────────────────────────

def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", fgColor=hex_color)


FILL_DARK_BLUE  = _fill("1F4E79")
FILL_MID_BLUE   = _fill("2E75B6")
FILL_LIGHT_BLUE = _fill("D6E4F0")
FILL_INPUT      = _fill("FFFACD")   # 레몬 노랑 — 영양사 입력 셀
FILL_FORMULA    = _fill("F5F5F5")   # 연회색 — 자동계산/읽기전용
FILL_HIGH       = _fill("C6EFCE")   # 연초록 — 신뢰도 high
FILL_MEDIUM     = _fill("FFEB9C")   # 연노랑 — 신뢰도 medium
FILL_LOW        = _fill("FFCC99")   # 연주황 — 신뢰도 low
CONF_FILLS: dict[str, PatternFill] = {
    "high": FILL_HIGH, "medium": FILL_MEDIUM, "low": FILL_LOW,
}

_THIN   = Side(style="thin")
BORDER  = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
ALIGN_C = Alignment(horizontal="center", vertical="center", wrap_text=True)
ALIGN_L = Alignment(horizontal="left",   vertical="center", wrap_text=True)
ALIGN_T = Alignment(horizontal="left",   vertical="top",    wrap_text=True)


def _w(ws, row: int, col: int, value=None, fill=None, font=None,
       align=None, fmt: str = None) -> None:
    """셀에 값·스타일을 한 번에 적용하는 내부 헬퍼."""
    c = ws.cell(row=row, column=col)
    if value is not None:
        c.value = value
    if fill:
        c.fill = fill
    if font:
        c.font = font
    c.alignment = align or ALIGN_L
    if fmt:
        c.number_format = fmt
    c.border = BORDER


# ─────────────────────────────────────────────────────────────────
# Sheet 1: 레시피 (읽기 전용 참조 시트)
# ─────────────────────────────────────────────────────────────────

def _build_recipe_sheet(ws, recipe: dict) -> None:
    """Sheet 1 "레시피"를 채운다."""
    n_col   = S1_COL_CONF
    n_col_l = get_column_letter(n_col)
    ings    = recipe["ingredients"]
    n       = recipe["target_serving_n"]

    # 제목 행
    ws.merge_cells(f"A{S1_TITLE_ROW}:{n_col_l}{S1_TITLE_ROW}")
    _w(ws, S1_TITLE_ROW, 1, "OptiMeal — 영양사 피드백 레시피 (Rule-based 엔진 계산 결과)",
       fill=FILL_DARK_BLUE, font=Font(bold=True, color="FFFFFF", size=13), align=ALIGN_C)
    ws.row_dimensions[S1_TITLE_ROW].height = 28

    # 메타 정보
    meta_values = {
        "recipe_name":    recipe["recipe_name"],
        "cooking_method": f"{recipe['cooking_method']}  ({recipe['group_type']})",
        "target_serving": f"{recipe.get('base_serving_n', 1)}인분 → {n}인분",
        "feedback_round": f"{recipe['feedback_round']}회차",
        "recipe_id":      recipe["recipe_id"],
        "generated_date": str(date.today()),
    }
    for key, (row, label) in S1_META.items():
        _w(ws, row, 1, label,               fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10))
        _w(ws, row, 2, meta_values[key],    font=Font(size=10))
        if key == "recipe_name":
            ws.merge_cells(f"B{row}:{n_col_l}{row}")

    # 재료 테이블 헤더
    hdr_font = Font(bold=True, color="FFFFFF", size=10)
    for col, label in [
        (S1_COL_NO,     "No"),
        (S1_COL_NAME,   "재료명"),
        (S1_COL_CAT,    "카테고리"),
        (S1_COL_BASE,   "1인분량 (g)"),
        (S1_COL_SCALED, f"{n}인분 계산량 (g)"),
        (S1_COL_CONF,   "신뢰도"),
    ]:
        _w(ws, S1_ING_HEADER_ROW, col, label,
           fill=FILL_MID_BLUE, font=hdr_font, align=ALIGN_C)
    ws.row_dimensions[S1_ING_HEADER_ROW].height = 22

    # 재료 데이터 행
    for i, ing in enumerate(ings):
        row  = S1_ING_START_ROW + i
        conf = ing.get("confidence", "low")
        rf   = CONF_FILLS.get(conf, FILL_LOW)
        nf   = Font(size=10)
        _w(ws, row, S1_COL_NO,     i + 1,                            fill=rf, font=nf, align=ALIGN_C)
        _w(ws, row, S1_COL_NAME,   ing["name"],                      fill=rf, font=nf)
        _w(ws, row, S1_COL_CAT,    ing["category"],                  fill=rf, font=nf, align=ALIGN_C)
        _w(ws, row, S1_COL_BASE,   round(ing["base_amount_g"], 1),   fill=rf, font=nf, align=ALIGN_C, fmt="#,##0.0")
        _w(ws, row, S1_COL_SCALED, round(ing["scaled_amount_g"], 1), fill=rf, font=nf, align=ALIGN_C, fmt="#,##0.0")
        _w(ws, row, S1_COL_CONF,   conf,                             fill=rf, font=nf, align=ALIGN_C)

    # 신뢰도 범례
    leg_row = S1_ING_START_ROW + len(ings) + 2
    ws.merge_cells(f"A{leg_row}:{n_col_l}{leg_row}")
    _w(ws, leg_row, 1,
       "신뢰도 색상 범례 — 초록(high, SE≤0.2) | 노랑(medium, 0.2<SE≤0.4) | 주황(low, SE>0.4)  ※ 신뢰도가 낮을수록 피드백 보정 효과가 큼",
       font=Font(size=9, italic=True))

    # 열 너비
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 11
    ws.column_dimensions["D"].width = 13
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 10

    # 시트 보호 (편집 방지, 비밀번호 없음)
    ws.protection.sheet = True


# ─────────────────────────────────────────────────────────────────
# Sheet 2: 영양사 평가 (영양사 작성 시트)
# ─────────────────────────────────────────────────────────────────

def _add_evaluator_section(ws, recipe: dict, n_col_l: str) -> None:
    """Sheet 2 평가자 정보 섹션."""
    ws.merge_cells(f"A3:{n_col_l}3")
    _w(ws, 3, 1, "▶  섹션 1 — 평가자 정보",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    for row, label in [
        (S2_EVAL_NAME_ROW, "평가자 이름"),
        (S2_EVAL_ID_ROW,   "평가자 ID (예: N001)"),
        (S2_EVAL_DATE_ROW, f"평가 날짜 (예: {date.today()})"),
    ]:
        _w(ws, row, 1, label, fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10))
        ws.merge_cells(f"B{row}:D{row}")
        _w(ws, row, 2, "", fill=FILL_INPUT, font=Font(size=10, color="000080"))


def _add_rubric_section(ws, n_col_l: str) -> None:
    """Sheet 2 루브릭 평가 섹션."""
    ws.merge_cells(f"A8:{n_col_l}8")
    _w(ws, 8, 1,
       "▶  섹션 2 — 루브릭 평가  (B열 노란 셀에 1~5 정수 입력)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    # 서브헤더
    for col, label in [(1, "평가 항목"), (2, "점수 (1~5)"), (3, "채점 기준 요약 (feedback_rubric_v1.0.md §2 참조)")]:
        _w(ws, S2_RUBRIC_HDR_ROW, col, label,
           fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10), align=ALIGN_C)
    ws.merge_cells(f"C{S2_RUBRIC_HDR_ROW}:{n_col_l}{S2_RUBRIC_HDR_ROW}")

    rubric_rows = [
        (S2_TASTE_ROW,       "① 맛·간 적절성",
         "5=즉시적용 / 4=미세조정(±5%) / 3=조미료 10~20% 수정 / 2=30%↑수정 / 1=전면재산출"),
        (S2_FEASIBILITY_ROW, "② 조리 실현 가능성",
         "5=완전실현 / 4=소폭조정 / 3=부분비현실 / 2=대규모변경 / 1=현장불가"),
        (S2_FIELD_ROW,       "③ 대량 조리 현장 적합성",
         "5=최적화 / 4=대부분적합 / 3=일부어려움 / 2=물리적 제약 발생 / 1=적용불가"),
    ]
    for row, label, guide in rubric_rows:
        _w(ws, row, 1, label, fill=FILL_FORMULA, font=Font(size=10))
        _w(ws, row, 2, None,  fill=FILL_INPUT,   font=Font(size=11, bold=True, color="000080"), align=ALIGN_C)
        ws.merge_cells(f"C{row}:{n_col_l}{row}")
        _w(ws, row, 3, guide, fill=FILL_FORMULA, font=Font(size=9, italic=True))

    # 구분선 행
    _w(ws, 13, 1, "")

    # 평균·판정 (Excel 수식)
    taste_r = f"B{S2_TASTE_ROW}"
    feas_r  = f"B{S2_FEASIBILITY_ROW}"
    field_r = f"B{S2_FIELD_ROW}"
    avg_r   = f"B{S2_AVG_ROW}"

    _w(ws, S2_AVG_ROW, 1, "평균 점수 (자동계산)", fill=FILL_FORMULA, font=Font(bold=True, size=10))
    c_avg = ws.cell(row=S2_AVG_ROW, column=2,
                    value=f"=IF(AND(ISNUMBER({taste_r}),ISNUMBER({feas_r}),ISNUMBER({field_r})),"
                          f"ROUND(({taste_r}+{feas_r}+{field_r})/3,2),\"미입력\")")
    c_avg.fill = FILL_FORMULA; c_avg.font = Font(bold=True, size=11)
    c_avg.border = BORDER; c_avg.alignment = ALIGN_C

    _w(ws, S2_RATING_ROW, 1, "종합 판정 (자동계산)", fill=FILL_FORMULA, font=Font(bold=True, size=10))
    c_rate = ws.cell(row=S2_RATING_ROW, column=2,
                     value=f'=IF({avg_r}="미입력","",IF({avg_r}>=4,"상용가능",IF({avg_r}>=2.5,"수정필요","부적합")))')
    c_rate.fill = FILL_FORMULA; c_rate.font = Font(bold=True, size=11)
    c_rate.border = BORDER; c_rate.alignment = ALIGN_C

    # 점수 드롭다운 유효성
    dv = DataValidation(
        type="whole", operator="between", formula1="1", formula2="5",
        showErrorMessage=True,
        error="1~5 사이의 정수를 입력하세요.",
        errorTitle="입력 오류",
    )
    ws.add_data_validation(dv)
    for row in [S2_TASTE_ROW, S2_FEASIBILITY_ROW, S2_FIELD_ROW]:
        dv.add(ws.cell(row=row, column=2))


def _add_ingredient_section(ws, recipe: dict, n_col_l: str) -> None:
    """Sheet 2 재료별 수정 의견 섹션."""
    ings = recipe["ingredients"]
    n    = recipe["target_serving_n"]

    ws.merge_cells(f"A{S2_ING_SECTION_ROW}:{n_col_l}{S2_ING_SECTION_ROW}")
    _w(ws, S2_ING_SECTION_ROW, 1,
       f"▶  섹션 3 — 재료별 수정 의견  "
       f"(E열 노란 셀에 숫자 입력: 양수=추가 필요(+g), 음수=감소 필요(-g), 0=변경없음)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    # 컬럼 헤더
    hdr_items = [
        (S2_COL_NO,     "No"),
        (S2_COL_NAME,   "재료명"),
        (S2_COL_CAT,    "카테고리"),
        (S2_COL_SCALED, f"{n}인분\n계산량 (g)"),
        (S2_COL_ADJ,    "조정량 입력\n(g, 양수/음수/0)"),
        (S2_COL_FINAL,  "최종량\n(자동계산, g)"),
        (S2_COL_NOTE,   "비고 (선택)"),
    ]
    for col, label in hdr_items:
        _w(ws, S2_ING_HEADER_ROW, col, label,
           fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=10), align=ALIGN_C)
    ws.row_dimensions[S2_ING_HEADER_ROW].height = 36

    # 재료 데이터
    adj_col_l   = get_column_letter(S2_COL_ADJ)
    scaled_col_l = get_column_letter(S2_COL_SCALED)
    for i, ing in enumerate(ings):
        row      = S2_ING_START_ROW + i
        scaled_g = round(ing.get("scaled_amount_g", 0), 1)

        _w(ws, row, S2_COL_NO,     i + 1,          fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C)
        _w(ws, row, S2_COL_NAME,   ing["name"],     fill=FILL_FORMULA, font=Font(size=10))
        _w(ws, row, S2_COL_CAT,    ing["category"], fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C)
        _w(ws, row, S2_COL_SCALED, scaled_g,        fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C, fmt="#,##0.0")
        _w(ws, row, S2_COL_ADJ,    0,               fill=FILL_INPUT,   font=Font(size=11, bold=True, color="000080"), align=ALIGN_C, fmt="#,##0.0")

        # 최종량 수식: =SCALED + ADJ
        c_final = ws.cell(
            row=row, column=S2_COL_FINAL,
            value=f"=IF(ISNUMBER({adj_col_l}{row}),{scaled_col_l}{row}+{adj_col_l}{row},{scaled_col_l}{row})"
        )
        c_final.fill = FILL_FORMULA; c_final.font = Font(size=10)
        c_final.border = BORDER; c_final.alignment = ALIGN_C; c_final.number_format = "#,##0.0"

        _w(ws, row, S2_COL_NOTE, "", fill=FILL_INPUT, font=Font(size=10))


def _add_comment_section(ws, n_ings: int, n_col_l: str) -> None:
    """Sheet 2 종합 의견 섹션 (재료 마지막 행 + S2_COMMENT_OFFSET 이후)."""
    header_row = S2_ING_START_ROW + n_ings + S2_COMMENT_OFFSET
    text_row   = header_row + 1

    ws.merge_cells(f"A{header_row}:{n_col_l}{header_row}")
    _w(ws, header_row, 1, "▶  섹션 4 — 종합 의견 (자유 기술)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    ws.merge_cells(f"A{text_row}:{n_col_l}{text_row + 2}")
    c = ws.cell(row=text_row, column=1, value="")
    c.fill = FILL_INPUT; c.border = BORDER
    c.alignment = ALIGN_T; c.font = Font(size=10)
    ws.row_dimensions[text_row].height = 90


def _build_evaluation_sheet(ws, recipe: dict) -> None:
    """Sheet 2 "영양사 평가" 전체를 구성한다."""
    n_col_l = get_column_letter(S2_COL_NOTE)

    # 제목
    ws.merge_cells(f"A{S2_TITLE_ROW}:{n_col_l}{S2_TITLE_ROW}")
    _w(ws, S2_TITLE_ROW, 1,
       f"영양사 평가 시트 — {recipe['recipe_name']}  ({recipe['feedback_round']}회차, "
       f"{recipe['target_serving_n']}인분)",
       fill=FILL_DARK_BLUE, font=Font(bold=True, color="FFFFFF", size=13), align=ALIGN_C)
    ws.row_dimensions[S2_TITLE_ROW].height = 28

    _add_evaluator_section(ws, recipe, n_col_l)
    _add_rubric_section(ws, n_col_l)
    _add_ingredient_section(ws, recipe, n_col_l)
    _add_comment_section(ws, len(recipe["ingredients"]), n_col_l)

    # 열 너비
    ws.column_dimensions["A"].width = 5
    ws.column_dimensions["B"].width = 20
    ws.column_dimensions["C"].width = 12
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 16
    ws.column_dimensions["G"].width = 22


# ─────────────────────────────────────────────────────────────────
# 공개 API
# ─────────────────────────────────────────────────────────────────

def generate(recipe: dict, output_dir: Optional[Path] = None) -> Path:
    """
    레시피 dict → 영양사 피드백 Excel 생성.

    scaled_amount_g가 없으면 Rule-based 엔진(scale_recipe)으로 자동 계산.

    Args:
        recipe: 레시피 데이터 dict
        output_dir: 저장 폴더. None이면 현재 디렉토리.

    Returns:
        생성된 .xlsx 파일 Path
    """
    # scaled_amount_g 자동 계산
    if any("scaled_amount_g" not in ing for ing in recipe["ingredients"]):
        scaled = scale_recipe(
            ingredients=recipe["ingredients"],
            group_type=recipe["group_type"],
            target_serving_n=recipe["target_serving_n"],
        )
        for orig, s in zip(recipe["ingredients"], scaled):
            orig.update(s)

    wb  = openpyxl.Workbook()
    ws1 = wb.active
    ws1.title = "레시피"
    ws2 = wb.create_sheet("영양사 평가")

    _build_recipe_sheet(ws1, recipe)
    _build_evaluation_sheet(ws2, recipe)

    # 기본 활성 시트: 영양사 평가
    wb.active = ws2

    safe_name = recipe["recipe_name"].replace(" ", "_")
    fname     = (
        f"feedback_R{recipe['feedback_round']:02d}"
        f"_{recipe['recipe_id']}"
        f"_{safe_name}"
        f"_{date.today().strftime('%Y%m%d')}.xlsx"
    )
    out_dir  = output_dir or Path(".")
    out_path = out_dir / fname
    wb.save(out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────
# 통합 시트 (스케일링 결과 + 영양사 평가 합본) — generate_single_file용
# ─────────────────────────────────────────────────────────────────

def _make_sheet_name(recipe: dict) -> str:
    """통합 파일의 시트 이름 생성. Excel 시트명 최대 31자 제한."""
    rnd    = recipe["feedback_round"]
    rid    = str(recipe["recipe_id"])
    safe   = recipe["recipe_name"].replace(" ", "")
    prefix = f"R{rnd}_{rid}_"
    return (prefix + safe)[:31]


def _build_combined_sheet(ws, recipe: dict) -> None:
    """스케일링 결과 + 영양사 평가를 하나의 시트에 구성한다.

    레이아웃 (A:G = 7열):
      Rows 1-6    : 제목·메타 (고정)
      Row  7      : ANCHOR_SCALING (스케일링 결과 섹션)
      Row  8      : 재료 테이블 헤더
      Rows 9..    : 재료 데이터 (n_ings행)
      ...         : 평가자 정보·루브릭·재료수정·종합의견 섹션 (n 에 따라 동적)

    입력 셀 (노란색)은 Protection(locked=False) → 시트 보호 활성 상태에서도 편집 가능.
    """
    ings   = recipe["ingredients"]
    n      = len(ings)
    N      = recipe["target_serving_n"]
    ncl    = get_column_letter(_C_LAST)   # "G"

    # ── 동적 행 위치 계산 ──────────────────────────────────
    SC_ANCHOR  = 7
    ING_HDR    = 8
    ING_START  = 9                        # ~ ING_START + n - 1
    LEGEND_ROW = ING_START + n + 1

    EI_ANCHOR  = LEGEND_ROW + 2
    EI_NAME    = EI_ANCHOR + 1
    EI_ID      = EI_ANCHOR + 2
    EI_DATE    = EI_ANCHOR + 3

    RB_ANCHOR  = EI_DATE + 2
    RB_HDR     = RB_ANCHOR + 1
    RB_TASTE   = RB_ANCHOR + 2
    RB_FEAS    = RB_ANCHOR + 3
    RB_FIELD   = RB_ANCHOR + 4
    RB_AVG     = RB_ANCHOR + 6
    RB_RATING  = RB_ANCHOR + 7

    IA_ANCHOR  = RB_RATING + 2
    IA_HDR     = IA_ANCHOR + 1
    IA_START   = IA_ANCHOR + 2            # ~ IA_START + n - 1

    CM_ANCHOR  = IA_START + n + 1
    CM_START   = CM_ANCHOR + 1

    # ── 제목 행 ───────────────────────────────────────────
    ws.merge_cells(f"A1:{ncl}1")
    _w(ws, 1, 1,
       f"OptiMeal 영양사 피드백 — {recipe['recipe_name']}  "
       f"({recipe['feedback_round']}회차, {N}인분)",
       fill=FILL_DARK_BLUE, font=Font(bold=True, color="FFFFFF", size=13), align=ALIGN_C)
    ws.row_dimensions[1].height = 28

    # ── 메타 정보 (rows 3–5) ─────────────────────────────
    meta_rows = [
        (3, "레시피명",    recipe["recipe_name"],
              "조리방법",  f"{recipe['cooking_method']}  ({recipe['group_type']})"),
        (4, "목표 인원수", f"{recipe.get('base_serving_n',1)}인분 → {N}인분",
              "피드백 회차", f"{recipe['feedback_round']}회차"),
        (5, "레시피 ID",   str(recipe["recipe_id"]),
              "생성일",    str(date.today())),
    ]
    for row, lbl1, val1, lbl2, val2 in meta_rows:
        _w(ws, row, 1, lbl1, fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10))
        ws.merge_cells(f"B{row}:D{row}")
        _w(ws, row, 2, val1, font=Font(size=10))
        _w(ws, row, 5, lbl2, fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10))
        ws.merge_cells(f"F{row}:{ncl}{row}")
        _w(ws, row, 6, val2, font=Font(size=10))

    # ── SECTION 1: 스케일링 결과 ─────────────────────────
    ws.merge_cells(f"A{SC_ANCHOR}:{ncl}{SC_ANCHOR}")
    _w(ws, SC_ANCHOR, 1, ANCHOR_SCALING,
       fill=FILL_DARK_BLUE, font=Font(bold=True, color="FFFFFF", size=11), align=ALIGN_C)
    ws.row_dimensions[SC_ANCHOR].height = 22

    hf = Font(bold=True, color="FFFFFF", size=10)
    for col, lbl in [(1,"No"),(2,"재료명"),(3,"카테고리"),
                     (4,"1인분량 (g)"),(5,f"{N}인분 계산량 (g)"),(6,"신뢰도")]:
        _w(ws, ING_HDR, col, lbl, fill=FILL_MID_BLUE, font=hf, align=ALIGN_C)
    ws.row_dimensions[ING_HDR].height = 22

    for i, ing in enumerate(ings):
        row  = ING_START + i
        conf = ing.get("confidence", "low")
        rf   = CONF_FILLS.get(conf, FILL_LOW)
        nf   = Font(size=10)
        _w(ws, row, 1, i+1,                             fill=rf, font=nf, align=ALIGN_C)
        _w(ws, row, 2, ing["name"],                     fill=rf, font=nf)
        _w(ws, row, 3, ing["category"],                 fill=rf, font=nf, align=ALIGN_C)
        _w(ws, row, 4, round(ing["base_amount_g"], 1),  fill=rf, font=nf, align=ALIGN_C, fmt="#,##0.0")
        _w(ws, row, 5, round(ing["scaled_amount_g"],1), fill=rf, font=nf, align=ALIGN_C, fmt="#,##0.0")
        _w(ws, row, 6, conf,                            fill=rf, font=nf, align=ALIGN_C)

    ws.merge_cells(f"A{LEGEND_ROW}:{ncl}{LEGEND_ROW}")
    _w(ws, LEGEND_ROW, 1,
       "신뢰도 범례 — 초록(high, SE≤0.2) | 노랑(medium) | 주황(low)  ※ 낮을수록 피드백 보정 효과 큼",
       font=Font(size=9, italic=True))

    # ── SECTION 2: 평가자 정보 ───────────────────────────
    ws.merge_cells(f"A{EI_ANCHOR}:{ncl}{EI_ANCHOR}")
    _w(ws, EI_ANCHOR, 1, ANCHOR_EVAL_INFO,
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    for row, lbl in [(EI_NAME, "평가자 이름"),
                     (EI_ID,   "평가자 ID (예: N001)"),
                     (EI_DATE, f"평가 날짜 (예: {date.today()})")]:
        _w(ws, row, 1, lbl, fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10))
        ws.merge_cells(f"B{row}:D{row}")
        c = ws.cell(row=row, column=2, value="")
        c.fill = FILL_INPUT; c.font = Font(size=10, color="000080")
        c.border = BORDER; c.alignment = ALIGN_L
        c.protection = Protection(locked=False)

    # ── SECTION 3: 루브릭 평가 ───────────────────────────
    ws.merge_cells(f"A{RB_ANCHOR}:{ncl}{RB_ANCHOR}")
    _w(ws, RB_ANCHOR, 1,
       f"{ANCHOR_RUBRIC}  (B열 노란 셀에 1~5 정수 입력)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    for col, lbl in [(1,"평가 항목"),(2,"점수 (1~5)"),(3,"채점 기준 요약 (feedback_rubric_v1.0.md §2 참조)")]:
        _w(ws, RB_HDR, col, lbl, fill=FILL_LIGHT_BLUE, font=Font(bold=True, size=10), align=ALIGN_C)
    ws.merge_cells(f"C{RB_HDR}:{ncl}{RB_HDR}")

    rubric_items = [
        (RB_TASTE, "① 맛·간 적절성",
         "5=즉시적용 / 4=미세조정(±5%) / 3=조미료 10~20% 수정 / 2=30%↑수정 / 1=전면재산출"),
        (RB_FEAS,  "② 조리 실현 가능성",
         "5=완전실현 / 4=소폭조정 / 3=부분비현실 / 2=대규모변경 / 1=현장불가"),
        (RB_FIELD, "③ 대량 조리 현장 적합성",
         "5=최적화 / 4=대부분적합 / 3=일부어려움 / 2=물리적 제약 / 1=적용불가"),
    ]
    for row, lbl, guide in rubric_items:
        _w(ws, row, 1, lbl, fill=FILL_FORMULA, font=Font(size=10))
        c = ws.cell(row=row, column=2, value=None)
        c.fill = FILL_INPUT; c.font = Font(size=11, bold=True, color="000080")
        c.border = BORDER; c.alignment = ALIGN_C
        c.protection = Protection(locked=False)
        ws.merge_cells(f"C{row}:{ncl}{row}")
        _w(ws, row, 3, guide, fill=FILL_FORMULA, font=Font(size=9, italic=True))

    dv = DataValidation(
        type="whole", operator="between", formula1="1", formula2="5",
        showErrorMessage=True, error="1~5 사이의 정수를 입력하세요.", errorTitle="입력 오류",
    )
    ws.add_data_validation(dv)
    for row in [RB_TASTE, RB_FEAS, RB_FIELD]:
        dv.add(ws.cell(row=row, column=2))

    t_r = f"B{RB_TASTE}"; f_r = f"B{RB_FEAS}"; fi_r = f"B{RB_FIELD}"
    avg_r = f"B{RB_AVG}"

    _w(ws, RB_AVG, 1, "평균 점수 (자동계산)", fill=FILL_FORMULA, font=Font(bold=True, size=10))
    ca = ws.cell(row=RB_AVG, column=2,
                 value=f"=IF(AND(ISNUMBER({t_r}),ISNUMBER({f_r}),ISNUMBER({fi_r})),"
                       f"ROUND(({t_r}+{f_r}+{fi_r})/3,2),\"미입력\")")
    ca.fill = FILL_FORMULA; ca.font = Font(bold=True, size=11)
    ca.border = BORDER; ca.alignment = ALIGN_C

    _w(ws, RB_RATING, 1, "종합 판정 (자동계산)", fill=FILL_FORMULA, font=Font(bold=True, size=10))
    cr = ws.cell(row=RB_RATING, column=2,
                 value=f'=IF({avg_r}="미입력","",IF({avg_r}>=4,"상용가능",IF({avg_r}>=2.5,"수정필요","부적합")))')
    cr.fill = FILL_FORMULA; cr.font = Font(bold=True, size=11)
    cr.border = BORDER; cr.alignment = ALIGN_C

    # ── SECTION 4: 재료별 수정 의견 ─────────────────────
    ws.merge_cells(f"A{IA_ANCHOR}:{ncl}{IA_ANCHOR}")
    _w(ws, IA_ANCHOR, 1,
       f"{ANCHOR_ING_ADJ}  (E열 노란 셀: 양수=추가(+g), 음수=감소(-g), 0=변경없음)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    for col, lbl in [(1,"No"),(2,"재료명"),(3,"카테고리"),
                     (4,f"{N}인분 계산량 (g)"),(5,"조정량 입력\n(g)"),(6,"최종량\n(자동계산, g)"),(7,"비고")]:
        _w(ws, IA_HDR, col, lbl, fill=FILL_MID_BLUE,
           font=Font(bold=True, color="FFFFFF", size=10), align=ALIGN_C)
    ws.row_dimensions[IA_HDR].height = 36

    for i, ing in enumerate(ings):
        row      = IA_START + i
        scaled_g = round(ing.get("scaled_amount_g", 0), 1)
        _w(ws, row, 1, i+1,            fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C)
        _w(ws, row, 2, ing["name"],    fill=FILL_FORMULA, font=Font(size=10))
        _w(ws, row, 3, ing["category"],fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C)
        _w(ws, row, 4, scaled_g,       fill=FILL_FORMULA, font=Font(size=10), align=ALIGN_C, fmt="#,##0.0")
        c_adj = ws.cell(row=row, column=5, value=0)
        c_adj.fill = FILL_INPUT; c_adj.font = Font(size=11, bold=True, color="000080")
        c_adj.border = BORDER; c_adj.alignment = ALIGN_C; c_adj.number_format = "#,##0.0"
        c_adj.protection = Protection(locked=False)
        c_fin = ws.cell(
            row=row, column=6,
            value=f"=IF(ISNUMBER(E{row}),D{row}+E{row},D{row})")
        c_fin.fill = FILL_FORMULA; c_fin.font = Font(size=10)
        c_fin.border = BORDER; c_fin.alignment = ALIGN_C; c_fin.number_format = "#,##0.0"
        c_note = ws.cell(row=row, column=7, value="")
        c_note.fill = FILL_INPUT; c_note.font = Font(size=10)
        c_note.border = BORDER; c_note.alignment = ALIGN_L
        c_note.protection = Protection(locked=False)

    # ── SECTION 5: 종합 의견 ────────────────────────────
    ws.merge_cells(f"A{CM_ANCHOR}:{ncl}{CM_ANCHOR}")
    _w(ws, CM_ANCHOR, 1, f"{ANCHOR_COMMENT} (자유 기술)",
       fill=FILL_MID_BLUE, font=Font(bold=True, color="FFFFFF", size=11))

    ws.merge_cells(f"A{CM_START}:{ncl}{CM_START+2}")
    c_cm = ws.cell(row=CM_START, column=1, value="")
    c_cm.fill = FILL_INPUT; c_cm.border = BORDER
    c_cm.alignment = ALIGN_T; c_cm.font = Font(size=10)
    c_cm.protection = Protection(locked=False)
    ws.row_dimensions[CM_START].height = 90

    # ── 열 너비 ──────────────────────────────────────────
    for col, w in [("A",5),("B",20),("C",12),("D",14),("E",16),("F",16),("G",22)]:
        ws.column_dimensions[col].width = w

    # ── 시트 보호 (입력 셀은 locked=False로 처리됨) ──────
    ws.protection.sheet = True


def generate_single_file(all_recipes: list[dict], output_path: Path) -> Path:
    """
    여러 레시피를 하나의 Excel 파일로 생성한다 (시트 1개 = 레시피 1개).

    각 시트에 스케일링 결과 + 영양사 평가 입력 영역이 통합되어 있다.
    시트 이름: "R{round}_{recipe_id}_{recipe_name_truncated}" (최대 31자)

    Args:
        all_recipes: 레시피 dict 목록 (generate()와 동일한 구조)
        output_path: 저장할 .xlsx 파일 경로

    Returns:
        생성된 .xlsx 파일 Path
    """
    wb = openpyxl.Workbook()
    wb.remove(wb.active)   # 기본 빈 시트 제거

    for recipe in all_recipes:
        # scaled_amount_g 자동 계산
        if any("scaled_amount_g" not in ing for ing in recipe["ingredients"]):
            scaled = scale_recipe(
                ingredients      = recipe["ingredients"],
                group_type       = recipe["group_type"],
                target_serving_n = recipe["target_serving_n"],
            )
            for orig, s in zip(recipe["ingredients"], scaled):
                orig.update(s)

        ws = wb.create_sheet(_make_sheet_name(recipe))
        _build_combined_sheet(ws, recipe)

    if wb.worksheets:
        wb.active = wb.worksheets[0]

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(output_path)
    return output_path


# ─────────────────────────────────────────────────────────────────
# 레시피 선정 도우미
# ─────────────────────────────────────────────────────────────────

# 회차별 허용 group_type (ADR-002 v3 + feedback_rubric_v1.0.md §5)
_ROUND_GROUP_TYPES: dict[int, list[str]] = {
    1: ["dry_heat"],
    2: ["dry_heat", "moist_heat"],
    3: ["moist_heat", "no_heat"],
    4: ["dry_heat", "moist_heat", "no_heat"],   # 혼합 조리법: 복수 group_type 레시피
    5: ["dry_heat", "moist_heat", "no_heat"],   # 전체 재검증: 제한 없음
}

# 필수 포함 카테고리
_REQUIRED_CATEGORIES = {"양념류"}
_MIN_CATEGORIES = 3
_ALL_CATEGORIES = {"주재료", "부재료", "양념류", "수분류", "유지류"}


def select_recipes_for_round(
    candidates: list[dict],
    round_no: int,
    used_recipe_ids: set | None = None,
    n: int = 10,
) -> tuple[list[dict], list[str]]:
    """피드백 회차에 맞는 레시피를 후보 목록에서 선정한다.

    Args:
        candidates: 레시피 dict 목록. 각 항목은 generate()에 넘기는 JSON 구조와 동일.
                    단, 추가 필드 허용:
                    - "composite_score" (float, 0~1): 매칭 유사도 점수
                    - "is_manually_verified" (bool): 수동 검수 여부
        round_no: 피드백 회차 (1~5).
        used_recipe_ids: 이전 회차에서 이미 사용한 recipe_id 집합. None이면 빈 집합.
        n: 최대 선정 수 (기본 10개).

    Returns:
        (selected, warnings)
        - selected: 선정된 레시피 목록 (우선순위 정렬)
        - warnings: 기준 미달 항목 경고 메시지 목록

    선정 우선순위:
        1순위: 5개 category 모두 포함 + composite_score ≥ 0.7
        2순위: 5개 category 모두 포함 + 수동 검수 확인 (0.6~0.7)
        3순위: 3개 이상 category 포함 + composite_score ≥ 0.7
        4순위: 3개 이상 category 포함 + 수동 검수 확인 (0.6~0.7)
        ※ 양념류 미포함 레시피는 모든 순위에서 제외
    """
    if round_no not in _ROUND_GROUP_TYPES:
        raise ValueError(f"round_no는 1~5여야 합니다. 받은 값: {round_no}")

    used_ids: set = used_recipe_ids or set()
    allowed_groups = _ROUND_GROUP_TYPES[round_no]
    warnings: list[str] = []

    def _priority(r: dict) -> int:
        """낮을수록 우선 선정."""
        cats = {ing["category"] for ing in r.get("ingredients", [])}
        n_cats = len(cats & _ALL_CATEGORIES)
        score  = r.get("composite_score", 0.0)
        verified = r.get("is_manually_verified", False)
        high_quality = score >= 0.7 or verified

        if n_cats == 5 and high_quality:
            return 1
        if n_cats == 5:
            return 2
        if n_cats >= _MIN_CATEGORIES and high_quality:
            return 3
        if n_cats >= _MIN_CATEGORIES:
            return 4
        return 99  # 기준 미달

    filtered: list[dict] = []
    for r in candidates:
        rid = r.get("recipe_id")

        # 중복 제외
        if rid in used_ids:
            continue

        # group_type 제약
        if r.get("group_type") not in allowed_groups:
            continue

        # Round 4: 혼합 조리법 레시피만 (secondary_method 존재 여부로 판별)
        if round_no == 4 and not r.get("has_secondary_method", False):
            continue

        # 양념류 필수
        cats = {ing["category"] for ing in r.get("ingredients", [])}
        if not _REQUIRED_CATEGORIES.issubset(cats):
            warnings.append(
                f"[제외] recipe_id={rid} '{r.get('recipe_name')}' — 양념류 미포함"
            )
            continue

        # 최소 카테고리 수
        n_cats = len(cats & _ALL_CATEGORIES)
        if n_cats < _MIN_CATEGORIES:
            warnings.append(
                f"[제외] recipe_id={rid} '{r.get('recipe_name')}'"
                f" — category {n_cats}개 < 최소 {_MIN_CATEGORIES}개"
            )
            continue

        filtered.append(r)

    # 우선순위 정렬
    filtered.sort(key=_priority)
    selected = filtered[:n]

    # 10개 미만 경고
    if len(selected) < n:
        warnings.append(
            f"[경고] 선정 {len(selected)}개 < 목표 {n}개 — "
            f"후보 부족 또는 기준 완화 필요 (composite_score 임계값 하향 검토)"
        )

    return selected, warnings


def main() -> None:
    parser = argparse.ArgumentParser(
        description="레시피 JSON → 영양사 피드백 Excel 생성 (sample_recipe.json 참고)"
    )
    parser.add_argument("recipe_json", help="레시피 JSON 파일 경로")
    parser.add_argument("--output", "-o", default=".", help="저장 폴더 (기본: 현재 디렉토리)")
    args = parser.parse_args()

    recipe_path = Path(args.recipe_json)
    if not recipe_path.exists():
        print(f"[오류] 파일 없음: {recipe_path}", file=sys.stderr)
        sys.exit(1)

    recipe = json.loads(recipe_path.read_text(encoding="utf-8"))
    out    = generate(recipe, output_dir=Path(args.output))
    print(f"[완료] Excel 생성: {out.resolve()}")
    print(f"       → '영양사 평가' 시트를 영양사에게 전달하세요.")


if __name__ == "__main__":
    main()
