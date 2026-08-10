import pandas as pd

# 데이터 읽기
df = pd.read_csv('extracted_with_convwt.csv')

# 재료명 + 단위명 기준으로 그룹화
grouped = df.groupby(['INGR_NM', 'UNIT_NM'])

# 중복이 있는 그룹만 찾기
duplicates = []

for (ingredient, unit), group in grouped:
    if len(group) > 1:
        duplicates.append({
            'INGR_NM': ingredient,
            'UNIT_NM': unit,
            'COUNT': len(group),
            'VALUES': group['CONV_WT'].tolist()
        })

# 중복 통계
print(f"전체 그룹 수: {grouped.ngroups}개")
print(f"중복 있는 그룹: {len(duplicates)}개")

# 중복이 많은 순으로 정렬
df_dup = pd.DataFrame(duplicates)
df_dup = df_dup.sort_values('COUNT', ascending=False)

print("\n중복이 가장 많은 10개:")
print(df_dup.head(10))

# 저장
df_dup.to_csv('duplicates_report.csv', index=False, encoding='utf-8-sig')