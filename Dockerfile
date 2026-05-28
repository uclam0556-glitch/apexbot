FROM python:3.11-slim

WORKDIR /app

# Install system dependencies required for building some python packages (like asyncpg)
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy and install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Set environment variable to ensure logs are printed directly without buffering
ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
