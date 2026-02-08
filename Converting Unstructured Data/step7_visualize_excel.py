import pandas as pd
import matplotlib.pyplot as plt
import platform

# 1. 엑셀 파일 불러오기
# '파일이름.xlsx'를 실제 엑셀 파일명으로 변경해주세요.
# 파일이 같은 폴더에 있어야 합니다.
filename = '비정형단위_환산_테이블_최종.xlsx' 
df = pd.read_excel(filename)

# 2. 한글 폰트 설정 (맥/윈도우 호환)
# 그래프 제목에 한글('허용 오차율 분포')이 있으므로 폰트 설정이 필수입니다.
if platform.system() == 'Darwin': # 맥
    plt.rc('font', family='AppleGothic')
elif platform.system() == 'Windows': # 윈도우
    plt.rc('font', family='Malgun Gothic')

# 마이너스 기호 깨짐 방지
plt.rcParams['axes.unicode_minus'] = False

# 3. 데이터 확인 (제대로 불러왔는지 확인용)
print(f"데이터 크기: {df.shape}")
print(df.head())

# 4. 그래프 그리기 (작성하신 코드)
try:
    plt.figure(figsize=(10, 6))
    
    # 엑셀에 'TOLERANCE_PCT'라는 컬럼 이름이 정확히 있어야 합니다.
    df['TOLERANCE_PCT'].hist(bins=30)
    
    plt.xlabel('TOLERANCE_PCT (%)')
    plt.ylabel('Frequency')
    plt.title('허용 오차율 분포')
    
    # 이미지 파일로 저장
    plt.savefig('tolerance_distribution.png')
    print("\n성공! 그래프 저장 완료: tolerance_distribution.png")
    
except KeyError:
    print("\n[에러] 엑셀 파일에 'TOLERANCE_PCT'라는 컬럼이 없습니다.")
    print("엑셀 파일의 첫 번째 줄(헤더)을 확인해주세요.")