import json
import re
import pandas as pd

# JSON 파일 읽기
with open('RCP_PARTS_DTLS.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 추출한 데이터를 저장할 리스트
extracted_data = []

# 정규표현식 패턴
# "재료명 숫자g(비정형단위)" 형태를 찾음
pattern = r'([가-힣a-zA-Z]+)\s+(\d+\.?\d*)g\s*\(([^)]+)\)'

# 모든 레시피 순회
for recipe_item in data:
    text = recipe_item.get('RCP_PARTS_DTLS', '')
    
    # 패턴에 맞는 모든 부분 찾기
    matches = re.findall(pattern, text)
    
    for ingredient, grams, unit_expr in matches:
        extracted_data.append({
            'INGR_NM': ingredient,      # 재료명
            'GRAMS': float(grams),       # g 수
            'UNIT_EXPR': unit_expr       # 비정형 단위 표현
        })

# 데이터프레임으로 변환
df = pd.DataFrame(extracted_data)

# 결과 확인
print(f"총 추출된 데이터: {len(df)}개")
print("\n처음 10개:")
print(df.head(10))

# 임시 저장
df.to_csv('extracted_raw.csv', index=False, encoding='utf-8-sig')
print("\n추출 완료! extracted_raw.csv 파일 확인하세요.")
