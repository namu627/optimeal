import pandas as pd

# 데이터 읽기
df = pd.read_csv('with_confidence.csv')

# 필요한 컬럼만 선택
final_columns = [
    'INGR_NM',          # 재료명
    'UNIT_NM',          # 단위명
    'CONV_WT',          # 환산 중량(g)
    'TOLERANCE_PCT',    # 허용 오차율(%)
    'CONFIDENCE_LVL',   # 신뢰도 (1~3)
    'SAMPLE_COUNT'      # 샘플 수 (참고용)
]

df_final = df[final_columns].copy()

# CONDITION_DESC 컬럼 추가 (비어있음 - 나중에 수작업으로 채움)
df_final['CONDITION_DESC'] = None

# 컬럼 순서 재정렬
df_final = df_final[[
    'INGR_NM',
    'UNIT_NM',
    'CONV_WT',
    'CONDITION_DESC',
    'TOLERANCE_PCT',
    'CONFIDENCE_LVL',
    'SAMPLE_COUNT'
]]

# 재료명 가나다순 정렬
df_final = df_final.sort_values('INGR_NM')

# Excel 파일로 저장
with pd.ExcelWriter('비정형단위_환산_테이블_최종.xlsx', engine='openpyxl') as writer:
    # 메인 테이블
    df_final.to_excel(writer, sheet_name='비정형단위 환산 테이블', index=False)
    
    # 통계 시트
    stats = pd.DataFrame({
        '항목': ['총 데이터 수', '재료 종류', '단위 종류', '평균 CONV_WT', '평균 TOLERANCE_PCT'],
        '값': [
            len(df_final),
            df_final['INGR_NM'].nunique(),
            df_final['UNIT_NM'].nunique(),
            f"{df_final['CONV_WT'].mean():.2f}g",
            f"{df_final['TOLERANCE_PCT'].mean():.1f}%"
        ]
    })
    stats.to_excel(writer, sheet_name='통계', index=False)

print("✅ 최종 Excel 파일 생성 완료!")
print(f"   파일명: 비정형단위_환산_테이블_최종.xlsx")
print(f"   항목 수: {len(df_final)}개")