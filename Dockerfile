FROM python:3.11-slim
WORKDIR /app
RUN pip install fastapi uvicorn pydantic -i https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com
COPY main.py version.json /app/
EXPOSE 50087
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "50087"]
