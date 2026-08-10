import re
import pandas as pd

# 이전 단계에서 저장한 CSV 읽기
df = pd.read_csv('extracted_raw.csv')

def parse_unit_expression(expr):
    """
    비정형 단위 표현을 (수량, 단위명)으로 분리
    
    예시:
    "3/4모" → (0.75, "모")
    "1큰술" → (1.0, "큰술")
    "1⅓작은술" → (1.33, "작은술")
    """
    
    # 패턴 1: 분수 형태 (3/4모)
    fraction_pattern = r'^(\d+)/(\d+)([가-힣]+)$'
    match = re.match(fraction_pattern, expr)
    if match:
        numerator = float(match.group(1))
        denominator = float(match.group(2))
        unit = match.group(3)
        quantity = numerator / denominator
        return (quantity, unit)
    
    # 패턴 2: 특수 분수 기호 (1⅓작은술)
    special_fractions = {
        '⅓': 1/3, '⅔': 2/3,
        '¼': 1/4, '¾': 3/4,
        '½': 1/2, '⅕': 1/5,
        '⅖': 2/5, '⅗': 3/5,
        '⅘': 4/5, '⅙': 1/6,
        '⅚': 5/6, '⅛': 1/8,
        '⅜': 3/8, '⅝': 5/8,
        '⅞': 7/8
    }
    
    for symbol, value in special_fractions.items():
        if symbol in expr:
            # "1⅓작은술" → "1" + "⅓" + "작은술"
            parts = expr.split(symbol)
            whole_part = parts[0]
            unit_part = parts[1]
            
            whole_num = float(whole_part) if whole_part else 0
            total = whole_num + value
            
            return (total, unit_part)
    
    # 패턴 3: 정수 + 단위 (1큰술, 5마리)
    simple_pattern = r'^(\d+\.?\d*)([가-힣]+)$'
    match = re.match(simple_pattern, expr)
    if match:
        quantity = float(match.group(1))
        unit = match.group(2)
        return (quantity, unit)
    
    # 패턴 4: 소수 + 단위 (0.5큰술)
    decimal_pattern = r'^(\d+\.\d+)([가-힣]+)$'
    match = re.match(decimal_pattern, expr)
    if match:
        quantity = float(match.group(1))
        unit = match.group(2)
        return (quantity, unit)
    
    # 패턴 5: "약간", "적당량" (수량 없음)
    if expr in ['약간', '적당량', '조금']:
        return (1.0, expr)
    
    # 파싱 실패
    return (None, None)

# 모든 행에 대해 파싱 적용
parsed_results = []

for idx, row in df.iterrows():
    quantity, unit = parse_unit_expression(row['UNIT_EXPR'])
    
    if quantity is not None:
        parsed_results.append({
            'INGR_NM': row['INGR_NM'],
            'UNIT_NM': unit,
            'QUANTITY': quantity,
            'GRAMS': row['GRAMS']
        })
    else:
        # 파싱 실패한 경우 기록
        print(f"파싱 실패: {row['UNIT_EXPR']}")

# 데이터프레임 생성
df_parsed = pd.DataFrame(parsed_results)

print(f"\n파싱 성공: {len(df_parsed)}개")
print("\n처음 10개:")
print(df_parsed.head(10))

# 저장
df_parsed.to_csv('extracted_parsed.csv', index=False, encoding='utf-8-sig')
print("\n파싱 완료! extracted_parsed.csv 파일 확인하세요.")