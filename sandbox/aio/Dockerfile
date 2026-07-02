FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /srv/agent-sandbox
COPY sandbox/aio/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY sandbox ./sandbox
RUN mkdir -p /mnt/user-data/workspace

EXPOSE 8765
CMD ["uvicorn", "sandbox.aio.agent_sandbox.app:app", "--host", "0.0.0.0", "--port", "8765"]
