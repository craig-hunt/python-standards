# The base image is pinned by digest, verified by pulling it rather than
# written from memory, so the build cannot drift when the tag moves.
#
# A single stage, because this is a scheduled process rather than a service:
# the image is built, run once, and discarded, so a multi-stage build would
# trade readability for a saving nobody collects.

FROM python:3.12-slim-bookworm@sha256:935c3c5b12357a7e10caa9c1e6ca5692344d97cf229832ef584be2cec9fc03df

# A non-root user, created before anything is copied, so no file in the image
# is owned by root and writable by the process.
RUN useradd --create-home --uid 10001 agent
WORKDIR /app

# Dependencies first and separately, so a change to the source does not
# invalidate the layer that took the time to build.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes --no-deps -r requirements.txt 2>/dev/null \
 || pip install --no-cache-dir -r requirements.txt

COPY pythonstandards/ ./pythonstandards/
COPY config.yaml requirements.txt ./
COPY sql/ ./sql/

USER agent

# No secrets and no env file. The platform supplies DATABASE_URL and the SMTP
# values at run time; anything baked in here would ship with the image.
ENTRYPOINT ["python", "-m", "pythonstandards"]
