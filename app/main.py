from fastapi import FastAPI

from app.db import Database

db = Database()

app = FastAPI(title="tiron")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello World"}
