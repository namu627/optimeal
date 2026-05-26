import json
import os
import pandas as pd

MAX_LENGTH = 20
PAD_TOKEN = "<PAD>"
UNK_TOKEN = "<UNK>"


class CharTokenizer:
    def __init__(self, char_to_idx: dict = None):
        if char_to_idx is not None:
            self.char_to_idx = char_to_idx
        else:
            self.char_to_idx = {PAD_TOKEN: 0, UNK_TOKEN: 1}
        self.pad_idx = self.char_to_idx[PAD_TOKEN]
        self.unk_idx = self.char_to_idx[UNK_TOKEN]

    def build_vocab(self, texts):
        chars = set()
        for text in texts:
            chars.update(list(text))
        for ch in sorted(chars):
            if ch not in self.char_to_idx:
                self.char_to_idx[ch] = len(self.char_to_idx)

    def encode(self, text: str, max_length: int = MAX_LENGTH) -> list:
        indices = [self.char_to_idx.get(ch, self.unk_idx) for ch in text[:max_length]]
        pad_len = max_length - len(indices)
        indices += [self.pad_idx] * pad_len
        return indices

    def save(self, path: str):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.char_to_idx, f, ensure_ascii=False, indent=2)

    @classmethod
    def load(cls, path: str):
        with open(path, "r", encoding="utf-8") as f:
            char_to_idx = json.load(f)
        return cls(char_to_idx=char_to_idx)

    def __len__(self):
        return len(self.char_to_idx)


def build_and_save(csv_path: str = "data/ml/train_ingredients.csv",
                   vocab_path: str = "data/ml/char_vocab.json") -> CharTokenizer:
    df = pd.read_csv(csv_path)
    tokenizer = CharTokenizer()
    tokenizer.build_vocab(df["ingredient_name"].astype(str).tolist())
    tokenizer.save(vocab_path)
    print(f"Vocab size: {len(tokenizer)}")
    print(f"Saved to: {vocab_path}")
    return tokenizer


if __name__ == "__main__":
    build_and_save()
