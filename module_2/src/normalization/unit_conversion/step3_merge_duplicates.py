import pandas as pd
import numpy as np

# 데이터 읽기
df = pd.read_csv('extracted_with_convwt.csv')

# 재료명 + 단위명으로 그룹화하여 통계 계산
result = []

for (ingredient, unit), group in df.groupby(['INGR_NM', 'UNIT_NM']):
    conv_wts = group['CONV_WT'].values
    
    # 통계 계산
    mean_wt = np.mean(conv_wts)
    std_wt = np.std(conv_wts, ddof=1) if len(conv_wts) > 1 else 0
    min_wt = np.min(conv_wts)
    max_wt = np.max(conv_wts)
    
    result.append({
        'INGR_NM': ingredient,
        'UNIT_NM': unit,
        'CONV_WT': round(mean_wt, 2),
        'STD': round(std_wt, 2),
        'MIN': round(min_wt, 2),
        'MAX': round(max_wt, 2),
        'SAMPLE_COUNT': len(conv_wts)
    })

# 데이터프레임 생성
df_merged = pd.DataFrame(result)

# 정렬 (재료명 가나다순)
df_merged = df_merged.sort_values('INGR_NM')

print(f"통합 완료! 총 {len(df_merged)}개 항목")
print("\n처음 10개:")
print(df_merged.head(10))

# 저장
df_merged.to_csv('merged_data.csv', index=False, encoding='utf-8-sig')
print("\nmerged_data.csv 저장 완료!")