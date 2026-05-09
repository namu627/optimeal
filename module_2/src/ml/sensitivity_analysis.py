"""
FR-06 Step 4: 민감도 분석
오분류 재료명의 b값 오차가 MAPE에 미치는 영향 정량 분석
출력: data/ml/sensitivity_analysis.csv
"""

import os

import joblib
import pandas as pd
from sklearn.model_selection import train_test_split

DATA_PATH = "data/ml/train_ingredients.csv"
MODEL_PATH = "data/ml/classifier.pkl"
OUTPUT_PATH = "data/ml/sensitivity_analysis.csv"

CATEGORY_B = {
    "주재료": 0.9330,
    "부재료": 0.8950,
    "양념류": 0.6550,
    "수분류": 1.0580,
    "유지류": 0.7570,
}

CATEGORIES = list(CATEGORY_B.keys())


def main():
    os.makedirs(os.path.dirname(OUTPUT_PATH), exist_ok=True)

    df = pd.read_csv(DATA_PATH)
    df = df[df["category"].isin(CATEGORIES)].dropna(subset=["ingredient_name", "category"])

    _, X_test, _, y_test = train_test_split(
        df["ingredient_name"], df["category"], test_size=0.2, stratify=df["category"], random_state=42
    )

    clf = joblib.load(MODEL_PATH)
    y_pred = clf.predict(X_test)

    results = []
    for name, true_cat, pred_cat in zip(X_test, y_test, y_pred):
        if true_cat == pred_cat:
            continue
        true_b = CATEGORY_B.get(true_cat)
        pred_b = CATEGORY_B.get(pred_cat)
        if true_b is None or pred_b is None:
            continue
        b_error = abs(pred_b - true_b)
        b_error_pct = b_error / true_b * 100
        results.append({
            "ingredient_name": name,
            "true_category": true_cat,
            "pred_category": pred_cat,
            "true_b": true_b,
            "pred_b": pred_b,
            "b_error": round(b_error, 4),
            "b_error_pct": round(b_error_pct, 2),
        })

    result_df = pd.DataFrame(results)
    result_df = result_df.sort_values("b_error_pct", ascending=False).reset_index(drop=True)
    result_df.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"=== Step 4: 민감도 분석 ===")
    print(f"오분류 케이스: {len(result_df)}건")
    if len(result_df) > 0:
        print(f"b_error 평균: {result_df['b_error'].mean():.4f}")
        print(f"b_error_pct 평균 (MAPE): {result_df['b_error_pct'].mean():.2f}%")
        print(f"b_error_pct 최대: {result_df['b_error_pct'].max():.2f}%")
        print()
        print("[ 오분류 패턴별 평균 b_error_pct ]")
        pattern = result_df.groupby(["true_category", "pred_category"])["b_error_pct"].agg(["count", "mean"])
        print(pattern.to_string())
    print(f"\n[산출물] {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
