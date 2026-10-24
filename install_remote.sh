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
except Exception:
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
unmounted_on_client = []

for dev, remote_mp in remote_catalog.items():
    if dev in local_mounts:
        local_mp = local_mounts[dev]
        matched += 1
        path_map[remote_mp] = local_mp
        if remote_mp != local_mp:
            print(f'  [Auto-Mapped] {remote_mp} -> {local_mp} (Volume: {dev})')
        else:
            print(f'  [Identical]   {remote_mp} == {local_mp} (Volume: {dev})')
    else:
        unmounted_on_client.append(remote_mp)

print(f'\nTotal {matched} NFS volumes matched with 77 server.')
if unmounted_on_client:
    print(f'Note: {len(unmounted_on_client)} NFS volumes on 77 are NOT mounted on this client machine.')

# Save mapping rules
with open('${MAP_FILE}', 'w') as f:
    json.dump({
        'mapping': path_map,
        'unmounted_roots': unmounted_on_client
    }, f, indent=2, ensure_ascii=False)
print(f'Configuration saved to: ${MAP_FILE}')
"

echo ""
echo "=== 2. Creating biofile-cli client wrapper ==="
WRAPPER_PATH="$BIN_DIR/biofile-cli"

cat << 'WRAPPER_EOF' > "$WRAPPER_PATH"
#!/usr/bin/env python3
"""
biofile-cli client wrapper with automated path translation and unmounted notice
"""
import sys, os, subprocess, json

SERVER_HOST = os.environ.get("BIOFILE_SERVER_HOST", "192.168.5.77")
SERVER_USER = os.environ.get("BIOFILE_SERVER_USER", os.environ.get("USER", "cgh2"))
MAP_FILE = os.path.expanduser("~/.biofile_map.json")

path_map = {}
unmounted_roots = []

if os.path.exists(MAP_FILE):
    try:
        with open(MAP_FILE, "r") as f:
            cfg = json.load(f)
            if "mapping" in cfg:
                path_map = cfg.get("mapping", {})
                unmounted_roots = cfg.get("unmounted_roots", [])
            else:
                path_map = cfg
    except Exception:
        pass

# Environment variable override: BIOFILE_PATH_MAP="/rem1=/loc1,/rem2=/loc2"
env_map = os.environ.get("BIOFILE_PATH_MAP", "")
if env_map:
    for item in env_map.split(","):
        if "=" in item:
            r, l = item.strip().split("=", 1)
            path_map[r.strip()] = l.strip()

# Check incoming CLI arguments: reverse-translate client path to 77 path if user passed local path
filtered_argv = []
client_local_filter = False

for arg in sys.argv[1:]:
    matched_reverse = False
    for remote_prefix, local_prefix in path_map.items():
        if arg == local_prefix or arg.startswith(local_prefix + "/"):
            # Translate client local path back to 77 server path
            translated_arg = remote_prefix + arg[len(local_prefix):]
            filtered_argv.append(translated_arg)
            matched_reverse = True
            break
    if not matched_reverse:
        filtered_argv.append(arg)

# Check if user wants local-only results (--local-only)
is_local_only = "--local-only" in filtered_argv
if is_local_only:
    filtered_argv.remove("--local-only")

def translate_path(p: str):
    # Check if p is on a share not mounted on this client
    for unmounted in unmounted_roots:
        if p == unmounted or p.startswith(unmounted + "/"):
            return p, False  # path exists on 77, but unmounted on client
    for remote_prefix, local_prefix in path_map.items():
        if p == remote_prefix or p.startswith(remote_prefix + "/"):
            return local_prefix + p[len(remote_prefix):], True
    return p, True

# Forward call to 77 server
ssh_cmd = ["ssh", "-T", f"{SERVER_USER}@{SERVER_HOST}", "biofile-cli"] + filtered_argv

proc = subprocess.Popen(ssh_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
stdout, stderr = proc.communicate()

if stderr:
    sys.stderr.write(stderr)

# Check if JSON output
try:
    data = json.loads(stdout)
    if isinstance(data, dict):
        if "results" in data:
            new_results = []
            for item in data["results"]:
                if "path" in item:
                    trans_p, is_local = translate_path(item["path"])
                    if is_local_only and not is_local:
                        continue
                    item["path"] = trans_p
                    item["mounted_on_client"] = is_local
                new_results.append(item)
            data["results"] = new_results
            data["count"] = len(new_results)
        if "folder" in data:
            data["folder"], _ = translate_path(data["folder"])
        sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
        sys.exit(proc.returncode)
except Exception:
    pass

# Plain text lines translation
for line in stdout.splitlines(keepends=True):
    modified = line
    is_unmounted = False
    for unmounted in unmounted_roots:
        if unmounted in modified:
            is_unmounted = True
            break
    
    if is_local_only and is_unmounted:
        continue

    for remote_prefix, local_prefix in path_map.items():
        if remote_prefix in modified:
            modified = modified.replace(remote_prefix, local_prefix)
    
    # Tag unmounted notice in human-readable table
    if is_unmounted and "[" in modified and "]" in modified:
        modified = modified.rstrip() + "  [⚠️ NOT MOUNTED ON THIS HOST]\n"

    sys.stdout.write(modified)

sys.exit(proc.returncode)
WRAPPER_EOF

chmod +x "$WRAPPER_PATH"

echo "=== Installation Complete ==="
echo "Client wrapper : $WRAPPER_PATH"
echo "Mapping file   : $MAP_FILE"
echo "============================="
