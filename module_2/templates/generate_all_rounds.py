"""
generate_all_rounds.py
======================
영양사 피드백 5회차 전체 Excel 파일을 일괄 생성하고 전달·수령 일정을 출력한다.

CLI:
    python generate_all_rounds.py                        # 기본: feedback_excels/ 폴더에 저장
    python generate_all_rounds.py --output /path/dir     # 저장 위치 지정
    python generate_all_rounds.py --schedule-only        # 일정 출력만 (Excel 생성 없음)

레시피 선정 근거:
    - 회차별 조리방법: ADR-002 v3 + feedback_rubric_v1.0.md §5
    - 재료 구성: 양념류 필수 + 최소 3개 category
    - 데이터 출처: 매칭 확정 171쌍 중 회차별 기준에 맞는 대표 레시피

기준 문서: ADR-002 v3, feedback_rubric_v1.0.md
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# templates 및 engine 경로 추가
_TMPL_DIR   = Path(__file__).parent
_ENGINE_DIR = _TMPL_DIR.parent / "src" / "engine"
for _p in [str(_TMPL_DIR), str(_ENGINE_DIR)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

from generate_feedback_excel import generate_single_file  # noqa: E402

# ─────────────────────────────────────────────────────────────────
# 일정 상수 (feedback_rubric_v1.0.md §5)
# ─────────────────────────────────────────────────────────────────
# D-7: 후보 목록 텍스트 전달
# D-3: Excel 파일 생성 & 영양사 전달
# D-0: 피드백 수령 마감 (당일 평가)
# D+1: parse_feedback_excel.py 실행 → CSV 업데이트
SCHEDULE = [
    {
        "round":            1,
        "label":            "Round 1 — 볶음 (dry_heat)",
        "feedback_date":    "2026-05-04 (월)",
        "send_candidate":   "2026-04-27 (월)  ← D-7  ⚠ 3일 후",
        "send_excel":       "2026-05-01 (금)  ← D-3",
        "receive_deadline": "2026-05-04 (월)  ← D-0  피드백 수령 마감",
        "parse_date":       "2026-05-05 (화)  ← D+1  parse_feedback_excel.py 실행",
        "method":           "볶음",
        "group_types":      ["dry_heat"],
        "note":             "양념류 b 보정 주목적. 제1회차 — 기준 정립 단계.",
    },
    {
        "round":            2,
        "label":            "Round 2 — 구이·부침 + 끓이기",
        "feedback_date":    "2026-05-08 (금)",
        "send_candidate":   "2026-05-01 (금)  ← D-7",
        "send_excel":       "2026-05-05 (화)  ← D-3",
        "receive_deadline": "2026-05-08 (금)  ← D-0  피드백 수령 마감",
        "parse_date":       "2026-05-09 (토)  ← D+1  parse_feedback_excel.py 실행",
        "method":           "구이·부침 (dry_heat 5개) + 끓이기 (moist_heat 5개)",
        "group_types":      ["dry_heat", "moist_heat"],
        "note":             "건열/습열 각 5개. 수분류 b 보정 포함.",
    },
    {
        "round":            3,
        "label":            "Round 3 — 찜·조림 + 비가열 무침",
        "feedback_date":    "2026-05-12 (화)",
        "send_candidate":   "2026-05-05 (화)  ← D-7",
        "send_excel":       "2026-05-08 (금)  ← D-4 (D-3=토 → 전날 금요일로 앞당김)",
        "receive_deadline": "2026-05-12 (화)  ← D-0  피드백 수령 마감",
        "parse_date":       "2026-05-13 (수)  ← D+1  parse_feedback_excel.py 실행",
        "method":           "찜·조림 (moist_heat 6개) + 비가열 무침 (no_heat 4개)",
        "group_types":      ["moist_heat", "no_heat"],
        "note":             "⚠ 비가열 30쌍 한계 인지. 보정 폭 보수적 적용. 논문 한계 절 명시 의무.",
    },
    {
        "round":            4,
        "label":            "Round 4 — 혼합 조리법",
        "feedback_date":    "2026-05-15 (금)",
        "send_candidate":   "2026-05-08 (금)  ← D-7",
        "send_excel":       "2026-05-12 (화)  ← D-3",
        "receive_deadline": "2026-05-15 (금)  ← D-0  피드백 수령 마감",
        "parse_date":       "2026-05-16 (토)  ← D+1  parse_feedback_excel.py 실행",
        "method":           "혼합 조리법 (secondary_method 존재 레시피)",
        "group_types":      ["dry_heat", "moist_heat"],
        "note":             "볶음+찜, 끓이기+볶음 등 복합 보정. 복수 group_type 레시피.",
    },
    {
        "round":            5,
        "label":            "Round 5 — 전체 재검증",
        "feedback_date":    "2026-05-19 (화)",
        "send_candidate":   "2026-05-12 (화)  ← D-7",
        "send_excel":       "2026-05-15 (금)  ← D-4 (D-3=토 → 전날 금요일로 앞당김)",
        "receive_deadline": "2026-05-19 (화)  ← D-0  피드백 수령 마감",
        "parse_date":       "2026-05-20 (수)  ← D+1  최종 파라미터 확정 + git tag v-module2-complete 준비",
        "method":           "회차별 대표 각 2개 (총 10개)",
        "group_types":      ["dry_heat", "moist_heat", "no_heat"],
        "note":             "최종 파라미터 확정 회차. b값 안정화 확인 후 scaling_coefficients.csv 고정.",
    },
]

# ─────────────────────────────────────────────────────────────────
# 레시피 데이터 (50개 — 회차별 10개)
# 선정 기준:
#   - 회차별 group_type 일치
#   - 양념류 필수 + 최소 3개 category
#   - 재료량: 1인분 기준 (g)
#   - recipe_id: R1_001 ~ R5_010
# ─────────────────────────────────────────────────────────────────

RECIPES: dict[int, list[dict]] = {

    # ── Round 1: 볶음 (dry_heat) ──────────────────────────────────
    1: [
        {
            "recipe_id": "R1_001", "recipe_name": "제육볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "돼지고기(앞다리살)", "category": "주재료",  "base_amount_g": 150.0},
                {"name": "양파",               "category": "부재료",  "base_amount_g": 50.0},
                {"name": "대파",               "category": "부재료",  "base_amount_g": 20.0},
                {"name": "고추장",             "category": "양념류",  "base_amount_g": 30.0},
                {"name": "간장",               "category": "양념류",  "base_amount_g": 10.0},
                {"name": "설탕",               "category": "양념류",  "base_amount_g": 8.0},
                {"name": "물",                 "category": "수분류",  "base_amount_g": 30.0},
                {"name": "참기름",             "category": "유지류",  "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R1_002", "recipe_name": "오징어볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "오징어",   "category": "주재료", "base_amount_g": 120.0},
                {"name": "양파",     "category": "부재료", "base_amount_g": 40.0},
                {"name": "당근",     "category": "부재료", "base_amount_g": 20.0},
                {"name": "고추장",   "category": "양념류", "base_amount_g": 25.0},
                {"name": "고춧가루", "category": "양념류", "base_amount_g": 5.0},
                {"name": "간장",     "category": "양념류", "base_amount_g": 8.0},
                {"name": "설탕",     "category": "양념류", "base_amount_g": 6.0},
                {"name": "물",       "category": "수분류", "base_amount_g": 20.0},
                {"name": "식용유",   "category": "유지류", "base_amount_g": 10.0},
            ],
        },
        {
            "recipe_id": "R1_003", "recipe_name": "닭갈비",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "닭고기(허벅지)",  "category": "주재료", "base_amount_g": 160.0},
                {"name": "양배추",          "category": "부재료", "base_amount_g": 40.0},
                {"name": "양파",            "category": "부재료", "base_amount_g": 30.0},
                {"name": "고추장",          "category": "양념류", "base_amount_g": 28.0},
                {"name": "간장",            "category": "양념류", "base_amount_g": 10.0},
                {"name": "설탕",            "category": "양념류", "base_amount_g": 8.0},
                {"name": "고춧가루",        "category": "양념류", "base_amount_g": 5.0},
                {"name": "물",              "category": "수분류", "base_amount_g": 25.0},
                {"name": "식용유",          "category": "유지류", "base_amount_g": 10.0},
            ],
        },
        {
            "recipe_id": "R1_004", "recipe_name": "소불고기볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "소고기(불고기용)", "category": "주재료", "base_amount_g": 100.0},
                {"name": "양파",             "category": "부재료", "base_amount_g": 40.0},
                {"name": "당근",             "category": "부재료", "base_amount_g": 20.0},
                {"name": "간장",             "category": "양념류", "base_amount_g": 20.0},
                {"name": "설탕",             "category": "양념류", "base_amount_g": 10.0},
                {"name": "배즙",             "category": "수분류", "base_amount_g": 20.0},
                {"name": "참기름",           "category": "유지류", "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R1_005", "recipe_name": "어묵볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "어묵(사각)",  "category": "주재료", "base_amount_g": 80.0},
                {"name": "양파",        "category": "부재료", "base_amount_g": 30.0},
                {"name": "당근",        "category": "부재료", "base_amount_g": 15.0},
                {"name": "간장",        "category": "양념류", "base_amount_g": 12.0},
                {"name": "설탕",        "category": "양념류", "base_amount_g": 5.0},
                {"name": "고춧가루",    "category": "양념류", "base_amount_g": 3.0},
                {"name": "물",          "category": "수분류", "base_amount_g": 20.0},
                {"name": "식용유",      "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R1_006", "recipe_name": "두부김치볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "두부",          "category": "주재료", "base_amount_g": 80.0},
                {"name": "김치",          "category": "부재료", "base_amount_g": 60.0},
                {"name": "돼지고기(목살)", "category": "주재료", "base_amount_g": 40.0},
                {"name": "고춧가루",       "category": "양념류", "base_amount_g": 5.0},
                {"name": "간장",           "category": "양념류", "base_amount_g": 8.0},
                {"name": "참기름",         "category": "유지류", "base_amount_g": 5.0},
                {"name": "식용유",         "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R1_007", "recipe_name": "버섯볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "느타리버섯",  "category": "주재료", "base_amount_g": 80.0},
                {"name": "팽이버섯",    "category": "부재료", "base_amount_g": 30.0},
                {"name": "당근",        "category": "부재료", "base_amount_g": 15.0},
                {"name": "간장",        "category": "양념류", "base_amount_g": 10.0},
                {"name": "소금",        "category": "양념류", "base_amount_g": 2.0},
                {"name": "참기름",      "category": "유지류", "base_amount_g": 5.0},
                {"name": "식용유",      "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R1_008", "recipe_name": "숙주나물볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "숙주나물",  "category": "주재료", "base_amount_g": 80.0},
                {"name": "부추",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 1.5},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 4.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 6.0},
            ],
        },
        {
            "recipe_id": "R1_009", "recipe_name": "애호박볶음",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "애호박",   "category": "주재료", "base_amount_g": 100.0},
                {"name": "양파",     "category": "부재료", "base_amount_g": 20.0},
                {"name": "간장",     "category": "양념류", "base_amount_g": 8.0},
                {"name": "소금",     "category": "양념류", "base_amount_g": 1.5},
                {"name": "참기름",   "category": "유지류", "base_amount_g": 4.0},
                {"name": "식용유",   "category": "유지류", "base_amount_g": 7.0},
            ],
        },
        {
            "recipe_id": "R1_010", "recipe_name": "고추잡채",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 1,
            "ingredients": [
                {"name": "소고기(채)",   "category": "주재료", "base_amount_g": 60.0},
                {"name": "고추(청·홍)",  "category": "부재료", "base_amount_g": 50.0},
                {"name": "양파",         "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 12.0},
                {"name": "굴소스",       "category": "양념류", "base_amount_g": 8.0},
                {"name": "설탕",         "category": "양념류", "base_amount_g": 4.0},
                {"name": "물",           "category": "수분류", "base_amount_g": 15.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
                {"name": "식용유",       "category": "유지류", "base_amount_g": 8.0},
            ],
        },
    ],

    # ── Round 2: 구이·부침 (dry_heat 5) + 끓이기 (moist_heat 5) ──
    2: [
        {
            "recipe_id": "R2_001", "recipe_name": "삼치구이",
            "cooking_method": "구이", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "삼치",      "category": "주재료", "base_amount_g": 120.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 3.0},
                {"name": "청주",      "category": "수분류", "base_amount_g": 10.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R2_002", "recipe_name": "돼지불고기(직화)",
            "cooking_method": "구이", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "돼지고기(목살)", "category": "주재료", "base_amount_g": 130.0},
                {"name": "양파",           "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",           "category": "양념류", "base_amount_g": 15.0},
                {"name": "설탕",           "category": "양념류", "base_amount_g": 8.0},
                {"name": "참기름",         "category": "유지류", "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R2_003", "recipe_name": "감자전",
            "cooking_method": "부침", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "감자(채)",  "category": "주재료", "base_amount_g": 100.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 2.0},
                {"name": "전분",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 15.0},
            ],
        },
        {
            "recipe_id": "R2_004", "recipe_name": "호박전",
            "cooking_method": "부침", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "애호박(슬라이스)", "category": "주재료", "base_amount_g": 80.0},
                {"name": "밀가루",           "category": "부재료", "base_amount_g": 15.0},
                {"name": "달걀",             "category": "부재료", "base_amount_g": 25.0},
                {"name": "소금",             "category": "양념류", "base_amount_g": 1.5},
                {"name": "식용유",           "category": "유지류", "base_amount_g": 12.0},
            ],
        },
        {
            "recipe_id": "R2_005", "recipe_name": "동태전",
            "cooking_method": "부침", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "동태(포)",  "category": "주재료", "base_amount_g": 100.0},
                {"name": "밀가루",    "category": "부재료", "base_amount_g": 15.0},
                {"name": "달걀",      "category": "부재료", "base_amount_g": 25.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 2.0},
                {"name": "후추",      "category": "양념류", "base_amount_g": 0.5},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 12.0},
            ],
        },
        {
            "recipe_id": "R2_006", "recipe_name": "콩나물국",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "콩나물",    "category": "주재료", "base_amount_g": 80.0},
                {"name": "대파",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 2.0},
                {"name": "물(육수)",  "category": "수분류", "base_amount_g": 200.0},
            ],
        },
        {
            "recipe_id": "R2_007", "recipe_name": "미역국",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "미역(불린)", "category": "주재료", "base_amount_g": 60.0},
                {"name": "소고기(국거리)", "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",       "category": "양념류", "base_amount_g": 10.0},
                {"name": "소금",       "category": "양념류", "base_amount_g": 1.5},
                {"name": "물",         "category": "수분류", "base_amount_g": 250.0},
                {"name": "참기름",     "category": "유지류", "base_amount_g": 3.0},
            ],
        },
        {
            "recipe_id": "R2_008", "recipe_name": "북어국",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "북어채",    "category": "주재료", "base_amount_g": 20.0},
                {"name": "두부",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "대파",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 1.5},
                {"name": "물",        "category": "수분류", "base_amount_g": 250.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 3.0},
            ],
        },
        {
            "recipe_id": "R2_009", "recipe_name": "시금치된장국",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "시금치",    "category": "주재료", "base_amount_g": 60.0},
                {"name": "된장",      "category": "양념류", "base_amount_g": 15.0},
                {"name": "마늘",      "category": "양념류", "base_amount_g": 3.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 1.0},
                {"name": "물(멸치육수)", "category": "수분류", "base_amount_g": 250.0},
            ],
        },
        {
            "recipe_id": "R2_010", "recipe_name": "무국",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 2,
            "ingredients": [
                {"name": "무",        "category": "주재료", "base_amount_g": 100.0},
                {"name": "대파",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 1.5},
                {"name": "물",        "category": "수분류", "base_amount_g": 250.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 3.0},
            ],
        },
    ],

    # ── Round 3: 찜·조림 (moist_heat 6) + 비가열 무침 (no_heat 4) ─
    3: [
        {
            "recipe_id": "R3_001", "recipe_name": "갈비찜",
            "cooking_method": "찜", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "갈비(소)",   "category": "주재료", "base_amount_g": 200.0},
                {"name": "당근",       "category": "부재료", "base_amount_g": 30.0},
                {"name": "무",         "category": "부재료", "base_amount_g": 40.0},
                {"name": "간장",       "category": "양념류", "base_amount_g": 25.0},
                {"name": "설탕",       "category": "양념류", "base_amount_g": 15.0},
                {"name": "참기름",     "category": "유지류", "base_amount_g": 5.0},
                {"name": "물",         "category": "수분류", "base_amount_g": 100.0},
            ],
        },
        {
            "recipe_id": "R3_002", "recipe_name": "달걀찜",
            "cooking_method": "찜", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "달걀",      "category": "주재료", "base_amount_g": 60.0},
                {"name": "새우젓",    "category": "양념류", "base_amount_g": 5.0},
                {"name": "소금",      "category": "양념류", "base_amount_g": 1.0},
                {"name": "물(육수)",  "category": "수분류", "base_amount_g": 80.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 2.0},
            ],
        },
        {
            "recipe_id": "R3_003", "recipe_name": "고등어조림",
            "cooking_method": "조림", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "고등어",    "category": "주재료", "base_amount_g": 120.0},
                {"name": "무",        "category": "부재료", "base_amount_g": 50.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 15.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 10.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 80.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R3_004", "recipe_name": "두부조림",
            "cooking_method": "조림", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "두부",      "category": "주재료", "base_amount_g": 100.0},
                {"name": "대파",      "category": "부재료", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 15.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "설탕",      "category": "양념류", "base_amount_g": 4.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 60.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R3_005", "recipe_name": "감자조림",
            "cooking_method": "조림", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "감자",      "category": "주재료", "base_amount_g": 100.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 12.0},
                {"name": "설탕",      "category": "양념류", "base_amount_g": 6.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 3.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 60.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 6.0},
            ],
        },
        {
            "recipe_id": "R3_006", "recipe_name": "닭찜",
            "cooking_method": "찜", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "닭고기(토막)", "category": "주재료", "base_amount_g": 180.0},
                {"name": "당근",         "category": "부재료", "base_amount_g": 25.0},
                {"name": "감자",         "category": "부재료", "base_amount_g": 40.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 20.0},
                {"name": "고추장",       "category": "양념류", "base_amount_g": 10.0},
                {"name": "설탕",         "category": "양념류", "base_amount_g": 8.0},
                {"name": "물",           "category": "수분류", "base_amount_g": 80.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
            ],
        },
        {
            "recipe_id": "R3_007", "recipe_name": "시금치무침",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "시금치(데친)", "category": "주재료", "base_amount_g": 80.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 8.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
                {"name": "소금",         "category": "양념류", "base_amount_g": 1.0},
            ],
        },
        {
            "recipe_id": "R3_008", "recipe_name": "콩나물무침",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "콩나물(데친)", "category": "주재료", "base_amount_g": 80.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 8.0},
                {"name": "고춧가루",     "category": "양념류", "base_amount_g": 3.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
            ],
        },
        {
            "recipe_id": "R3_009", "recipe_name": "오이무침",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "오이",      "category": "주재료", "base_amount_g": 80.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "식초",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "설탕",      "category": "양념류", "base_amount_g": 5.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 3.0},
            ],
        },
        {
            "recipe_id": "R3_010", "recipe_name": "도라지무침",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 3,
            "ingredients": [
                {"name": "도라지",    "category": "주재료", "base_amount_g": 60.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 5.0},
                {"name": "설탕",      "category": "양념류", "base_amount_g": 4.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 3.0},
            ],
        },
    ],

    # ── Round 4: 혼합 조리법 (secondary_method 존재) ───────────────
    4: [
        {
            "recipe_id": "R4_001", "recipe_name": "잡채",
            "cooking_method": "볶음+비가열", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "당면(불린)",   "category": "주재료", "base_amount_g": 80.0},
                {"name": "소고기(채)",   "category": "주재료", "base_amount_g": 40.0},
                {"name": "시금치",       "category": "부재료", "base_amount_g": 30.0},
                {"name": "당근",         "category": "부재료", "base_amount_g": 20.0},
                {"name": "양파",         "category": "부재료", "base_amount_g": 25.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 20.0},
                {"name": "설탕",         "category": "양념류", "base_amount_g": 10.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 8.0},
                {"name": "식용유",       "category": "유지류", "base_amount_g": 10.0},
            ],
        },
        {
            "recipe_id": "R4_002", "recipe_name": "된장찌개",
            "cooking_method": "끓이기+조림", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "두부",      "category": "주재료", "base_amount_g": 60.0},
                {"name": "호박",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "양파",      "category": "부재료", "base_amount_g": 20.0},
                {"name": "된장",      "category": "양념류", "base_amount_g": 20.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 5.0},
                {"name": "물(멸치육수)", "category": "수분류", "base_amount_g": 200.0},
            ],
        },
        {
            "recipe_id": "R4_003", "recipe_name": "부대찌개",
            "cooking_method": "끓이기+볶음", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "소시지",    "category": "주재료", "base_amount_g": 50.0},
                {"name": "햄",        "category": "주재료", "base_amount_g": 40.0},
                {"name": "두부",      "category": "부재료", "base_amount_g": 40.0},
                {"name": "김치",      "category": "부재료", "base_amount_g": 50.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 12.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 5.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 200.0},
            ],
        },
        {
            "recipe_id": "R4_004", "recipe_name": "순두부찌개",
            "cooking_method": "끓이기+찜", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "순두부",    "category": "주재료", "base_amount_g": 150.0},
                {"name": "돼지고기(다짐)", "category": "부재료", "base_amount_g": 30.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 8.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "새우젓",    "category": "양념류", "base_amount_g": 5.0},
                {"name": "물(육수)",  "category": "수분류", "base_amount_g": 180.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 3.0},
            ],
        },
        {
            "recipe_id": "R4_005", "recipe_name": "김치찌개",
            "cooking_method": "끓이기+볶음", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "김치",      "category": "주재료", "base_amount_g": 100.0},
                {"name": "돼지고기(목살)", "category": "부재료", "base_amount_g": 50.0},
                {"name": "두부",      "category": "부재료", "base_amount_g": 40.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 10.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 5.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 180.0},
            ],
        },
        {
            "recipe_id": "R4_006", "recipe_name": "낙지볶음찜",
            "cooking_method": "볶음+찜", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "낙지",      "category": "주재료", "base_amount_g": 100.0},
                {"name": "양파",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "대파",      "category": "부재료", "base_amount_g": 15.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 20.0},
                {"name": "고춧가루",  "category": "양념류", "base_amount_g": 5.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "물",        "category": "수분류", "base_amount_g": 30.0},
                {"name": "식용유",    "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R4_007", "recipe_name": "고추장삼겹살볶음",
            "cooking_method": "볶음+구이", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "삼겹살",    "category": "주재료", "base_amount_g": 120.0},
                {"name": "양파",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 20.0},
                {"name": "간장",      "category": "양념류", "base_amount_g": 8.0},
                {"name": "설탕",      "category": "양념류", "base_amount_g": 6.0},
                {"name": "참기름",    "category": "유지류", "base_amount_g": 4.0},
            ],
        },
        {
            "recipe_id": "R4_008", "recipe_name": "마파두부",
            "cooking_method": "볶음+찜", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "두부",       "category": "주재료", "base_amount_g": 100.0},
                {"name": "돼지고기(다짐)", "category": "부재료", "base_amount_g": 30.0},
                {"name": "두반장",     "category": "양념류", "base_amount_g": 15.0},
                {"name": "간장",       "category": "양념류", "base_amount_g": 8.0},
                {"name": "물",         "category": "수분류", "base_amount_g": 50.0},
                {"name": "식용유",     "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R4_009", "recipe_name": "떡볶이",
            "cooking_method": "끓이기+볶음", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "떡(가래)",   "category": "주재료", "base_amount_g": 100.0},
                {"name": "어묵",       "category": "부재료", "base_amount_g": 30.0},
                {"name": "고추장",     "category": "양념류", "base_amount_g": 25.0},
                {"name": "고춧가루",   "category": "양념류", "base_amount_g": 5.0},
                {"name": "설탕",       "category": "양념류", "base_amount_g": 10.0},
                {"name": "간장",       "category": "양념류", "base_amount_g": 5.0},
                {"name": "물",         "category": "수분류", "base_amount_g": 150.0},
            ],
        },
        {
            "recipe_id": "R4_010", "recipe_name": "해물파전",
            "cooking_method": "부침+볶음", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 4,
            "ingredients": [
                {"name": "오징어+새우(혼합해물)", "category": "주재료", "base_amount_g": 60.0},
                {"name": "쪽파",         "category": "부재료", "base_amount_g": 40.0},
                {"name": "밀가루",       "category": "부재료", "base_amount_g": 30.0},
                {"name": "소금",         "category": "양념류", "base_amount_g": 1.5},
                {"name": "물(반죽용)",   "category": "수분류", "base_amount_g": 50.0},
                {"name": "식용유",       "category": "유지류", "base_amount_g": 15.0},
            ],
        },
    ],

    # ── Round 5: 전체 재검증 (회차별 2개씩) ───────────────────────
    5: [
        # Round 1 대표 2개
        {
            "recipe_id": "R5_001", "recipe_name": "제육볶음(재검증)",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "돼지고기(앞다리살)", "category": "주재료",  "base_amount_g": 150.0},
                {"name": "양파",               "category": "부재료",  "base_amount_g": 50.0},
                {"name": "고추장",             "category": "양념류",  "base_amount_g": 30.0},
                {"name": "간장",               "category": "양념류",  "base_amount_g": 10.0},
                {"name": "설탕",               "category": "양념류",  "base_amount_g": 8.0},
                {"name": "물",                 "category": "수분류",  "base_amount_g": 30.0},
                {"name": "참기름",             "category": "유지류",  "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R5_002", "recipe_name": "닭갈비(재검증)",
            "cooking_method": "볶음", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "닭고기(허벅지)",  "category": "주재료", "base_amount_g": 160.0},
                {"name": "양배추",          "category": "부재료", "base_amount_g": 40.0},
                {"name": "고추장",          "category": "양념류", "base_amount_g": 28.0},
                {"name": "간장",            "category": "양념류", "base_amount_g": 10.0},
                {"name": "설탕",            "category": "양념류", "base_amount_g": 8.0},
                {"name": "물",              "category": "수분류", "base_amount_g": 25.0},
                {"name": "식용유",          "category": "유지류", "base_amount_g": 10.0},
            ],
        },
        # Round 2 대표 2개
        {
            "recipe_id": "R5_003", "recipe_name": "삼치구이(재검증)",
            "cooking_method": "구이", "group_type": "dry_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "삼치",   "category": "주재료", "base_amount_g": 120.0},
                {"name": "소금",   "category": "양념류", "base_amount_g": 3.0},
                {"name": "청주",   "category": "수분류", "base_amount_g": 10.0},
                {"name": "식용유", "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R5_004", "recipe_name": "미역국(재검증)",
            "cooking_method": "끓이기", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "미역(불린)",     "category": "주재료", "base_amount_g": 60.0},
                {"name": "소고기(국거리)", "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",           "category": "양념류", "base_amount_g": 10.0},
                {"name": "소금",           "category": "양념류", "base_amount_g": 1.5},
                {"name": "물",             "category": "수분류", "base_amount_g": 250.0},
                {"name": "참기름",         "category": "유지류", "base_amount_g": 3.0},
            ],
        },
        # Round 3 대표 2개
        {
            "recipe_id": "R5_005", "recipe_name": "갈비찜(재검증)",
            "cooking_method": "찜", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "갈비(소)", "category": "주재료", "base_amount_g": 200.0},
                {"name": "당근",     "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",     "category": "양념류", "base_amount_g": 25.0},
                {"name": "설탕",     "category": "양념류", "base_amount_g": 15.0},
                {"name": "참기름",   "category": "유지류", "base_amount_g": 5.0},
                {"name": "물",       "category": "수분류", "base_amount_g": 100.0},
            ],
        },
        {
            "recipe_id": "R5_006", "recipe_name": "콩나물무침(재검증)",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "콩나물(데친)", "category": "주재료", "base_amount_g": 80.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 8.0},
                {"name": "고춧가루",     "category": "양념류", "base_amount_g": 3.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
            ],
        },
        # Round 4 대표 2개
        {
            "recipe_id": "R5_007", "recipe_name": "잡채(재검증)",
            "cooking_method": "볶음+비가열", "group_type": "dry_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "당면(불린)",  "category": "주재료", "base_amount_g": 80.0},
                {"name": "소고기(채)",  "category": "주재료", "base_amount_g": 40.0},
                {"name": "시금치",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "간장",        "category": "양념류", "base_amount_g": 20.0},
                {"name": "설탕",        "category": "양념류", "base_amount_g": 10.0},
                {"name": "참기름",      "category": "유지류", "base_amount_g": 8.0},
            ],
        },
        {
            "recipe_id": "R5_008", "recipe_name": "된장찌개(재검증)",
            "cooking_method": "끓이기+조림", "group_type": "moist_heat",
            "has_secondary_method": True,
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "두부",      "category": "주재료", "base_amount_g": 60.0},
                {"name": "호박",      "category": "부재료", "base_amount_g": 30.0},
                {"name": "된장",      "category": "양념류", "base_amount_g": 20.0},
                {"name": "고추장",    "category": "양념류", "base_amount_g": 5.0},
                {"name": "물(멸치육수)", "category": "수분류", "base_amount_g": 200.0},
            ],
        },
        # 추가 2개 (전체 균형 확인용)
        {
            "recipe_id": "R5_009", "recipe_name": "고등어조림(재검증)",
            "cooking_method": "조림", "group_type": "moist_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "고등어",   "category": "주재료", "base_amount_g": 120.0},
                {"name": "무",       "category": "부재료", "base_amount_g": 50.0},
                {"name": "고추장",   "category": "양념류", "base_amount_g": 15.0},
                {"name": "간장",     "category": "양념류", "base_amount_g": 10.0},
                {"name": "물",       "category": "수분류", "base_amount_g": 80.0},
                {"name": "식용유",   "category": "유지류", "base_amount_g": 5.0},
            ],
        },
        {
            "recipe_id": "R5_010", "recipe_name": "시금치무침(재검증)",
            "cooking_method": "무침", "group_type": "no_heat",
            "base_serving": 1, "target_serving_n": 100, "feedback_round": 5,
            "ingredients": [
                {"name": "시금치(데친)", "category": "주재료", "base_amount_g": 80.0},
                {"name": "간장",         "category": "양념류", "base_amount_g": 8.0},
                {"name": "참기름",       "category": "유지류", "base_amount_g": 4.0},
                {"name": "소금",         "category": "양념류", "base_amount_g": 1.0},
            ],
        },
    ],
}


# ─────────────────────────────────────────────────────────────────
# 일정 출력
# ─────────────────────────────────────────────────────────────────

def print_schedule() -> None:
    """피드백 5회차 전달·수령 일정을 출력한다."""
    WIDTH = 70
    print()
    print("=" * WIDTH)
    print("  OptiMeal 영양사 피드백 — 5회차 전달·수령 일정")
    print("  기준: ADR-002 v3 | feedback_rubric_v1.0.md §5")
    print("  현재일: 2026-04-24 (오늘)")
    print("=" * WIDTH)

    for s in SCHEDULE:
        n_recipes = len(RECIPES[s["round"]])
        print()
        print(f"┌─ {s['label']}  ({n_recipes}개 레시피)")
        print(f"│  조리방법: {s['method']}")
        print(f"│")
        print(f"│  📋 [전달-1] {s['send_candidate']}")
        print(f"│             후보 레시피 목록을 텍스트(카톡·이메일)로 영양사에게 사전 안내")
        print(f"│             → 영양사가 레시피 방향 사전 검토")
        print(f"│")
        print(f"│  📁 [전달-2] {s['send_excel']}")
        print(f"│             generate_all_rounds.py --round {s['round']} 실행 → Excel 1파일 ({n_recipes}시트)")
        print(f"│             → 영양사에게 Excel 파일 1개 전달 (이메일/USB/카톡)")
        print(f"│")
        print(f"│  📥 [수령]   {s['receive_deadline']}")
        print(f"│             영양사 작성 완료된 Excel {n_recipes}개 수령")
        print(f"│             → '영양사 평가' 시트 작성 확인 (루브릭 3항목 + 재료 조정)")
        print(f"│")
        print(f"│  ⚙  [처리]   {s['parse_date']}")
        print(f"│             parse_feedback_excel.py 순차 실행 → scaling_coefficients.csv 업데이트")
        if s["note"]:
            print(f"│")
            print(f"│  ⚠  {s['note']}")
        print(f"└{'─' * (WIDTH - 2)}")

    print()
    print("=" * WIDTH)
    print("  처리 명령어 요약")
    print("=" * WIDTH)
    print()
    print("  # Excel 생성 (D-3에 실행) — 각 회차 1파일 출력")
    print("  python generate_all_rounds.py --round 1   # Round 1만 (10시트)")
    print("  python generate_all_rounds.py             # 전체 5회차 (50시트)")
    print()
    print("  # 영양사 Excel 수령 후 파싱 (D+1에 실행)")
    print("  python parse_feedback_excel.py 피드백_Round1_*.xlsx")
    print("  python parse_feedback_excel.py 피드백_Round1_*.xlsx --dry-run  # 미리보기")
    print()
    print("  # Round 5 완료 후")
    print("  git tag v-module2-complete")
    print("=" * WIDTH)
    print()


# ─────────────────────────────────────────────────────────────────
# Excel 일괄 생성
# ─────────────────────────────────────────────────────────────────

def generate_all(output_root: Path, rounds: list[int] | None = None) -> None:
    """지정한 회차(기본: 전체)의 레시피를 하나의 Excel 파일로 생성한다.

    전체 5회차 생성 시: 피드백_전체_5회차_YYYYMMDD.xlsx  (시트 50개)
    단일 회차 생성 시:  피드백_Round{N}_YYYYMMDD.xlsx    (시트 10개)
    """
    target_rounds = rounds or list(RECIPES.keys())
    all_recipes   = [r for rnd in target_rounds for r in RECIPES[rnd]]
    total         = len(all_recipes)

    if len(target_rounds) == 1:
        fname = f"피드백_Round{target_rounds[0]}_{date.today().strftime('%Y%m%d')}.xlsx"
    else:
        fname = f"피드백_전체_5회차_{date.today().strftime('%Y%m%d')}.xlsx"

    output_root.mkdir(parents=True, exist_ok=True)
    out_path = output_root / fname

    print(f"\n[생성 시작] {len(target_rounds)}개 회차 / 총 {total}개 레시피 → 시트 {total}개")
    for i, r in enumerate(all_recipes, 1):
        print(f"  [{i:2d}/{total}]  R{r['feedback_round']}  {r['recipe_id']}  {r['recipe_name']}")

    path = generate_single_file(all_recipes, out_path)
    print(f"\n[완료] {total}개 레시피 → {path.resolve()}")
    print(f"       시트 수: {total}개 (레시피 1개 = 시트 1장)")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="영양사 피드백 5회차 Excel 일괄 생성 + 일정 출력"
    )
    parser.add_argument(
        "--output", "-o",
        default=str(_TMPL_DIR / "feedback_excels"),
        help="저장 루트 폴더 (기본: templates/feedback_excels/)",
    )
    parser.add_argument(
        "--round", "-r",
        type=int, choices=[1, 2, 3, 4, 5],
        help="특정 회차만 생성 (생략 시 전체 5회차)",
    )
    parser.add_argument(
        "--schedule-only", "-s",
        action="store_true",
        help="일정 출력만 (Excel 생성 없음)",
    )
    args = parser.parse_args()

    print_schedule()

    if not args.schedule_only:
        rounds = [args.round] if args.round else None
        generate_all(Path(args.output), rounds=rounds)


if __name__ == "__main__":
    main()
