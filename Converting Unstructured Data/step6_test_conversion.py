import pandas as pd

# 환산 테이블 읽기
df = pd.read_excel('비정형단위_환산_테이블_최종.xlsx', 
                   sheet_name='비정형단위 환산 테이블')

def test_conversion(ingredient, quantity, unit):
    """
    환산 테스트 함수
    """
    # 테이블에서 조회
    result = df[(df['INGR_NM'] == ingredient) & (df['UNIT_NM'] == unit)]
    
    if len(result) == 0:
        print(f"❌ 찾을 수 없음: {ingredient} - {unit}")
        return None
    
    conv_wt = result.iloc[0]['CONV_WT']
    calculated_g = quantity * conv_wt
    
    print(f"✅ {ingredient} {quantity}{unit} = {calculated_g}g")
    return calculated_g

# 테스트 케이스
print("=" * 60)
print("환산 테이블 테스트")
print("=" * 60)

test_cases = [
    ("연두부", 1, "모"),
    ("설탕", 2, "작은술"),
    ("간장", 1.5, "큰술"),
    ("양파", 0.5, "개"),
    ("달걀", 3, "개"),
]

for ingredient, qty, unit in test_cases:
    test_conversion(ingredient, qty, unit)

print("=" * 60)