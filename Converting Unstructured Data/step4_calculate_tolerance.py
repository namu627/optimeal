import pandas as pd
import numpy as np

# 통합 데이터 읽기
df = pd.read_csv('merged_data.csv')

def calculate_tolerance_pct(row):
    """
    허용 오차율 계산
    
    방법: 2σ / mean × 100
    (통계학적으로 95% 신뢰구간)
    """
    mean = row['CONV_WT']
    std = row['STD']
    
    if std == 0 or row['SAMPLE_COUNT'] == 1:
        # 데이터가 1개뿐이면 기본값
        return 10.0
    
    # 2σ 방식
    tolerance = (2 * std / mean) * 100
    
    # 최소값 설정 (너무 작으면 5%)
    tolerance = max(5.0, tolerance)
    
    # 최대값 제한 (너무 크면 100%)
    tolerance = min(100.0, tolerance)
    
    return round(tolerance, 1)

# 모든 행에 적용
df['TOLERANCE_PCT'] = df.apply(calculate_tolerance_pct, axis=1)

# 결과 확인
print("TOLERANCE_PCT 계산 완료!")
print("\n통계:")
print(df['TOLERANCE_PCT'].describe())

print("\n처음 10개:")
print(df[['INGR_NM', 'UNIT_NM', 'CONV_WT', 'STD', 'TOLERANCE_PCT']].head(10))

# 저장
df.to_csv('with_tolerance.csv', index=False, encoding='utf-8-sig')
print("\nwith_tolerance.csv 저장 완료!")