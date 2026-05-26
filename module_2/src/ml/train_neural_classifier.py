import os
import sys
import json
import random
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from sklearn.model_selection import train_test_split
from sklearn.utils.class_weight import compute_class_weight
from sklearn.metrics import f1_score

sys.path.insert(0, ".")
from module_2.src.ml.char_tokenizer import CharTokenizer, build_and_save
from module_2.src.ml.neural_classifier import CharCNNClassifier

# ── 시드 고정 ──────────────────────────────────────────────
SEED = 42
random.seed(SEED)
np.random.seed(SEED)
torch.manual_seed(SEED)

# ── 상수 ──────────────────────────────────────────────────
CSV_PATH      = "data/ml/train_ingredients.csv"
VOCAB_PATH    = "data/ml/char_vocab.json"
MODEL_PATH    = "data/ml/neural_classifier.pt"
CONFIG_PATH   = "data/ml/neural_classifier_config.json"

BATCH_SIZE    = 32
EPOCHS        = 50
LR            = 0.001
PATIENCE      = 10
MAX_LENGTH    = 20
DEVICE        = torch.device("cpu")


# ── Dataset ───────────────────────────────────────────────
class IngredientDataset(Dataset):
    def __init__(self, texts, labels, tokenizer):
        self.encodings = [tokenizer.encode(t) for t in texts]
        self.labels    = labels

    def __len__(self):
        return len(self.labels)

    def __getitem__(self, idx):
        x = torch.tensor(self.encodings[idx], dtype=torch.long)
        y = torch.tensor(self.labels[idx],    dtype=torch.long)
        return x, y


# ── 메인 학습 함수 ─────────────────────────────────────────
def train():
    os.makedirs("data/ml", exist_ok=True)

    # 1. 데이터 로드
    df = pd.read_csv(CSV_PATH)
    texts  = df["ingredient_name"].astype(str).tolist()
    cats   = df["category"].tolist()

    # 2. 레이블 인코딩
    label_list = sorted(set(cats))
    label2idx  = {l: i for i, l in enumerate(label_list)}
    idx2label  = {i: l for l, i in label2idx.items()}
    labels     = [label2idx[c] for c in cats]

    # 3. stratified split: 60/20/20
    # 클래스 샘플이 4개 미만이면 val/test 분할 불가 → 해당 클래스는 train 전용
    from collections import Counter
    cnt = Counter(labels)
    minority_idxs = [i for i, (t, l) in enumerate(zip(texts, labels)) if cnt[l] < 4]
    majority_idxs = [i for i in range(len(texts)) if i not in set(minority_idxs)]

    X_min  = [texts[i]  for i in minority_idxs]
    y_min  = [labels[i] for i in minority_idxs]
    X_maj  = [texts[i]  for i in majority_idxs]
    y_maj  = [labels[i] for i in majority_idxs]

    X_maj_tr, X_tmp, y_maj_tr, y_tmp = train_test_split(
        X_maj, y_maj, test_size=0.4, stratify=y_maj, random_state=SEED
    )
    X_val, X_test, y_val, y_test = train_test_split(
        X_tmp, y_tmp, test_size=0.5, stratify=y_tmp, random_state=SEED
    )
    X_train = X_maj_tr + X_min
    y_train = y_maj_tr + y_min
    print(f"Train: {len(X_train)} / Val: {len(X_val)} / Test: {len(X_test)}")
    print(f"  (소수 클래스 train 전용 배치: {len(X_min)}건)")

    # 4. 토크나이저
    if not os.path.exists(VOCAB_PATH):
        tokenizer = build_and_save(CSV_PATH, VOCAB_PATH)
    else:
        tokenizer = CharTokenizer.load(VOCAB_PATH)

    # 5. DataLoader
    train_ds = IngredientDataset(X_train, y_train, tokenizer)
    val_ds   = IngredientDataset(X_val,   y_val,   tokenizer)
    test_ds  = IngredientDataset(X_test,  y_test,  tokenizer)

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE)
    test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE)

    # 6. class_weight
    cw = compute_class_weight("balanced", classes=np.unique(y_train), y=np.array(y_train))
    class_weights = torch.tensor(cw, dtype=torch.float).to(DEVICE)
    print(f"Class weights: { {idx2label[i]: round(w, 4) for i, w in enumerate(cw)} }")

    # 7. 모델 / 손실 / 옵티마이저
    model     = CharCNNClassifier(vocab_size=len(tokenizer)).to(DEVICE)
    criterion = nn.CrossEntropyLoss(weight=class_weights)
    optimizer = torch.optim.Adam(model.parameters(), lr=LR)

    # 8. 학습 루프
    best_val_f1   = -1.0
    patience_cnt  = 0
    best_state    = None

    for epoch in range(1, EPOCHS + 1):
        # — train —
        model.train()
        train_loss = 0.0
        for xb, yb in train_loader:
            xb, yb = xb.to(DEVICE), yb.to(DEVICE)
            optimizer.zero_grad()
            logits = model(xb)
            loss   = criterion(logits, yb)
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * len(yb)
        train_loss /= len(train_ds)

        # — validation —
        model.eval()
        val_loss = 0.0
        all_preds, all_true = [], []
        with torch.no_grad():
            for xb, yb in val_loader:
                xb, yb = xb.to(DEVICE), yb.to(DEVICE)
                logits = model(xb)
                loss   = criterion(logits, yb)
                val_loss += loss.item() * len(yb)
                preds = logits.argmax(dim=1)
                all_preds.extend(preds.cpu().tolist())
                all_true.extend(yb.cpu().tolist())
        val_loss /= len(val_ds)
        val_f1 = f1_score(all_true, all_preds, average="macro", zero_division=0)

        print(f"Epoch {epoch:3d} | Train Loss: {train_loss:.4f} | "
              f"Val Loss: {val_loss:.4f} | Val Macro-F1: {val_f1:.4f}")

        # — early stopping —
        if val_f1 > best_val_f1:
            best_val_f1  = val_f1
            patience_cnt = 0
            best_state   = {k: v.clone() for k, v in model.state_dict().items()}
        else:
            patience_cnt += 1
            if patience_cnt >= PATIENCE:
                print(f"\n[Early Stopping] patience {PATIENCE} 소진 — epoch {epoch}에서 중단")
                break

    print(f"\n최고 Val Macro-F1: {best_val_f1:.4f}")

    # 9. 최적 가중치로 복원 후 저장
    model.load_state_dict(best_state)
    torch.save(best_state, MODEL_PATH)
    print(f"모델 저장: {MODEL_PATH}")

    # 10. 설정 저장
    config = {
        "vocab_size":   len(tokenizer),
        "embed_dim":    128,
        "num_filters":  64,
        "kernel_sizes": [2, 3, 4],
        "num_classes":  5,
        "dropout":      0.3,
        "max_length":   MAX_LENGTH,
        "label2idx":    label2idx,
        "idx2label":    {str(k): v for k, v in idx2label.items()},
        "batch_size":   BATCH_SIZE,
        "lr":           LR,
        "epochs":       EPOCHS,
        "patience":     PATIENCE,
        "random_state": SEED,
    }
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"설정 저장: {CONFIG_PATH}")

    return model, test_loader, label_list, idx2label


if __name__ == "__main__":
    train()
