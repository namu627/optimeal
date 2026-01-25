import sys
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError

# Docker Compose에 적은 설정과 똑같아야 함
# 형식: postgresql://아이디:비밀번호@주소:포트/DB이름
DATABASE_URL = "postgresql://user:password@localhost:5432/optimeal"

def check_connection():
    try:
        # 1. 엔진 생성 (접속 시도)
        engine = create_engine(DATABASE_URL)
        
        # 2. 연결 맺기
        with engine.connect() as connection:
            # 3. 간단한 SQL 날려보기 (1이라는 숫자 가져오기)
            result = connection.execute(text("SELECT 1"))
            print("\n" + "="*40)
            print("✅ [성공] 데이터베이스 연결에 성공했습니다!")
            print(f"   - DB 이름: optimeal")
            print(f"   - 테스트 결과: {result.scalar()} (정상)")
            print("="*40 + "\n")
            
    except OperationalError as e:
        print("\n" + "="*40)
        print("❌ [실패] 데이터베이스에 연결할 수 없습니다.")
        print("   - Docker가 켜져 있는지 확인하세요.")
        print("   - 아이디/비번이 docker-compose.yml과 같은지 확인하세요.")
        print(f"   - 에러 내용: {e}")
        print("="*40 + "\n")

if __name__ == "__main__":
    check_connection()