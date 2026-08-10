import pandas as pd

# 데이터 읽기
df = pd.read_csv('with_tolerance.csv')

def assign_confidence_level(row):
    """
    신뢰도 레벨 결정
    
    1 = 정확 (샘플 5개 이상)
    2 = 평균 (샘플 2~4개)
    3 = 추정 (샘플 1개)
    """
    count = row['SAMPLE_COUNT']
    
    if count >= 5:
        return 1
    elif count >= 2:
        return 2
    else:
        return 3

# 적용
df['CONFIDENCE_LVL'] = df.apply(assign_confidence_level, axis=1)

# 신뢰도 분포 확인
print("신뢰도 레벨 분포:")
print(df['CONFIDENCE_LVL'].value_counts().sort_index())

# 저장
df.to_csv('with_confidence.csv', index=False, encoding='utf-8-sig')
print("\nwith_confidence.csv 저장 완료!")
