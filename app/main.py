from fastapi import FastAPI

app = FastAPI(title="tiron")


@app.get("/")
def read_root() -> dict[str, str]:
    return {"message": "Hello World"}
