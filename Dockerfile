FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 CR_NO_SANDBOX=1
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt && playwright install --with-deps chromium
COPY . .
RUN useradd -m app && chown -R app /app
USER app
EXPOSE 8501 8502
CMD ["streamlit", "run", "app.py", "--server.address=0.0.0.0", "--server.port=8501"]
