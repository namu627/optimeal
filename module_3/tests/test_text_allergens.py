# -*- coding: utf-8 -*-
"""원문(조리 순서·재료명·메뉴명) 키워드로 알레르기 표시 누락을 보완하는지 고정한다.

배경(2026-10-06): 조리 순서엔 '달걀'이 있는데 recipe_ingredient_map 에 달걀이 없어 난류 표시가 빠진 메뉴
(두부 달걀전 등)가 알레르기 그룹 대체식에 그대로 남았다. DB 없이 검증한다.
"""
import os
import sys

SRC = os.path.join(os.path.dirname(__file__), "..", "src")
sys.path.insert(0, os.path.abspath(SRC))

import alternative_menu as am                      # noqa: E402
from csp_solver import MenuItem, text_allergens    # noqa: E402


def test_keywords_detect_unlabeled_allergens():
    assert text_allergens("두부 달걀전", "1. 두부를 으깨고 계란을 푼다.") >= {"난류", "대두"}
    assert "우유" in text_allergens("감자크로켓", "치즈를 넣고 버터에 굽는다")
    assert "밀" in text_allergens(None, "부침가루를 묻힌다")


def test_keywords_avoid_common_false_positives():
    assert "우유" not in text_allergens("", "땅콩버터를 바른다")      # 땅콩버터 ≠ 유제품
    assert "게" not in text_allergens("", "맛있게 볶는다")             # 어미 '게'
    assert "밀" not in text_allergens("", "메밀묵을 썬다")             # 메밀 ≠ 밀
    assert text_allergens(None, "", None) == set()


def test_text_detected_allergen_forces_menu_replacement():
    # 재료 목록엔 달걀이 없고 원문에만 있다 → 난류 그룹에선 다른 메뉴로 바뀌어야 한다(치환으론 못 없앰)
    jeon = MenuItem(1, "두부 달걀전", "주찬", 100.0, allergens={"난류"}, allergens_from_text={"난류"},
                    ingredients={"두부"}, ingredient_allergens={})
    safe = MenuItem(2, "버섯볶음", "주찬", 100.0, ingredients={"버섯"})
    assert am._try_ingredient_substitution(jeon, {"난류"}, allergen_index={}) is None
    alt = am.derive_alternative_menus({1: {"점심": ["두부 달걀전"]}}, [jeon, safe],
                                      [am.AllergyGroup(label="g", allergens={"난류"})])[0]
    assert [s.alternative for s in alt.substitutions] == ["버섯볶음"]
