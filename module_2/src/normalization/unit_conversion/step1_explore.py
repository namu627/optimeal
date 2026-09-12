import json

# JSON 파일 읽기
with open('RCP_PARTS_DTLS.json', 'r', encoding='utf-8') as f:
    data = json.load(f)

# 기본 정보 출력
print(f"총 레시피 재료 개수: {len(data)}개")
print("\n첫 번째 레시피 재료 예시:")
print(data[0])

# 처음 5개 재료 출력
print("\n처음 5개 레시피:")
for i, recipe in enumerate(data[:5], 1):
    print(f"\n{i}. {recipe['RCP_PARTS_DTLS'][:100]}...")


# 식품안전나라 DB에는 레시피가 1146개 있다.