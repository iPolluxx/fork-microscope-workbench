FROM python:3.13-slim-bookworm
ENV DEBIAN_FRONTEND=noninteractive \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH=/opt/fork-microscope/.venv/bin:$PATH \
    HF_HOME=/workspace/huggingface \
    OTRECON_FORCE_RUPTURES=1 \
    PYTHONUNBUFFERED=1
RUN apt-get update && apt-get install -y --no-install-recommends git openssh-server ca-certificates tini curl \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir uv==0.11.2
WORKDIR /opt/fork-microscope
COPY requirements/ requirements/
RUN uv venv --python /usr/local/bin/python .venv \
    && uv pip sync --python .venv/bin/python --index https://download.pytorch.org/whl/cu128 \
       --index-strategy unsafe-best-match requirements/cuda.lock \
    && uv pip install --python .venv/bin/python --no-deps -r requirements/lens.txt \
    && uv cache clean
# Fetch public upstream at the tested pin; no host Git config or credentials enter layers.
COPY src/fork_microscope/upstream_snapshot.py /tmp/upstream_snapshot.py
RUN git clone --no-checkout https://github.com/ericb-goodfire/forking-fast.git vendor/forking-fast \
    && git -C vendor/forking-fast checkout --detach d32fed8d4162a4888291c4b3a38b059727c85a41 \
    && python /tmp/upstream_snapshot.py vendor/forking-fast --revision d32fed8d4162a4888291c4b3a38b059727c85a41 --write \
    && rm -rf vendor/forking-fast/.git /tmp/upstream_snapshot.py
COPY . .
RUN uv pip install --python .venv/bin/python --no-deps ./vendor/forking-fast/otrecon ./vendor/forking-fast/forking_paths \
    && uv pip install --python .venv/bin/python --no-deps --editable . \
    && uv cache clean \
    && chmod +x docker/start.sh \
    && rm -f /etc/ssh/ssh_host_* \
    && mkdir -p /run/sshd \
    && printf '\nPasswordAuthentication no\nPermitRootLogin prohibit-password\n' >> /etc/ssh/sshd_config
# Include the JavaScript runtime used by the bundled verification suite.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
EXPOSE 22
ENTRYPOINT ["/usr/bin/tini", "--", "/opt/fork-microscope/docker/start.sh"]

# Model selection is inexpensive image metadata: changing it reuses all dependency layers.
# Runtime environment overrides these defaults; no model weights or credentials are baked in.
ARG FORK_MODEL_PROFILE=""
ARG FORK_MODEL_ID=""
ARG FORK_MODEL_REVISION=""
ENV AUTO_LOAD_MODEL=0 \
    FORK_MODEL_PROFILE=${FORK_MODEL_PROFILE} \
    FORK_MODEL_ID=${FORK_MODEL_ID} \
    FORK_MODEL_REVISION=${FORK_MODEL_REVISION} \
    HF_XET_HIGH_PERFORMANCE=1
RUN python docker/autoload.py --print-config
