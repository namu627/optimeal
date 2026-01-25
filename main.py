from fastapi import FastAPI

app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, OptiMeal! Mac 환경에서 실행된 서버입니다."}
