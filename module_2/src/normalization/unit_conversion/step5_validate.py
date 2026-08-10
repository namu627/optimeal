import pandas as pd

# 최종 데이터 읽기
df = pd.read_csv('with_confidence.csv')

print("=" * 60)
print("데이터 품질 검증 보고서")
print("=" * 60)

# 1. 기본 통계
print(f"\n총 항목 수: {len(df)}개")
print(f"재료 종류: {df['INGR_NM'].nunique()}개")
print(f"단위 종류: {df['UNIT_NM'].nunique()}개")

# 2. 결측치 확인
print("\n결측치 확인:")
print(df.isnull().sum())

# 3. 이상값 확인
print("\n이상값 확인 (CONV_WT):")
print(f"  최소값: {df['CONV_WT'].min()}")
print(f"  최대값: {df['CONV_WT'].max()}")
print(f"  평균: {df['CONV_WT'].mean():.2f}")

# 4. 비정상적으로 큰 값 찾기
outliers = df[df['CONV_WT'] > 1000]
if len(outliers) > 0:
    print(f"\n⚠️  1000g 초과 항목: {len(outliers)}개")
    print(outliers[['INGR_NM', 'UNIT_NM', 'CONV_WT']])

# 5. 비정상적으로 작은 값 찾기
tiny_values = df[df['CONV_WT'] < 0.1]
if len(tiny_values) > 0:
    print(f"\n⚠️  0.1g 미만 항목: {len(tiny_values)}개")
    print(tiny_values[['INGR_NM', 'UNIT_NM', 'CONV_WT']])

# 6. TOLERANCE_PCT 분포
print("\nTOLERANCE_PCT 분포:")
print(df['TOLERANCE_PCT'].describe())

# 7. CONFIDENCE_LVL 분포
print("\nCONFIDENCE_LVL 분포:")
print(df.groupby('CONFIDENCE_LVL').size())

print("\n" + "=" * 60)
print("검증 완료!")
print("=" * 60)