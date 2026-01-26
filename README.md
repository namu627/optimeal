# OptiMeal Project

## Mac/Windows 공통 실행 가이드
1. Clone Repository
    2. `python3 -m venv venv` 
    (Windows: `python -m venv venv`)
    3. `source venv/bin/activate` 
    (Windows: `venv\Scripts\activate`)
    4. `pip install -r requirements.txt`
    5. `docker compose up -d` 
    (Docker Desktop 실행 필수)
    6. `uvicorn main:app --reload`/