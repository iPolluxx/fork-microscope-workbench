# API/controller only; build with repository root using this file explicitly.
FROM python:3.13.12-slim-bookworm
WORKDIR /app
COPY requirements/hosted.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY src/fork_microscope/hosted /app/fork_microscope/hosted
RUN touch /app/fork_microscope/__init__.py && useradd --uid 10001 --create-home app
USER 10001
ENV PORT=8080
# Controller uses the same image with create_controller_app as its command override.
CMD ["uvicorn", "fork_microscope.hosted.bootstrap:create_public_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--no-access-log"]
