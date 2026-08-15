# -*- coding: utf-8 -*-
"""OptiMeal 모듈3 · 급식 대상 프로파일 (하루 열량·나트륨 등 영양 기준)

영양사가 **자기 업장의 급식 대상**을 고르면 그에 맞는 기준으로 식단이 생성되도록,
공인 기준을 표로 정리해 두고 골라 쓰게 한다.

기준의 출처 — **임의 수치를 만들지 않는다**:
  · `2025 한국인 영양소 섭취기준`(보건복지부·한국영양학회, 2025.12) — 1일 에너지필요추정량(EER),
    단백질 권장섭취량, 나트륨 충분섭취량(AI)·만성질환위험감소섭취량(CDRR).
    ※ KDRIs 의 EER 은 신체활동수준 **'저활동적'** 기준이다(사무직에 부합).
  · `학교급식법 시행규칙 [별표3] 학교급식의 영양관리기준` — 학교급별 **1식** 에너지·단백질.
    학생 프로파일이 1식(점심) 급식일 때는 이 **법정 1식 기준이 우선**한다.
  · '혼성' 행은 남녀 1:1 단순 평균의 **파생값**이며 `source` 열에 그렇게 적혀 있다.
    실제 성비가 다르면 조정해야 한다.

표는 코드가 아니라 `data/processed/user_group_profiles.csv` 다 — 어울림 근거표와 같은 이유로,
**영양사 검수 결과로 덮어쓸 수 있어야** 하기 때문이다. 파일이 없으면 빈 목록(우아한 저하).

DB `user_group` 테이블과의 관계: 같은 내용을 `scripts/load_user_groups.py` 로 적재해 다른
모듈이 SQL 로도 쓸 수 있게 한다. 단 **정본은 CSV** 다(테이블에 sex·법정 1식 기준·출처 열이 없다).

⚠ 이 표는 **일반식 기준**이다. 치료식(당뇨·신장 등)은 의료진·병원 영양팀이 정할 사항이며
   본 모듈은 기준값을 제공하지 않는다.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

DEFAULT_PROFILE_PATH = (Path(__file__).resolve().parents[2]
                        / "data" / "processed" / "user_group_profiles.csv")

# 끼니 이름 기본값과 하루 열량 배분 비율(아침30·점심40·저녁30, PRD §끼니별 영양 배분).
#   1식·2식으로 줄일 때 **어느 끼니인지**에 따라 배분이 달라지므로 이름과 비율을 함께 둔다.
MEAL_RATIOS: dict[str, float] = {"아침": 0.30, "점심": 0.40, "저녁": 0.30}
DEFAULT_MEALS: tuple[str, ...] = ("아침", "점심", "저녁")


@dataclass
class UserProfile:
    """급식 대상 1종의 영양 기준(모두 **1일** 기준. 1식 기준은 legal_meal_* 만 별도)."""
    profile_key: str
    group_name: str
    group_type: str            # 학생 / 일반 / 노인 / 환자 (user_group CHECK 제약과 동일)
    sex: str                   # 남 / 여 / 혼성
    age_band: str
    daily_kcal: float
    protein_g: float | None
    sodium_cdrr_mg: float | None    # 만성질환위험감소섭취량 — 상한 제약에 쓰는 값
    sodium_ai_mg: float | None      # 충분섭취량 — 저염 강화 시 참고
    legal_meal_kcal: float | None   # 학교급식법 [별표3] 1식 에너지(학생만)
    legal_meal_protein_g: float | None
    default_meals: int
    source: str
    note: str


def load_profiles(path: str | Path | None = None) -> dict[str, UserProfile]:
    """프로파일 표를 읽는다. 파일이 없거나 손상되면 빈 dict(우아한 저하).

    Args:
        path: CSV 경로. None 이면 `DEFAULT_PROFILE_PATH`.

    Returns:
        {profile_key: UserProfile}. 삽입 순서를 유지하므로 목록 표시에 그대로 쓸 수 있다.
    """
    p = Path(path or DEFAULT_PROFILE_PATH)
    if not p.exists():
        return {}
    out: dict[str, UserProfile] = {}
    try:
        with open(p, encoding="utf-8", newline="") as f:
            for r in csv.DictReader(f):
                out[r["profile_key"]] = UserProfile(
                    profile_key=r["profile_key"], group_name=r["group_name"],
                    group_type=r["group_type"], sex=r["sex"], age_band=r["age_band"],
                    daily_kcal=float(r["daily_kcal"]),
                    protein_g=_num(r.get("protein_g")),
                    sodium_cdrr_mg=_num(r.get("sodium_cdrr_mg")),
                    sodium_ai_mg=_num(r.get("sodium_ai_mg")),
                    legal_meal_kcal=_num(r.get("legal_meal_kcal")),
                    legal_meal_protein_g=_num(r.get("legal_meal_protein_g")),
                    default_meals=int(r.get("default_meals") or 3),
                    source=r.get("source", ""), note=r.get("note", ""),
                )
    except (OSError, KeyError, ValueError):
        return {}
    return out


def _num(v) -> float | None:
    """빈 칸은 None(값 없음)으로. 0 과 구분해야 한다 — 0kcal 기준은 존재하지 않는다."""
    if v is None or str(v).strip() == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def meal_fraction(meals: tuple[str, ...] | list[str]) -> float:
    """편성할 끼니들이 하루 열량에서 차지하는 비율의 합.

    점심만 급식하면 0.40, 점심·저녁이면 0.70, 3식이면 1.00 이다.
    `MEAL_RATIOS` 에 없는 이름(예: '간식')은 균등 배분으로 간주하지 않고 **0 으로 두지 않는다** —
    모르는 끼니가 있으면 비율 합이 어긋나므로 전체를 균등 배분(1/n)으로 되돌린다.
    """
    if not meals:
        return 0.0
    if all(m in MEAL_RATIOS for m in meals):
        return sum(MEAL_RATIOS[m] for m in meals)
    return len(meals) / len(DEFAULT_MEALS)


def targets_for(profile: UserProfile, meals: tuple[str, ...] | list[str]) -> dict:
    """프로파일 + 편성할 끼니 → CSP 에 넘길 목표값을 만든다.

    두 가지를 정확히 지킨다:
      1. **하루 총량을 끼니 비율만큼만 잡는다.** 점심 1식 급식에 1일 2,500kcal 을 그대로
         걸면 한 끼에 2,500kcal 을 우겨넣는 식단이 나온다(실측으로 확인).
      2. **학생은 법정 1식 기준이 우선한다.** 학교급식법 [별표3]이 1식 값을 직접 정해 두었고,
         그 값은 1일의 1/3 수준이라 비율 배분(점심 40%)과 다르다. 법정 값이 있고 1식만
         편성할 때는 그 값을 쓴다.

    Args:
        profile: UserProfile.
        meals: 편성할 끼니 이름들(예: ("점심",), ("점심","저녁")).

    Returns:
        {"target_kcal_per_day", "sodium_max_mg_per_day", "protein_min_g",
         "meal_energy_ratios", "basis"} — basis 는 어떤 근거로 정했는지 문자열.
    """
    frac = meal_fraction(meals)
    use_legal = (len(meals) == 1 and profile.legal_meal_kcal is not None)
    if use_legal:
        kcal = profile.legal_meal_kcal
        protein = profile.legal_meal_protein_g
        basis = "학교급식법 [별표3] 1식 기준"
    else:
        kcal = profile.daily_kcal * frac
        protein = (profile.protein_g * frac) if profile.protein_g is not None else None
        basis = f"{profile.source} 1일 기준 × 끼니비율 {frac:.2f}"
    sodium = (profile.sodium_cdrr_mg * frac) if profile.sodium_cdrr_mg is not None else None
    # 끼니 배분 비율은 편성한 끼니들 안에서 다시 정규화한다(점심·저녁이면 4:3 → 0.571:0.429).
    ratios = _normalized_ratios(meals)
    return {
        "target_kcal_per_day": round(kcal, 1),
        "sodium_max_mg_per_day": round(sodium, 1) if sodium is not None else None,
        "protein_min_g": round(protein, 1) if protein is not None else None,
        "meal_energy_ratios": ratios,
        "basis": basis,
    }


def _normalized_ratios(meals: tuple[str, ...] | list[str]) -> tuple:
    """편성한 끼니들 사이의 열량 배분 비율(합=1). 끼니가 1개면 (1.0,)."""
    if not meals:
        return ()
    known = [MEAL_RATIOS.get(m) for m in meals]
    if any(k is None for k in known):
        return tuple(round(1.0 / len(meals), 4) for _ in meals)
    total = sum(known)
    return tuple(round(k / total, 4) for k in known)
