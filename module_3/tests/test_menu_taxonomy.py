# -*- coding: utf-8 -*-
"""메뉴 분류 체계(주식 유형·국 유형·카테고리 재분류) 검증 테스트.

배경(2026-08-11 영양사 지적):
  1. 주식 슬롯에 '포니언 스프'가 단독 편성됐다.
  2. `grouping_type='일품요리'` 171종이 통째로 주식이라 스테이크·탕수육이 섞였다.
  3. 국수 + 부대찌개처럼 어울리지 않는 조합이 나왔다.

여기서는 분류기 자체를 고정한다. 실제 편성 제약은 `test_menu_pairing.py`.
"""
import os
import sys

import pytest

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import menu_taxonomy as mt  # noqa: E402


# ---------------------------------------------------------------------------
# 접미사 우선 — 두 유형 키워드가 한 이름에 있을 때
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name,kind", [
    ("죽순콩나물밥", "rice"),        # 죽순의 '죽' 무시 + 끝의 '밥'
    ("찬밥이용닭죽", "porridge"),    # 앞의 '밥'보다 끝의 '죽'
    ("토마토스프파스타", "noodle"),  # 앞의 '스프'보다 끝의 '파스타'
    ("황태포당면국수", "noodle"),
    ("파인애플볶음밥", "rice"),
])
def test_suffix_wins_over_prefix(name, kind):
    """가장 뒤에 오는 키워드가 요리 형태를 결정한다."""
    assert mt.classify_staple_kind(name) == kind


# ---------------------------------------------------------------------------
# 오탐 토큰 — 다른 낱말의 일부인 글자를 키워드로 세면 안 된다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["가지 탕수육", "미니버섯탕수", "미니 그라탕"])
def test_tang_false_friends_are_not_soup(name):
    """탕수육·그라탕의 '탕'은 국물이 아니다."""
    assert mt.detect_soup_kind(name) != mt.STEW_KIND


@pytest.mark.parametrize("name", ["청국장소스 연어 스테이크", "콩국물소스를 곁들인 게살 냉채"])
def test_guk_false_friends_are_not_soup(name):
    """청국장·콩국물의 '국'은 국물요리가 아니다."""
    assert mt.detect_soup_kind(name) is None


def test_real_stew_still_detected():
    """오탐 마스킹이 진짜 국물요리까지 지우면 안 된다."""
    assert mt.classify_soup_kind("청국장찌개") == mt.STEW_KIND
    assert mt.classify_soup_kind("콩국수") != mt.STEW_KIND


# ---------------------------------------------------------------------------
# 지적 1 — 스프는 주식이 아니다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["포니언 스프", "치킨완자스프", "차가운 당근 수프",
                                  "단호박 두부 포타주", "연어차우더스프"])
def test_soup_is_not_staple(name):
    """스프류는 주식 슬롯 자격이 없다."""
    assert mt.classify_staple_kind(name) == "soup_only"
    assert mt.is_staple(name) is False


# ---------------------------------------------------------------------------
# 지적 2 — 일품요리 안의 주찬은 주식이 아니고, 외래어 주식은 주식이다
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("name", ["통삼겹스테이크", "가지 탕수육", "크림소스치킨롤",
                                  "닭가슴살 브로콜리 만두", "들깨 곤약 냉채"])
def test_onedish_main_dishes_are_not_staple(name):
    """스테이크·탕수육·만두는 주찬이지 주식이 아니다."""
    assert mt.is_staple(name) is False
    assert mt.resolve_menu_category("일품요리", name) == "반찬"


@pytest.mark.parametrize("name", ["된장크림소스 잡곡 오므라이스", "새우 카레 빠에야",
                                  "두유 크림 현미리소토", "버섯을 넣은 가지 라자냐",
                                  "머위향 통밀푸실리", "단호박약식", "실곤약팟타야"])
def test_loanword_staples_stay_staple(name):
    """오므라이스·빠에야·리소토 같은 외래어 주식이 반찬으로 새면 안 된다.

    이걸 놓치면 '주식 없는 끼니'가 다시 생긴다(전수 검증에서 실제로 발견됨).
    """
    assert mt.is_staple(name) is True
    assert mt.resolve_menu_category("일품요리", name) == "주식"


def test_onedish_soup_goes_to_soup_not_side():
    """일품요리로 라벨된 탕·전골은 반찬이 아니라 국으로 간다."""
    assert mt.resolve_menu_category("일품요리", "호박잎 삼계탕") == "국"
    assert mt.resolve_menu_category("일품요리", "돼지머리수육맑은전골") == "국"
    assert mt.resolve_menu_category("일품요리", "포니언 스프") == "국"


def test_non_staple_grouping_is_untouched():
    """밥·국·반찬 등 다른 grouping_type 은 메뉴명과 무관하게 기존 매핑을 유지한다."""
    assert mt.resolve_menu_category("국", "부대찌개") == "국"
    assert mt.resolve_menu_category("주찬", "통삼겹스테이크") == "반찬"
    assert mt.resolve_menu_category("김치", "함초김치") == "반찬"
    assert mt.resolve_menu_category("후식", "멜론스프") == "후식"


# ---------------------------------------------------------------------------
# 지적 3 — 궁합 규칙
# ---------------------------------------------------------------------------
def test_stew_requires_rice():
    """찌개·전골·탕은 밥류 주식과만 배식한다."""
    assert mt.is_compatible("rice", mt.STEW_KIND) is True
    for kind in ("noodle", "porridge", "bread"):
        assert mt.is_compatible(kind, mt.STEW_KIND) is False


def test_plain_soup_goes_with_anything():
    """맑은국·미역국·스프는 어떤 주식과도 배식 가능하다."""
    for kind in ("rice", "noodle", "porridge", "bread"):
        assert mt.is_compatible(kind, "soup") is True


def test_unknown_soup_is_treated_as_stew():
    """국 카테고리인데 유형을 모르면 보수적으로 밥 전용으로 본다.

    정체불명 국물요리를 면·빵에 붙이는 쪽이 더 위험하다.
    """
    assert mt.classify_soup_kind("이름없는무언가") == mt.STEW_KIND


def test_noodle_with_budae_jjigae_is_rejected():
    """지적된 실제 사례: 국수 + 부대찌개."""
    staple = mt.classify_staple_kind("함초 냉이 국수")
    soup = mt.classify_soup_kind("삼계부대찌개")
    assert staple == "noodle" and soup == mt.STEW_KIND
    assert mt.is_compatible(staple, soup) is False
