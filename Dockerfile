FROM python:3.13-slim

# Set working directory
WORKDIR /app

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose port 8080
EXPOSE 8080

# Command to run the application
CMD ["uvicorn", "Default_channel_group:app", "--host", "0.0.0.0", "--port", "8080"]
