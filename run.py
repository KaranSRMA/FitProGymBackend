import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        ssl_keyfile="192.168.29.209-key.pem",
        ssl_certfile="192.168.29.209.pem",
        # docs_url=None,
        # redoc_url=None,
        # openapi_url=None
    )
