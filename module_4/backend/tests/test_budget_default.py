"""/api/menu/generate 예산 기본값 — 생략 시 1인 1끼 3,500원 × 끼니 수를 1일 상한으로.

명시값·명시적 null 경로(프론트가 한 끼 예산 × 끼니 수를 보내는 기존 경로)는 바뀌지 않아야 한다.
DB·모듈3 없이 도는 순수 단위 테스트.
"""
from __future__ import annotations

import pytest

from module_4.backend.src import schemas
from module_4.backend.src.routers import menu

THREE = ("아침", "점심", "저녁")


@pytest.mark.parametrize("meals, expected", [(("점심",), 3500.0), (("점심", "저녁"), 7000.0), (THREE, 10500.0)])
def test_omitted_budget_defaults_to_per_meal_times_meals(meals, expected):
    payload = schemas.MenuGenerateRequest(days=7)
    assert payload.budget_limit_per_person is None
    assert menu._budget_limit_per_day(payload, meals) == expected


def test_explicit_budget_passes_through_unchanged():
    payload = schemas.MenuGenerateRequest(days=7, budget_limit_per_person=4200)
    assert menu._budget_limit_per_day(payload, THREE) == 4200


def test_explicit_null_budget_means_no_cap():
    payload = schemas.MenuGenerateRequest.model_validate({"days": 7, "budget_limit_per_person": None})
    assert menu._budget_limit_per_day(payload, THREE) is None
