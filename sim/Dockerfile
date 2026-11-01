FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY fleet_sim ./fleet_sim
CMD ["python", "-m", "fleet_sim.main"]
