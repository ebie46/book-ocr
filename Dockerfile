FROM python:3.11-slim

WORKDIR /app

# Install tesseract kalau OCR pake itu
RUN apt-get update && apt-get install -y tesseract-ocr && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


COPY backend_book/ ./backend_book/
COPY frontend_book/ ./frontend_book/
COPY app.py .

# Port, ganti kalau app.py lo pake port lain selain 8000
EXPOSE 8003

# Langsung jalanin app.py tanpa nohup
CMD ["python", "app.py"]
