FROM python:3.11-slim

WORKDIR /app

# WHY install dependencies before copying src: Docker layer caching means
# a code change (frequent) doesn't invalidate the pip install layer (slow).
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./src/

EXPOSE 8000

CMD ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "8000"]
