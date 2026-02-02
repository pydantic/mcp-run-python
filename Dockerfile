FROM python:3.13-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.8.8 /uv /uvx /bin/
COPY --from=docker.io/denoland/deno:bin-2.5.6 /deno /bin

# Copy the project into the image
ADD . /app

# Sync the project into a new environment, using the frozen lockfile
WORKDIR /app

# Prepare the python bits
#   'make install' also does something with precommit
RUN uv sync --frozen --compile-bytecode
# or rather 'make build'?
RUN uv run build/build.py

# Prepare the deno bits - install all dependencies including npm packages
WORKDIR /app/mcp_run_python/deno
# Use deno install to download all dependencies into node_modules
RUN deno install --allow-scripts --entrypoint src/main.ts
# Also cache to ensure all deps are resolved
RUN deno cache src/main.ts

# Pre-cache pyodide packages (micropip, pydantic) by running noop mode
# This downloads pyodide Python packages during build so they're available offline
# Skip on ARM64 due to Pyodide enum initialization bug
RUN if [ "$(uname -m)" != "aarch64" ]; then \
      deno run --allow-net --allow-read --allow-write=./node_modules --node-modules-dir=auto src/main.ts noop; \
    fi

WORKDIR /app

# Define default executable with --offline to prevent network calls
ENTRYPOINT ["uv", "run", "mcp-run-python", "--offline"]

# Advertise default port used in default CMD
EXPOSE 3001

# By default start streamable-http on port 3001
CMD ["--port=3001", "streamable-http"]
