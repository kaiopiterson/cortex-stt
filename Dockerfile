FROM nvidia/cuda:12.3.2-cudnn9-runtime-ubuntu22.04
RUN apt-get update && apt-get install -y python3 python3-pip && rm -rf /var/lib/apt/lists/*
WORKDIR /srv
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt
COPY app app
ENV HF_HOME=/models
CMD ["python3","-m","uvicorn","app.main:app","--host","0.0.0.0","--port","8000","--workers","1"]
