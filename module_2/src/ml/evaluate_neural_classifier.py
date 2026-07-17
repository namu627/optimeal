import os
import sys
import json
import numpy as np
import pandas as pd
import torch
from collections import Counter
from sklearn.model_selection import train_test_split
from sklearn.metrics import (
    accuracy_score, f1_score,
    precision_recall_fscore_support, confusion_matrix,
    classification_report,
)

sys.path.insert(0, ".")
from module_2.src.ml.char_tokenizer import CharTokenizer
from module_2.src.ml.neural_classifier import CharCNNClassifier
from module_2.src.ml.train_neural_classifier import IngredientDataset
from torch.utils.data import DataLoader

# ── 경로 ──────────────────────────────────────────────────
CSV_PATH    = "data/ml/train_ingredients.csv"
VOCAB_PATH  = "data/ml/char_vocab.json"
MODEL_PATH  = "data/ml/neural_classifier.pt"
CONFIG_PATH = "data/ml/neural_classifier_config.json"
REPORT_PATH = "data/ml/neural_classification_report.txt"

SEED   = 42
DEVICE = torch.device("cpu")

# TF-IDF 기준값
TFIDF = {
    "accuracy":   0.8178,
    "macro_f1":   0.3761,
    "micro_f1":   None,
    "weighted_f1": None,
    "per_class": {
        "주재료": {"f1": 0.18},
        "부재료": {"f1": 0.88},
        "양념류": {"f1": 0.82},
        "수분류": {"f1": None},
        "유지류": {"f1": None},
    },
}


def rebuild_test_set():
    """학습 스크립트와 동일한 시드/로직으로 test set 재현."""
    df     = pd.read_csv(CSV_PATH)
    texts  = df["ingredient_name"].astype(str).tolist()
    cats   = df["category"].tolist()

    label_list = sorted(set(cats))
    label2idx  = {l: i for i, l in enumerate(label_list)}
    idx2label  = {i: l for l, i in label2idx.items()}
    labels     = [label2idx[c] for c in cats]

    cnt           = Counter(labels)
    minority_idxs = [i for i, l in enumerate(labels) if cnt[l] < 4]
    majority_idxs = [i for i in range(len(texts)) if i not in set(minority_idxs)]

    X_maj = [texts[i]  for i in majority_idxs]
    y_maj = [labels[i] for i in majority_idxs]

    _, X_tmp, _, y_tmp = train_test_split(
        X_maj, y_maj, test_size=0.4, stratify=y_maj, random_state=SEED
    )
    _, X_test, _, y_test = train_test_split(
        X_tmp, y_tmp, test_size=0.5, stratify=y_tmp, random_state=SEED
    )
    return X_test, y_test, label_list, idx2label


