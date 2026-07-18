FROM python:3.11-slim

# System deps needed to build some Python packages (cryptography, web3, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libssl-dev \
    libffi-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cached unless requirements change)
# Path is relative to repo root — Railway build context is always repo root
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the bot source
COPY . .

# Create writable runtime directories
RUN mkdir -p logs

CMD ["python", "main.py"]
