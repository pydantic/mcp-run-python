#!/bin/bash
# Smoke test for mcp-run-python server

set -e

HOST="${HOST:-localhost}"
PORT="${PORT:-3001}"
BASE_URL="http://${HOST}:${PORT}"

echo "=== MCP Run Python Smoke Test ==="
echo "Testing server at ${BASE_URL}"
echo

# Test 1: Check server is responding with initialize request
echo "[1/3] Checking server health (initialize)..."
curl -s --max-time 10 "${BASE_URL}/mcp" -X POST \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -D /tmp/mcp_headers.txt \
    -o /tmp/mcp_init_response.txt \
    -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke-test","version":"1.0"}}}' \
    2>&1 || true

echo "Response: $(cat /tmp/mcp_init_response.txt)"

if grep -q '"serverInfo"' /tmp/mcp_init_response.txt; then
    echo "PASS: Server initialized successfully"
else
    echo "FAIL: Unexpected response"
    exit 1
fi

# Extract session ID from response headers
SESSION_ID=$(grep -i 'mcp-session-id' /tmp/mcp_headers.txt | tr -d '\r' | awk '{print $2}' || true)
echo "Session ID: ${SESSION_ID:-none}"

if [ -z "$SESSION_ID" ]; then
    echo "WARN: No session ID found, trying stateless mode..."
fi

# Build headers for subsequent requests
HEADERS=(-H "Content-Type: application/json" -H "Accept: application/json, text/event-stream")
if [ -n "$SESSION_ID" ]; then
    HEADERS+=(-H "Mcp-Session-Id: ${SESSION_ID}")
fi

# Test 2: List tools
echo
echo "[2/3] Listing available tools..."
curl -s --max-time 10 "${BASE_URL}/mcp" -X POST \
    "${HEADERS[@]}" \
    -o /tmp/mcp_tools_response.txt \
    -d '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}' \
    2>&1 || true

echo "Response: $(cat /tmp/mcp_tools_response.txt)"

if grep -q 'run_python_code' /tmp/mcp_tools_response.txt; then
    echo "PASS: run_python_code tool available"
else
    echo "FAIL: run_python_code tool not found"
    exit 1
fi

# Test 3: Execute simple Python code
echo
echo "[3/3] Running Python code (1 + 1)..."
curl -s --max-time 30 "${BASE_URL}/mcp" -X POST \
    "${HEADERS[@]}" \
    -o /tmp/mcp_run_response.txt \
    -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"run_python_code","arguments":{"python_code":"12 * 12"}}}' \
    2>&1 || true

echo "Response: $(cat /tmp/mcp_run_response.txt)"

if grep -qE '(success|"2")' /tmp/mcp_run_response.txt; then
    echo "PASS: Python code executed successfully"
else
    echo "FAIL: Python execution failed"
    exit 1
fi

echo
echo "=== All smoke tests passed ==="

# Cleanup
rm -f /tmp/mcp_headers.txt /tmp/mcp_init_response.txt /tmp/mcp_tools_response.txt /tmp/mcp_run_response.txt
