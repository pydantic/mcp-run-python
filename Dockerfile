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

# Prepare the deno bits
WORKDIR /app/mcp_run_python/deno
# no deno task build defined, replaced
RUN deno cache src/main.ts

WORKDIR /app

# Define default executable
ENTRYPOINT ["uv", "run", "mcp-run-python"]

# Advertise default port used in default CMD
EXPOSE 3001

# By default start streamable-http on port 3001
CMD ["--port=3001", "streamable-http"]
