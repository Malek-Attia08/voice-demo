# The demo's voice worker (Gemini Live + tools + the cloned voice). Built from the app folder:
#   docker build -f worker/Dockerfile .
# Its own image on purpose: LiveKit Agents pins its own versions of openai and friends.
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY worker/requirements.txt worker/requirements.txt
RUN pip install -r worker/requirements.txt

# Only what the worker reads: itself and its tools. It holds no Mistral key and no master key.
COPY worker worker

CMD ["python", "-m", "worker.agent", "start"]
