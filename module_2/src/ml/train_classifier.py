"""
FR-06 Step 2~3: TF-IDF + 로지스틱 회귀 분류기 학습, CV, test set 평가
입력:  data/ml/train_ingredients.csv
출력:  data/ml/classifier_cv_results.json
       data/ml/classification_report.txt
       data/ml/classifier.pkl  (Step 5 직렬화)
"""

import json
import os
import warnings

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
)
from sklearn.model_selection import StratifiedKFold, cross_validate, train_test_split
from sklearn.pipeline import Pipeline

warnings.filterwarnings("ignore")

DATA_PATH = "data/ml/train_ingredients.csv"
OUTPUT_DIR = "data/ml"
CV_RESULTS_PATH = os.path.join(OUTPUT_DIR, "classifier_cv_results.json")
REPORT_PATH = os.path.join(OUTPUT_DIR, "classification_report.txt")
MODEL_PATH = os.path.join(OUTPUT_DIR, "classifier.pkl")

CATEGORIES = ["주재료", "부재료", "양념류", "수분류", "유지류"]

PASS_MACRO_F1 = 0.80
PASS_MICRO_F1 = 0.85
PASS_WEIGHTED_F1 = 0.83


def build_pipeline() -> Pipeline:
    return Pipeline([
        ("tfidf", TfidfVectorizer(
            analyzer="char",
            ngram_range=(2, 3),
            max_features=5000,
            sublinear_tf=True,
        )),
        ("logreg", LogisticRegression(
            solver="lbfgs",
            max_iter=1000,
            C=1.0,
            class_weight="balanced",
            multi_class="multinomial",
            random_state=42,
        )),
    ])


def run_cv(clf: Pipeline, X: pd.Series, y: pd.Series) -> dict:
    cv = StratifiedKFold(n_splits=5, shuffle=True, random_state=42)
    scoring = ["accuracy", "f1_macro", "f1_micro", "f1_weighted"]
    results = cross_validate(clf, X, y, cv=cv, scoring=scoring, return_train_score=False)

    summary = {}
    for metric in scoring:
        key = f"test_{metric}"
        scores = results[key].tolist()
        summary[metric] = {
            "fold_scores": [round(s, 4) for s in scores],
            "mean": round(float(np.mean(scores)), 4),
            "std": round(float(np.std(scores)), 4),
        }
    return summary


def format_confusion_matrix(cm: np.ndarray, labels: list) -> str:
    col_width = max(len(l) for l in labels) + 2
    header = " " * col_width + "".join(l.ljust(col_width) for l in labels)
    lines = [header]
    for i, row in enumerate(cm):
        line = labels[i].ljust(col_width) + "".join(str(v).ljust(col_width) for v in row)
        lines.append(line)
    return "\n".join(lines)


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    df = pd.read_csv(DATA_PATH)
    df = df[df["category"].isin(CATEGORIES)].dropna(subset=["ingredient_name", "category"])
    X = df["ingredient_name"]
    y = df["category"]

    print(f"전체 데이터: {len(df)}건")
    print("카테고리 분포:")
    for cat, cnt in y.value_counts().items():
        print(f"  {cat}: {cnt}건")

    # --- Step 2: 5-Fold CV ---
    print("\n=== Step 2: 5-Fold Stratified CV ===")
    clf_cv = build_pipeline()
    cv_results = run_cv(clf_cv, X, y)

    for metric, info in cv_results.items():
        print(f"  {metric}: mean={info['mean']:.4f} ± {info['std']:.4f}")

    with open(CV_RESULTS_PATH, "w", encoding="utf-8") as f:
        json.dump(cv_results, f, ensure_ascii=False, indent=2)
    print(f"\n[산출물] {CV_RESULTS_PATH}")

    # --- Step 3: train/test split 평가 ---
    print("\n=== Step 3: test set 평가 (80/20 stratified split) ===")
    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.2, stratify=y, random_state=42
    )

    clf_final = build_pipeline()
    clf_final.fit(X_train, y_train)
    y_pred = clf_final.predict(X_test)

    acc = accuracy_score(y_test, y_pred)
    macro_f1 = f1_score(y_test, y_pred, average="macro", zero_division=0)
    micro_f1 = f1_score(y_test, y_pred, average="micro", zero_division=0)
    weighted_f1 = f1_score(y_test, y_pred, average="weighted", zero_division=0)

    # 테스트 셋에 실제로 등장하는 레이블만 추출
    present_labels = [l for l in CATEGORIES if l in y_test.unique()]
    cr = classification_report(y_test, y_pred, labels=present_labels, zero_division=0)
    cm = confusion_matrix(y_test, y_pred, labels=present_labels)

    print(f"  Accuracy:    {acc:.4f}")
    print(f"  Macro-F1:    {macro_f1:.4f}  {'PASS' if macro_f1 >= PASS_MACRO_F1 else 'FAIL'} (>= {PASS_MACRO_F1})")
    print(f"  Micro-F1:    {micro_f1:.4f}  {'PASS' if micro_f1 >= PASS_MICRO_F1 else 'FAIL'} (>= {PASS_MICRO_F1})")
    print(f"  Weighted-F1: {weighted_f1:.4f}  {'PASS' if weighted_f1 >= PASS_WEIGHTED_F1 else 'FAIL'} (>= {PASS_WEIGHTED_F1})")

    report_lines = [
        "=" * 60,
        "FR-06 ML 분류기 (TF-IDF + 로지스틱 회귀) — test set 평가 리포트",
        "=" * 60,
        f"학습 데이터: {DATA_PATH}",
        f"train 크기: {len(X_train)}건 / test 크기: {len(X_test)}건",
        "",
        "[ 종합 지표 ]",
        f"  Accuracy:    {acc:.4f}",
        f"  Macro-F1:    {macro_f1:.4f}  {'PASS' if macro_f1 >= PASS_MACRO_F1 else 'FAIL'} (목표 >= {PASS_MACRO_F1})",
        f"  Micro-F1:    {micro_f1:.4f}  {'PASS' if micro_f1 >= PASS_MICRO_F1 else 'FAIL'} (목표 >= {PASS_MICRO_F1})",
        f"  Weighted-F1: {weighted_f1:.4f}  {'PASS' if weighted_f1 >= PASS_WEIGHTED_F1 else 'FAIL'} (목표 >= {PASS_WEIGHTED_F1})",
        "",
        "[ Per-class Precision / Recall / F1 ]",
        cr,
        "",
        "[ Confusion Matrix ]",
        f"  (행=실제, 열=예측) 레이블 순서: {present_labels}",
        format_confusion_matrix(cm, present_labels),
        "",
        "[ 5-Fold CV 결과 ]",
    ]
    for metric, info in cv_results.items():
        report_lines.append(
            f"  {metric}: mean={info['mean']:.4f} ± {info['std']:.4f}  folds={info['fold_scores']}"
        )

    report_text = "\n".join(report_lines)
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_text)
    print(f"\n[산출물] {REPORT_PATH}")

    # --- Step 5: 모델 직렬화 ---
    joblib.dump(clf_final, MODEL_PATH)
    print(f"[산출물] {MODEL_PATH}")


if __name__ == "__main__":
    main()
