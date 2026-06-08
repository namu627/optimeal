"""
run_demo.py
===========
For_finalexam 통합 데모 진입점.

흐름:
  1. 모듈 1 산물(df_B.csv 205개 1인분 레시피)에서 랜덤 또는 지정 메뉴 추출
  2. 모듈 2 운영 엔진(NN_v2 + lookup 하이브리드, ADR-007)으로 N인분 스케일링
  3. CLI 요약 출력 + HTML 리포트 생성 (사람 열람용, 전역 규칙)

사용:
    python For_finalexam/run_demo.py --n 100
    python For_finalexam/run_demo.py --n 50 --recipe-id A1034
    python For_finalexam/run_demo.py --n 200 --seed 7

기말발표용. 11월 최종 모듈이 아니라 1-5월 작업의 정합성 증명물.

담당: 권성민 (Opus 4.7 보조)
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

_HERE = Path(__file__).parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from hybrid_engine import HybridScaler  # noqa: E402
from recipe_loader import load_recipes, pick_recipe  # noqa: E402
from reporter import render_html  # noqa: E402


OUTPUT_DIR: Path = _HERE / "output"


def format_cli(recipe, results, n: int) -> str:
    """CLI 요약 텍스트 생성."""
    lines = []
    lines.append("=" * 78)
    lines.append(f"OptiMeal 통합 데모 (모듈 1+2, ADR-007 NN_v2+lookup 하이브리드)")
    lines.append("=" * 78)
    lines.append(f"메뉴          : {recipe.menu_name}  [{recipe.recipe_id}]")
    lines.append(f"group_type    : {recipe.group_type_kor}")
    lines.append(f"cooking_method: {recipe.cooking_method}")
    lines.append(f"목표 인원수    : N = {n}")
    lines.append("-" * 78)
    lines.append(f"{'#':>2} {'재료':<20} {'카테고리':<8} {'1인분(g)':>10} "
                 f"{'분기':<22} {'b':>7} {'N인분(g)':>12}")
    lines.append("-" * 78)
    nn_cnt, lk_cnt, skip_cnt = 0, 0, 0
    for i, r in enumerate(results, 1):
        if r.method == "skip":
            skip_cnt += 1
            tag = "SKIP"
        elif r.method == "nn_v2":
            nn_cnt += 1
            tag = "NN_v2 (학습 셀)"
        else:
            lk_cnt += 1
            tag = "lookup fallback"
        scaled_disp = f"{r.scaled:.2f}" if r.scaled == r.scaled else "—"
        b_disp = f"{r.b_value:.4f}" if r.b_value == r.b_value else "—"
        lines.append(
            f"{i:>2} {r.name[:18]:<20} {r.category:<8} {r.base:>10.2f} "
            f"{tag:<22} {b_disp:>7} {scaled_disp:>12}"
        )
    lines.append("-" * 78)
    lines.append(
        f"분기 요약: NN_v2 {nn_cnt}건 | lookup {lk_cnt}건 | skip {skip_cnt}건 "
        f"(총 {len(results)}건)"
    )
    return "\n".join(lines)


def main() -> int:
    """CLI 진입점."""
    parser = argparse.ArgumentParser(
        description="OptiMeal 모듈 1+2 통합 데모 (ADR-007 하이브리드)"
    )
    parser.add_argument(
        "--n", type=int, required=True,
        help="목표 인원수 (예: 100)",
    )
    parser.add_argument(
        "--recipe-id", type=str, default=None,
        help="특정 small_recipe_id (지정 시 랜덤 추출 비활성)",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="랜덤 시드 (재현성)",
    )
    parser.add_argument(
        "--no-html", action="store_true",
        help="HTML 리포트 생성 생략",
    )
    args = parser.parse_args()

    if args.n < 1:
        print("[error] N은 1 이상이어야 합니다.", file=sys.stderr)
        return 1

    bundles = load_recipes()
    matched_cnt = sum(1 for b in bundles.values() if b.matched_in_dfB)
    print(
        f"[loader] {len(bundles)}개 1인분 레시피 로드 "
        f"(소규모 DB 엑셀, plausibility 50~3000g 필터 적용, "
        f"df_B 매칭 {matched_cnt}개)"
    )

    recipe = pick_recipe(bundles, recipe_id=args.recipe_id, seed=args.seed)
    skip_msg = (
        f", g 외 단위 {recipe.skipped_non_g}개 제외"
        if recipe.skipped_non_g else ""
    )
    matched_msg = " · df_B 매칭됨" if recipe.matched_in_dfB else ""
    print(
        f"[loader] 선택: [{recipe.recipe_id}] {recipe.menu_name} "
        f"({recipe.group_type_kor} / {recipe.cooking_method}, "
        f"{len(recipe.items)} 재료, 총 {recipe.total_base_g:.0f}g{skip_msg}{matched_msg})"
    )

    scaler = HybridScaler()
    print(f"[engine] NN_v2 모델 로드 완료, b_table 셀 {len(scaler.b_table)}개")

    results = scaler.predict(recipe.group_type_kor, recipe.items, args.n)

    print()
    print(format_cli(recipe, results, args.n))

    if not args.no_html:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = OUTPUT_DIR / f"demo_{recipe.recipe_id}_N{args.n}_{ts}.html"
        render_html(recipe, results, args.n, out_path)
        print(f"\n[report] HTML → {out_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
