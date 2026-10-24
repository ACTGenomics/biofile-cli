#!/usr/bin/env bash
#
# biofile-cli remote client installer with Automatic NFS Path Mapping
#

SERVER_HOST="${BIOFILE_SERVER_HOST:-192.168.5.77}"
SERVER_USER="${BIOFILE_SERVER_USER:-$USER}"
BIN_DIR="$HOME/.local/bin"
CONF_DIR="$HOME/.config/biofile"
MAP_FILE="$HOME/.biofile_map.json"

mkdir -p "$BIN_DIR" "$CONF_DIR"

echo "=== 1. Detecting local NFS mounts ==="
python3 -c "
import json, os, subprocess, sys

server_host = '${SERVER_HOST}'
server_user = '${SERVER_USER}'

# 1. Fetch remote NFS catalog from 77
print(f'Fetching NFS volume catalog from 77 ({server_host})...')
remote_catalog = {}
try:
    cmd = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', f'{server_user}@{server_host}', 'cat', '/mnt/home/cgh2/pi-projects/biofile-cli/nfs_mounts_77.json']
    out = subprocess.check_output(cmd).decode()
    remote_catalog = json.loads(out)
except Exception as e:
    # If file not found on 77, query /proc/self/mountinfo on 77 dynamically
    try:
        cmd = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=5', f'{server_user}@{server_host}', 'cat', '/proc/self/mountinfo']
        raw = subprocess.check_output(cmd).decode()
        for line in raw.splitlines():
            parts = line.strip().split(' - ')
            if len(parts) == 2:
                left = parts[0].split()
                right = parts[1].split()
                mp = left[4].replace('\\040', ' ')
                fs = right[0]
                dev = right[1] if len(right) > 1 else ''
                if fs.startswith('nfs') and '/.snapshot' not in mp and '/.snapshot' not in dev:
                    remote_catalog[dev] = mp
    except Exception as e2:
        print('Warning: could not fetch remote NFS catalog from 77:', e2)

# 2. Inspect local NFS mounts
local_mounts = {}
try:
    with open('/proc/self/mountinfo', 'r') as f:
        for line in f:
            parts = line.strip().split(' - ')
            if len(parts) == 2:
                left = parts[0].split()
                right = parts[1].split()
                mp = left[4].replace('\\040', ' ')
                fs = right[0]
                dev = right[1] if len(right) > 1 else ''
                if fs.startswith('nfs') and '/.snapshot' not in mp and '/.snapshot' not in dev:
                    local_mounts[dev] = mp
except Exception as e:
    print('Warning: could not read local mountinfo:', e)

# 3. Match by NFS Device/Volume (IP:ExportPath)
path_map = {}
matched = 0
for dev, remote_mp in remote_catalog.items():
    if dev in local_mounts:
        local_mp = local_mounts[dev]
        matched += 1
        if remote_mp != local_mp:
            path_map[remote_mp] = local_mp
            print(f'  [Auto-Mapped] {remote_mp} -> {local_mp} (Volume: {dev})')
        else:
            print(f'  [Identical]   {remote_mp} == {local_mp} (Volume: {dev})')

print(f'\nTotal {matched} NFS volumes matched with 77 server.')
if path_map:
    with open('${MAP_FILE}', 'w') as f:
        json.dump(path_map, f, indent=2, ensure_ascii=False)
    print(f'Generated auto-mapping configuration: ${MAP_FILE} ({len(path_map)} translations)')
else:
    print('All matched NFS mountpoints are identical! No path translation needed.')
"

echo ""
echo "=== 2. Creating biofile-cli client wrapper ==="
WRAPPER_PATH="$BIN_DIR/biofile-cli"

cat << 'WRAPPER_EOF' > "$WRAPPER_PATH"
#!/usr/bin/env python3
"""
biofile-cli client wrapper with automated path translation
"""
import sys, os, subprocess, json

SERVER_HOST = os.environ.get("BIOFILE_SERVER_HOST", "192.168.5.77")
SERVER_USER = os.environ.get("BIOFILE_SERVER_USER", os.environ.get("USER", "cgh2"))
MAP_FILE = os.path.expanduser("~/.biofile_map.json")

# Load path map rules
path_map = {}
if os.path.exists(MAP_FILE):
    try:
        with open(MAP_FILE, "r") as f:
            path_map = json.load(f)
    except Exception:
        pass

# Also parse BIOFILE_PATH_MAP environment variable: "/rem1=/loc1,/rem2=/loc2"
env_map = os.environ.get("BIOFILE_PATH_MAP", "")
if env_map:
    for item in env_map.split(","):
        if "=" in item:
            r, l = item.strip().split("=", 1)
            path_map[r.strip()] = l.strip()

def translate_path(p: str) -> str:
    for remote_prefix, local_prefix in path_map.items():
        if p == remote_prefix or p.startswith(remote_prefix + "/"):
            return local_prefix + p[len(remote_prefix):]
    return p

# Forward call to 77 server
ssh_cmd = ["ssh", "-T", f"{SERVER_USER}@{SERVER_HOST}", "biofile-cli"] + sys.argv[1:]

if not path_map:
    # No mapping needed, direct exec
    os.execvp("ssh", ssh_cmd)

# Intercept and translate output
proc = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate()

if stderr:
    sys.stderr.write(stderr)

# Check if JSON output
try:
    data = json.loads(stdout)
    if isinstance(data, dict):
        if "results" in data:
            for item in data["results"]:
                if "path" in item:
                    item["path"] = translate_path(item["path"])
        if "folder" in data:
            data["folder"] = translate_path(data["folder"])
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        sys.exit(proc.returncode)
except Exception:
    pass

# Plain text lines translation
for line in stdout.splitlines(keepends=True):
    modified = line
    for remote_prefix, local_prefix in path_map.items():
        if remote_prefix in modified:
            modified = modified.replace(remote_prefix, local_prefix)
    sys.stdout.write(modified)

sys.exit(proc.returncode)
WRAPPER_EOF

chmod +x "$WRAPPER_PATH"

echo "=== Installation & Auto-Mapping Complete ==="
echo "Client wrapper : $WRAPPER_PATH"
echo "Mapping file   : $MAP_FILE"
echo ""
echo "Test command:"
echo "  biofile-cli status"
echo "  biofile-cli search AANB01 --limit 3"
echo "============================================"