def evaluate():
    os.makedirs("data/ml", exist_ok=True)

    # 1. test set 재현
    X_test, y_test, label_list, idx2label = rebuild_test_set()

    # 2. 토크나이저 / 모델 로드
    tokenizer = CharTokenizer.load(VOCAB_PATH)
    with open(CONFIG_PATH, encoding="utf-8") as f:
        config = json.load(f)

    model = CharCNNClassifier(
        vocab_size   = config["vocab_size"],
        embed_dim    = config["embed_dim"],
        num_filters  = config["num_filters"],
        kernel_sizes = config["kernel_sizes"],
        num_classes  = config["num_classes"],
        dropout      = config["dropout"],
    )
    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.eval()

    # 3. 추론
    test_ds     = IngredientDataset(X_test, y_test, tokenizer)
    test_loader = DataLoader(test_ds, batch_size=32)

    all_preds, all_true = [], []
    with torch.no_grad():
        for xb, yb in test_loader:
            preds = model(xb).argmax(dim=1)
            all_preds.extend(preds.tolist())
            all_true.extend(yb.tolist())

    # 4. 지표 계산
    accuracy    = accuracy_score(all_true, all_preds)
    macro_f1    = f1_score(all_true, all_preds, average="macro",    zero_division=0)
    micro_f1    = f1_score(all_true, all_preds, average="micro",    zero_division=0)
    weighted_f1 = f1_score(all_true, all_preds, average="weighted", zero_division=0)

    prec, rec, f1, support = precision_recall_fscore_support(
        all_true, all_preds, labels=list(range(len(label_list))),
        zero_division=0
    )
    cm = confusion_matrix(all_true, all_preds, labels=list(range(len(label_list))))

    # 5. 리포트 텍스트 구성
    lines = []
    sep  = "=" * 65
    sep2 = "-" * 65

    lines += [sep, "  신경망(CharCNN) vs TF-IDF 분류 성능 비교 리포트", sep, ""]

    # 전체 지표 비교표
    lines += ["[ 전체 지표 비교 ]", ""]
    lines += [f"{'지표':<18} {'CharCNN':>10} {'TF-IDF':>10} {'개선':>10}"]
    lines += [sep2]

    def fmt_diff(nn_val, tf_val):
        if tf_val is None:
            return "    N/A"
        return f"  {nn_val - tf_val:+.4f}"

    lines += [f"{'Accuracy':<18} {accuracy:>10.4f} {TFIDF['accuracy']:>10.4f} {fmt_diff(accuracy, TFIDF['accuracy']):>10}"]
    lines += [f"{'Macro-F1':<18} {macro_f1:>10.4f} {TFIDF['macro_f1']:>10.4f} {fmt_diff(macro_f1, TFIDF['macro_f1']):>10}"]
    lines += [f"{'Micro-F1':<18} {micro_f1:>10.4f} {'   N/A':>10} {'':>10}"]
    lines += [f"{'Weighted-F1':<18} {weighted_f1:>10.4f} {'   N/A':>10} {'':>10}"]
    lines += [""]

    # 클래스별 지표
    lines += ["[ 클래스별 Precision / Recall / F1 / Support ]", ""]
    lines += [f"{'클래스':<10} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Support':>10}"]
    lines += [sep2]
    for i, label in enumerate(label_list):
        lines += [f"{label:<10} {prec[i]:>10.4f} {rec[i]:>10.4f} {f1[i]:>10.4f} {support[i]:>10}"]
    lines += [""]

    # 클래스별 F1 비교표
    lines += ["[ 클래스별 F1 비교 (CharCNN vs TF-IDF) ]", ""]
    lines += [f"{'클래스':<10} {'CharCNN F1':>12} {'TF-IDF F1':>12} {'개선':>10}"]
    lines += [sep2]
    for i, label in enumerate(label_list):
        tf_f1 = TFIDF["per_class"].get(label, {}).get("f1")
        tf_str  = f"{tf_f1:.4f}" if tf_f1 is not None else "   N/A"
        diff_str = fmt_diff(f1[i], tf_f1) if tf_f1 is not None else "   N/A"
        lines += [f"{label:<10} {f1[i]:>12.4f} {tf_str:>12} {diff_str:>10}"]
    lines += [""]

    # Confusion Matrix
    lines += ["[ Confusion Matrix ]", ""]
    header = f"{'':>10}" + "".join(f"{l:>10}" for l in label_list)
    lines += [header]
    lines += [sep2]
    for i, label in enumerate(label_list):
        row = f"{label:<10}" + "".join(f"{cm[i][j]:>10}" for j in range(len(label_list)))
        lines += [row]
    lines += ["", "(행: 실제 클래스 / 열: 예측 클래스)", ""]
    lines += [sep]

    report_str = "\n".join(lines)

    # 6. 콘솔 출력
    print(report_str)

    # 7. 파일 저장
    with open(REPORT_PATH, "w", encoding="utf-8") as f:
        f.write(report_str + "\n")
    print(f"\n리포트 저장: {REPORT_PATH}")


if __name__ == "__main__":
    evaluate()
