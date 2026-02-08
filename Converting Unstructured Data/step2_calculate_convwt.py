import pandas as pd

# 파싱된 데이터 읽기
df = pd.read_csv('extracted_parsed.csv')

# CONV_WT 계산: g수 / 수량, = 1 단위 당 g 수.
# e.g., 칵테일 새우는 5마리에 20g, 1마리당 4g.
df['CONV_WT'] = df['GRAMS'] / df['QUANTITY']

# 결과 확인
print("CONV_WT 계산 완료!")
print("\n처음 10개:")
print(df[['INGR_NM', 'UNIT_NM', 'QUANTITY', 'GRAMS', 'CONV_WT']].head(10))

# 저장
df.to_csv('extracted_with_convwt.csv', index=False, encoding='utf-8-sig')