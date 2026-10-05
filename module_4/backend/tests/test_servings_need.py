"""생성 응답의 인원별 레시피(menu_recipes_by_servings)가 어떤 (인원, 메뉴)를 만들지 — 프론트 recipeView 와 같은 규칙.

대체식은 바뀐 메뉴만 따로 만든다 → 일반식은 그 메뉴를 대체식으로 받는 그룹 인원만 빼고, 같이 먹는 메뉴는 전체 인원.
DB 없이 _servings_need 만 검증한다.
"""
from module_4.backend.src.routers import menu


def test_servings_need_subtracts_only_replaced_menus():
    plan = {"1": {"점심": ["밥", "미역국", "달걀찜", "우유푸딩"]}}
    plan_ids = {"1": {"점심": [1, 2, 3, 4]}}
    alts = [
        {"group": {"count": 9}, "plan": {"1": {"점심": ["밥", "미역국", "두부조림", "우유푸딩"]}},
         "plan_ids": {"1": {"점심": [1, 2, 30, 4]}}},
        {"group": {"count": 12}, "plan": {"1": {"점심": ["밥", "미역국", "두부조림", "과일"]}},
         "plan_ids": {"1": {"점심": [1, 2, 30, 40]}}},
    ]
    need = menu._servings_need(plan, plan_ids, alts, 237)
    # 밥·미역국은 237(전체 — menu_recipes_by_id 가 이미 가짐)이라 없다
    assert need == {216: {3}, 225: {4}, 9: {30}, 12: {30, 40}}


def test_servings_need_empty_without_groups():
    assert menu._servings_need({"1": {"점심": ["밥"]}}, {"1": {"점심": [1]}}, [], 100) == {}
