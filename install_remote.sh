#!/usr/bin/env bash
#
# biofile-cli remote client installer
# Usage on client machine:
#   curl -sSL http://192.168.5.77/.../install_remote.sh | bash
#   or run directly after cloning the repo on client machine.
#

SERVER_HOST="${BIOFILE_SERVER_HOST:-192.168.5.77}"
SERVER_USER="${BIOFILE_SERVER_USER:-$USER}"
BIN_DIR="$HOME/.local/bin"

mkdir -p "$BIN_DIR"

WRAPPER_PATH="$BIN_DIR/biofile-cli"

cat << WRAPPER_EOF > "$WRAPPER_PATH"
#!/usr/bin/env bash
# biofile-cli remote proxy to index server ($SERVER_HOST)
exec ssh -T -o BatchMode=yes -o ConnectTimeout=5 "${SERVER_USER}@${SERVER_HOST}" "biofile-cli \$*"
WRAPPER_EOF

chmod +x "$WRAPPER_PATH"

echo "=== biofile-cli client installed successfully ==="
echo "Target script : $WRAPPER_PATH"
echo "Remote server : ${SERVER_USER}@${SERVER_HOST}"
echo ""
echo "Ensure $BIN_DIR is in your PATH:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
echo ""
echo "Test connection:"
echo "  biofile-cli status"
echo "================================================"
