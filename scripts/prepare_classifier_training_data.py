"""
FR-06 Step 1: ML 분류기 학습 데이터 추출
출력: data/ml/train_ingredients.csv (컬럼: ingredient_name, category)
"""

import re
import os
import pandas as pd

OUTPUT_DIR = "data/ml"
OUTPUT_PATH = os.path.join(OUTPUT_DIR, "train_ingredients.csv")
XLSX_PATH = "data/raw/소규모_레시피_DB_남유찬_v0_10.xlsx"
DF_B_PATH = "data/raw/df_B.csv"

ROLE_TO_CATEGORY = {
    "주재료": "주재료",
    "부재료": "부재료",
    "조미료": "양념류",
    "양념": "양념류",
}

DERIVED_TO_CATEGORY = {
    "oil_fat": "유지류",
    "water_base": "수분류",
}


def preprocess(name: str) -> str:
    name = str(name).strip()
    name = re.sub(r"[(\[（【].*?[)\]）】]", "", name)
    name = re.sub(r"\d+[가-힣a-zA-Z]*", "", name)
    # 한글/영문 없이 숫자+알파벳만 있는 코드 형식 제거 (예: S0001)
    if re.fullmatch(r"[A-Za-z0-9_\-\s]*", name) and not re.search(r"[가-힣]", name):
        return ""
    return name.strip()


def load_xlsx_source() -> pd.DataFrame:
    df = pd.read_excel(XLSX_PATH, header=1)
    # 실제 데이터 행만 (recipe_id가 있고 헤더 반복 행 제외)
    df = df[df["recipe_id"].notna()]
    df = df[df["recipe_id"].astype(str) != "레시피 ID(임시)"]

    records = []
    for i in range(1, 29):
        name_col = f"ingredient_{i}_name"
        role_col = f"ingredient_{i}_role"
        if name_col not in df.columns or role_col not in df.columns:
            continue
        chunk = df[[name_col, role_col]].copy()
        chunk.columns = ["ingredient_name", "role"]
        chunk = chunk[chunk["ingredient_name"].notna() & chunk["role"].notna()]
        records.append(chunk)

    long_df = pd.concat(records, ignore_index=True)
    long_df["category"] = long_df["role"].map(ROLE_TO_CATEGORY)
    long_df = long_df[long_df["category"].notna()][["ingredient_name", "category"]]
    return long_df


def load_dfb_source() -> pd.DataFrame:
    df = pd.read_csv(DF_B_PATH)
    df = df[df["derived_category"].notna()]
    df = df[df["derived_category"].isin(DERIVED_TO_CATEGORY)]
    result = df[["ingredient_name", "derived_category"]].copy()
    result["category"] = result["derived_category"].map(DERIVED_TO_CATEGORY)
    return result[["ingredient_name", "category"]]


def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)

    print("=== Step 1: 학습 데이터 추출 ===")
    print(f"[1/4] xlsx 소스 로드: {XLSX_PATH}")
    xlsx_df = load_xlsx_source()
    print(f"      xlsx 원본 행 수: {len(xlsx_df)}")

    print(f"[2/4] df_B.csv 소스 로드: {DF_B_PATH}")
    dfb_df = load_dfb_source()
    print(f"      df_B 원본 행 수: {len(dfb_df)}")

    print("[3/4] 합치기 및 전처리")
    # df_B(수분류/유지류)를 먼저 배치하여 중복 제거 시 우선권 부여
    combined = pd.concat([dfb_df, xlsx_df], ignore_index=True)
    combined["ingredient_name"] = combined["ingredient_name"].apply(preprocess)
    combined = combined[combined["ingredient_name"] != ""]
    combined = combined[combined["ingredient_name"].notna()]

    print("[4/4] 중복 제거 (ingredient_name 기준 첫 번째 유지)")
    combined = combined.drop_duplicates(subset="ingredient_name", keep="first")

    combined.to_csv(OUTPUT_PATH, index=False, encoding="utf-8-sig")

    print(f"\n=== 카테고리별 행 수 ===")
    counts = combined["category"].value_counts()
    for cat, cnt in counts.items():
        flag = " [!] 20건 미만" if cnt < 20 else ""
        print(f"  {cat}: {cnt}건{flag}")
    print(f"  합계: {len(combined)}건")
    print(f"\n산출물 저장: {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
