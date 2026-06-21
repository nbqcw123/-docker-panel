#!/usr/bin/env python3
"""Docker Management Panel - Backend (FastAPI)"""
import json, subprocess, asyncio, re, os, socket, ssl, urllib.request, urllib.error, shutil, tempfile
from typing import Optional
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

VERSION = "1.3.2"
GITHUB_REPO = "nbqcw123/docker-panel"
GITHUB_RAW_BASE = f"https://raw.githubusercontent.com/{GITHUB_REPO}/master"

app = FastAPI(title="Docker Panel")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

DOCKER_SOCKET = "/var/run/docker.sock"

# Custom names and descriptions storage
CUSTOM_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "container_meta.json")

def _load_custom_meta():
    """Load custom names and descriptions from local JSON file"""
    try:
        if os.path.exists(CUSTOM_DATA_FILE):
            with open(CUSTOM_DATA_FILE, "r") as f:
                return json.load(f)
    except:
        pass
    return {"names": {}, "descriptions": {}}

def _save_custom_meta(data):
    """Save custom names and descriptions to local JSON file"""
    try:
        with open(CUSTOM_DATA_FILE, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except:
        pass

def _get_remote_version():
    """Fetch latest version info from GitHub"""
    try:
        url = f"{GITHUB_RAW_BASE}/version.json"
        req = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
            return data
    except Exception as e:
        return None

def _version_tuple(v):
    """Parse version string to tuple for comparison"""
    try:
        return tuple(int(x) for x in v.strip().split("."))
    except:
        return (0,)

@app.get("/api/version")
async def get_version():
    """Get local and remote version info"""
    remote = _get_remote_version()
    local_version = VERSION
    remote_version = remote.get("version", "") if remote else ""
    has_update = False
    if remote_version:
        has_update = _version_tuple(remote_version) > _version_tuple(local_version)
    return {
        "local": local_version,
        "remote": remote_version,
        "has_update": has_update,
        "changelog": remote.get("changelog", []) if remote else [],
        "date": remote.get("date", "") if remote else "",
        "repo": GITHUB_REPO
    }

class UpdateRequest(BaseModel):
    target_version: str = ""

@app.post("/api/update")
async def perform_update(req: UpdateRequest):
    """Download latest main.py from GitHub and replace local file"""
    try:
        # Download latest main.py
        url = f"{GITHUB_RAW_BASE}/main.py"
        req_dl = urllib.request.Request(url, headers={"Cache-Control": "no-cache"})
        with urllib.request.urlopen(req_dl, timeout=30) as resp:
            new_content = resp.read()
        
        # Verify it's valid Python
        try:
            compile(new_content, "<string>", "exec")
        except SyntaxError as e:
            raise HTTPException(500, f"Downloaded file has syntax error: {e}")
        
        # Backup current file
        main_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
        backup_path = main_path + ".bak"
        shutil.copy2(main_path, backup_path)
        
        # Write new file
        with open(main_path, "wb") as f:
            f.write(new_content)
        
        # Also download version.json
        try:
            url_v = f"{GITHUB_RAW_BASE}/version.json"
            req_v = urllib.request.Request(url_v, headers={"Cache-Control": "no-cache"})
            with urllib.request.urlopen(req_v, timeout=10) as resp:
                v_content = resp.read()
            v_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "version.json")
            with open(v_path, "wb") as f:
                f.write(v_content)
        except:
            pass  # version.json is optional
        
        # Clear pycache
        pycache = os.path.join(os.path.dirname(os.path.abspath(__file__)), "__pycache__")
        if os.path.exists(pycache):
            shutil.rmtree(pycache)
        
        return {"success": True, "message": "更新成功，请重启面板服务以生效", "restarted": False}
    except HTTPException:
        raise
    except Exception as e:
        # Restore backup if exists
        try:
            if os.path.exists(backup_path):
                shutil.copy2(backup_path, main_path)
        except:
            pass
        raise HTTPException(500, f"更新失败: {str(e)}")

@app.post("/api/restart")
async def restart_service():
    """Restart the panel service"""
    try:
        # Write a restart flag file
        flag_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".restart_flag")
        with open(flag_path, "w") as f:
            f.write(str(os.getpid()))
        return {"success": True, "message": "重启信号已发送"}
    except Exception as e:
        raise HTTPException(500, f"重启失败: {str(e)}")

def _detect_docker_bin() -> str:
    for c in ["docker", "/usr/bin/docker", "/usr/local/bin/docker", "/volume1/@appstore/ContainerManager/usr/bin/docker"]:
        try:
            r = subprocess.run([c, "version", "--format", "{{.Server.Version}}"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0: return c
        except: continue
    return "docker"
DOCKER_BIN = _detect_docker_bin()

def _detect_disk_targets() -> list:
    targets = []
    try:
        result = subprocess.run(["df", "-h", "--output=target,pcent"], capture_output=True, text=True, timeout=5)
        for line in result.stdout.strip().split("\n")[1:]:
            parts = line.split()
            if len(parts) >= 2:
                mount = parts[0]
                try: int(parts[-1].replace("%", ""))
                except ValueError: continue
                if mount in ("/", "/boot", "/boot/efi"): targets.append(mount)
                elif re.match(r"^/volume\d+$", mount): targets.append(mount)
                elif mount in ("/mnt", "/srv", "/data", "/home"): targets.append(mount)
                elif re.match(r"^(/mnt|/srv|/data|/home)/[^/]+$", mount): targets.append(mount)
    except: pass
    seen, unique = set(), []
    for t in targets:
        if t not in seen: seen.add(t); unique.append(t)
    unique.sort(key=lambda x: (0 if x == "/" else 1, x))
    return unique if unique else ["/"]

def docker_api(method, path, data=None):
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(15); sock.connect(DOCKER_SOCKET)
        body = data or b""
        hdrs = f"{method} {path} HTTP/1.1\r\nHost: localhost\r\nAccept: application/json\r\n"
        if data: hdrs += "Content-Type: application/json\r\n"
        hdrs += f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
        sock.sendall(hdrs.encode() + body)
        resp = b""
        while True:
            try:
                chunk = sock.recv(8192)
                if not chunk: break
                resp += chunk
            except socket.timeout: break
        sock.close()
        he = resp.find(b"\r\n\r\n")
        if he == -1: raise Exception("Invalid HTTP response")
        hp = resp[:he].decode("utf-8", errors="replace")
        bp = resp[he+4:]
        sc = int(hp.split("\r\n")[0].split(" ")[1])
        if "Transfer-Encoding: chunked" in hp: bp = _decode_chunked(bp)
        if sc >= 400:
            try: return {"error": json.loads(bp).get("message", f"HTTP {sc}"), "status_code": sc}
            except: return {"error": f"HTTP {sc}"}
        if not bp: return {}
        try: return json.loads(bp)
        except: return {"raw": bp.decode("utf-8", errors="replace")}
    except (PermissionError, FileNotFoundError, OSError): return _docker_cli_fallback(method, path, data)
    except Exception as e: return {"error": str(e)}

def _decode_chunked(data):
    r, pos = b"", 0
    while pos < len(data):
        e = data.find(b"\r\n", pos)
        if e == -1: break
        try: sz = int(data[pos:e].split(b";")[0].strip(), 16)
        except: break
        if sz == 0: break
        r += data[e+2:e+2+sz]; pos = e+2+sz+2
    return r

def _docker_cli_fallback(method, path, data=None):
    try:
        if path.startswith("/containers/json"):
            cmd = [c for c in [DOCKER_BIN, "ps", "-a" if "all=true" in path else ""] if c]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if r.returncode != 0: return {"error": r.stderr.strip()}
            cs = []
            for ln in r.stdout.strip().split("\n")[1:]:
                p = ln.split(None, 7)
                if len(p) >= 7:
                    ss = " ".join(p[4:7])
                    st = "running" if ss.startswith("Up") else "exited" if "Exited" in ss else "created" if "Created" in ss else "paused"
                    cs.append({"Id": p[0], "Names": [ln.split()[-1]], "Image": p[1], "Status": ss, "State": st, "Ports": _parse_ports(p[6] if len(p)>6 else ""), "Labels": {}})
            return cs
        cid = path.split("/")[2]
        if path.endswith("/start"): r = subprocess.run([DOCKER_BIN, "start", cid], capture_output=True, text=True, timeout=30); return {"started": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if path.endswith("/stop"): r = subprocess.run([DOCKER_BIN, "stop", cid], capture_output=True, text=True, timeout=30); return {"stopped": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if path.endswith("/restart"): r = subprocess.run([DOCKER_BIN, "restart", cid], capture_output=True, text=True, timeout=30); return {"restarted": True} if r.returncode == 0 else {"error": r.stderr.strip()}
        if "/stats" in path:
            r = subprocess.run([DOCKER_BIN, "stats", cid, "--no-stream", "--format", "{{json .}}"], capture_output=True, text=True, timeout=15)
            if r.returncode != 0: return {"error": r.stderr.strip()}
            try:
                s = json.loads(r.stdout.strip())
                return {"cpu_stats":{"cpu_usage":{"total_usage":0},"system_cpu_usage":0,"online_cpus":1},"precpu_stats":{},"memory_stats":{"usage":int(s.get("MemUsage","0").split("/")[0].strip().replace("MiB","000").replace("GiB","000000")),"limit":int(s.get("MemUsage","0").split("/")[1].strip().replace("MiB","000").replace("GiB","000000"))},"networks":{}}
            except: return {"memory_stats":{"usage":0,"limit":1}}
        return {"error": f"Unsupported: {path}"}
    except Exception as e: return {"error": str(e)}

def _parse_ports(ps):
    if not ps: return []
    ports = []
    for part in ps.split(","):
        part = part.strip()
        if "->" in part:
            h,c = part.split("->",1)
            hp = h.split(":")[-1].strip() if ":" in h else ""
            cp = c.split("/")[0].strip() if "/" in c else c.strip()
            proto = c.split("/")[1].strip() if "/" in c else "tcp"
            if hp: ports.append({"PublicPort":int(hp) if hp.isdigit() else 0,"PrivatePort":int(cp) if cp.isdigit() else 0,"Type":proto,"IP":"0.0.0.0"})
    return ports

def get_disk_usage():
    try:
        tgts = _detect_disk_targets()
        r = subprocess.run(["df","-h"]+tgts, capture_output=True, text=True, timeout=5)
        ds = {}
        for ln in r.stdout.strip().split("\n")[1:]:
            p = ln.split()
            if len(p) >= 6: ds[p[5]] = {"size":p[1],"used":p[2],"available":p[3],"use_percent":p[4].replace("%","")}
        return ds
    except: return {}

def get_system_memory():
    try:
        r = subprocess.run(["cat","/proc/meminfo"], capture_output=True, text=True, timeout=5)
        mi = {}
        for ln in r.stdout.strip().split("\n"):
            m = re.match(r"(\w+):\s+(\d+)\s+kB", ln)
            if m: mi[m.group(1)] = int(m.group(2))
        t,a = mi.get("MemTotal",0), mi.get("MemAvailable",0)
        return {"total_mb":round(t/1024),"used_mb":round((t-a)/1024),"available_mb":round(a/1024),"use_percent":round((t-a)/t*100,1) if t>0 else 0}
    except: return {}

@app.get("/api/containers")
async def list_containers():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, dict) and "error" in raw: raise HTTPException(500, raw["error"])
    meta = _load_custom_meta()
    cs = []
    for c in raw:
        ps = [{"container_port":p.get("PrivatePort"),"host_port":p.get("PublicPort"),"host_ip":p.get("IP","0.0.0.0"),"type":p.get("Type","tcp")} for p in c.get("Ports",[])]
        cid = c.get("Id", "")
        short_id = cid[:12]
        # Get version from labels
        labels = c.get("Labels", {}) or {}
        version = labels.get("org.opencontainers.image.version", "")
        if not version:
            # Try to extract from image tag
            img = c.get("Image", "")
            if ":" in img and "@" not in img:
                tag = img.split(":")[-1]
                if tag and tag != "latest":
                    version = tag
        # Custom name
        custom_name = meta.get("names", {}).get(cid, "")
        description = meta.get("descriptions", {}).get(cid, "")
        cs.append({
            "id": short_id,
            "full_id": cid,
            "name": c.get("Names",[""])[0].lstrip("/"),
            "custom_name": custom_name,
            "description": description,
            "image": c.get("Image",""),
            "version": version,
            "status": c.get("Status",""),
            "state": c.get("State",""),
            "created": c.get("Created",0),
            "ports": ps
        })
    return {"containers": cs, "count": len(cs)}

@app.get("/api/container/{cid}/stats")
async def container_stats(cid: str):
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, lambda: docker_api("GET", f"/containers/{cid}/stats?stream=false"))
    if isinstance(r, dict) and "error" in r: raise HTTPException(500, r["error"])
    cpu = 0
    try:
        cu,pu = r.get("cpu_stats",{}).get("cpu_usage",{}), r.get("precpu_stats",{}).get("cpu_usage",{})
        cd,sd = cu.get("total_usage",0)-pu.get("total_usage",0), r.get("cpu_stats",{}).get("system_cpu_usage",0)-r.get("precpu_stats",{}).get("system_cpu_usage",0)
        if sd>0 and cd>0: cpu = round(cd/sd*r.get("cpu_stats",{}).get("online_cpus",1)*100,2)
    except: pass
    ms = r.get("memory_stats",{})
    mu,ml = ms.get("usage",0), ms.get("limit",1)
    mp = round(mu/ml*100,2) if ml>0 else 0
    ns = r.get("networks",{})
    return {"id":cid[:12],"cpu_percent":cpu,"memory_usage":mu,"memory_limit":ml,"memory_usage_mb":round(mu/1024/1024,1),"memory_limit_mb":round(ml/1024/1024,1),"memory_percent":mp,"network_rx":sum(n.get("rx_bytes",0) for n in ns.values()),"network_tx":sum(n.get("tx_bytes",0) for n in ns.values())}

@app.get("/api/container/{cid}/disk")
async def container_disk(cid: str):
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, lambda: docker_api("GET", f"/containers/{cid}/json"))
    if isinstance(r, dict) and "error" in r: raise HTTPException(500, r["error"])
    size_rw = r.get("SizeRw", 0) or 0
    size_root = r.get("SizeRootFs", 0) or 0
    return {"id": cid[:12], "size_rw": size_rw, "size_root_fs": size_root}

@app.get("/api/system")
async def system_info():
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    ports = []
    if isinstance(raw, list):
        for c in raw:
            if c.get("State") != "running": continue
            n = c.get("Names",[""])[0].lstrip("/")
            for p in c.get("Ports",[]):
                hp = p.get("PublicPort")
                if hp: ports.append({"host_port":hp,"container_port":p.get("PrivatePort"),"protocol":p.get("Type","tcp"),"host_ip":p.get("IP","0.0.0.0"),"container_name":n})
    ports.sort(key=lambda x: x["host_port"])
    seen, up = set(), []
    for p in ports:
        k = (p["host_port"],p["protocol"])
        if k not in seen: seen.add(k); up.append(p)
    return {"memory":get_system_memory(),"disk":get_disk_usage(),"ports":up,"ports_count":len(up)}


def get_cpu_info():
    """Get detailed CPU info and per-core usage"""
    info = {"model": "", "cores": 0, "freq": "", "load_avg": [], "per_core": [], "processes": []}
    try:
        # CPU model and cores
        r = subprocess.run(["cat", "/proc/cpuinfo"], capture_output=True, text=True, timeout=5)
        model_found = False
        for line in r.stdout.split("\n"):
            if "model name" in line and not model_found:
                info["model"] = line.split(":")[1].strip()
                model_found = True
        info["cores"] = r.stdout.count("processor\t:")
    except:
        pass
    try:
        # CPU freq
        r = subprocess.run(["cat", "/proc/cpuinfo"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            if "cpu MHz" in line:
                mhz = float(line.split(":")[1].strip())
                info["freq"] = f"{mhz:.0f} MHz"
                break
    except:
        pass
    try:
        # Load average
        r = subprocess.run(["cat", "/proc/loadavg"], capture_output=True, text=True, timeout=5)
        parts = r.stdout.strip().split()
        info["load_avg"] = [float(parts[0]), float(parts[1]), float(parts[2])]
    except:
        pass
    try:
        # Per-core usage via mpstat or /proc/stat
        r = subprocess.run(
            ["sh", "-c", "head -1 /proc/stat | tr -s ' ' '\n'"],
            capture_output=True, text=True, timeout=5
        )
        # Get overall CPU stats
        r2 = subprocess.run(["cat", "/proc/stat"], capture_output=True, text=True, timeout=5)
        cpu_lines = [l for l in r2.stdout.split("\n") if l.startswith("cpu")]
        for cl in cpu_lines[:9]:  # cpu + cpu0..cpu8
            parts = cl.split()
            if len(parts) >= 5:
                name = parts[0]
                user = int(parts[1])
                nice = int(parts[2])
                system = int(parts[3])
                idle = int(parts[4])
                total = user + nice + system + idle
                usage = round((user + nice + system) / total * 100, 1) if total > 0 else 0
                if name == "cpu":
                    info["total_usage"] = usage
                else:
                    info["per_core"].append({"core": name, "usage": usage})
    except:
        pass
    return info


def get_network_info():
    """Get network interfaces info and traffic"""
    info = {"interfaces": [], "default_iface": "", "hostname": "", "ip": ""}
    try:
        info["hostname"] = socket.gethostname()
    except:
        pass
    try:
        # Default interface via ip route
        r = subprocess.run(["ip", "route", "show", "default"], capture_output=True, text=True, timeout=5)
        for line in r.stdout.split("\n"):
            parts = line.split()
            if "dev" in parts:
                info["default_iface"] = parts[parts.index("dev") + 1]
                break
    except:
        pass
    try:
        # IP address of default iface
        r = subprocess.run(["hostname", "-I"], capture_output=True, text=True, timeout=5)
        if r.stdout.strip():
            info["ip"] = r.stdout.strip().split()[0]
    except:
        pass
    try:
        # Network interfaces stats
        with open("/proc/net/dev", "r") as f:
            lines = f.readlines()[2:]  # skip header
        for line in lines:
            parts = line.split()
            if len(parts) < 10:
                continue
            iface = parts[0].rstrip(":")
            if iface == "lo":
                continue
            rx_bytes = int(parts[1])
            tx_bytes = int(parts[9])
            info["interfaces"].append({
                "name": iface,
                "rx_bytes": rx_bytes,
                "tx_bytes": tx_bytes,
                "rx_human": _format_bytes(rx_bytes),
                "tx_human": _format_bytes(tx_bytes),
                "is_default": iface == info.get("default_iface", ""),
            })
    except:
        pass
    return info


def _format_bytes(b):
    if b < 1024: return f"{b} B"
    if b < 1048576: return f"{b/1024:.1f} KB"
    if b < 1073741824: return f"{b/1048576:.1f} MB"
    return f"{b/1073741824:.1f} GB"


@app.get("/api/system/cpu-info")
async def cpu_info_api():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_cpu_info)


@app.get("/api/system/network-info")
async def network_info_api():
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, get_network_info)

class ActionRequest(BaseModel):
    action: str

class CustomNameRequest(BaseModel):
    name: str

class DescriptionRequest(BaseModel):
    description: str

async def _find_and_update(cid: str, field: str, value: str):
    """Find container by short ID and update custom name or description"""
    meta = _load_custom_meta()
    loop = asyncio.get_event_loop()
    raw = await loop.run_in_executor(None, lambda: docker_api("GET", "/containers/json?all=true"))
    if isinstance(raw, list):
        for c in raw:
            if c.get("Id", "").startswith(cid):
                meta[field][c["Id"]] = value
                _save_custom_meta(meta)
                return {"success": True, "cid": cid, field: value}
    raise HTTPException(404, "Container not found")

@app.post("/api/container/{cid}/custom-name")
async def set_custom_name(cid: str, req: CustomNameRequest):
    return await _find_and_update(cid, "names", req.name)

@app.post("/api/container/{cid}/description")
async def set_description(cid: str, req: DescriptionRequest):
    return await _find_and_update(cid, "descriptions", req.description)

@app.post("/api/container/{cid}/action")
async def container_action(cid: str, req: ActionRequest):
    act = req.action.lower()
    if act not in ("start","stop","restart"): raise HTTPException(400, f"Invalid: {act}")
    loop = asyncio.get_event_loop()
    r = await loop.run_in_executor(None, lambda: docker_api("POST", f"/containers/{cid}/{act}"))
    if isinstance(r, dict) and "error" in r:
        if r.get("status_code") == 304: return {"success":True,"action":act,"cid":cid[:12],"note":"already in state"}
        raise HTTPException(500, r["error"])
    return {"success":True,"action":act,"cid":cid[:12]}

@app.get("/api/containers/all-stats")
async def all_containers_stats():
    cr = await list_containers()
    cs = cr["containers"]
    running = [c for c in cs if c["state"]=="running"]
    async def fs(c):
        try: c["stats"] = await container_stats(c["id"])
        except: c["stats"] = None
        return c
    await asyncio.gather(*[fs(c) for c in running])
    for c in cs:
        if c["state"]!="running": c["stats"] = None
    l2 = asyncio.get_event_loop()
    return {"containers":cs,"system":{"memory":await l2.run_in_executor(None,get_system_memory),"disk":await l2.run_in_executor(None,get_disk_usage)}}

@app.get("/", response_class=HTMLResponse)
async def frontend():
    return HTMLResponse(content=FRONTEND_HTML)

FRONTEND_HTML = r"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Docker 管理面板</title>
<style>
/* ===== THEME VARIABLES ===== */
:root{--bg:#0f1117;--bg2:#161b22;--card:#1c2333;--card-hover:#242d3d;--border:#30363d;--text:#e6edf3;--text-dim:#8b949e;--text-bright:#f0f6fc;--accent:#58a6ff;--accent-dim:#1f6feb;--green:#3fb950;--green-dim:#238636;--red:#f85149;--red-dim:#da3633;--yellow:#d29922;--yellow-dim:#9e6a03;--shadow:rgba(0,0,0,0.4);--radius:10px;--radius-sm:8px;--radius-xs:6px;--transition:0.2s ease;}
[data-theme="light"]{--bg:#f6f8fa;--bg2:#fff;--card:#fff;--card-hover:#f3f4f6;--border:#d0d7de;--text:#1f2328;--text-dim:#656d76;--text-bright:#1f2328;--accent:#0969da;--accent-dim:#0550ae;--green:#1a7f37;--green-dim:#116329;--red:#cf222e;--red-dim:#a40e26;--yellow:#9a6700;--yellow-dim:#7d4e00;--shadow:rgba(0,0,0,0.08);}
[data-theme="ocean"]{--bg:#0a1628;--bg2:#0d1f3c;--card:#112645;--card-hover:#163056;--border:#1c3a5f;--text:#c3d4e6;--text-dim:#6b8cae;--text-bright:#e8f0fe;--accent:#38bdf8;--accent-dim:#0284c7;--green:#34d399;--green-dim:#059669;--red:#fb7185;--red-dim:#e11d48;--yellow:#fbbf24;--yellow-dim:#d97706;--shadow:rgba(0,0,0,0.5);}
[data-theme="purple"]{--bg:#13081f;--bg2:#1a0e2e;--card:#221440;--card-hover:#2a1a4d;--border:#3d2666;--text:#d8c8f0;--text-dim:#8b7aab;--text-bright:#f0e8ff;--accent:#c084fc;--accent-dim:#9333ea;--green:#4ade80;--green-dim:#16a34a;--red:#f87171;--red-dim:#dc2626;--yellow:#facc15;--yellow-dim:#ca8a04;--shadow:rgba(0,0,0,0.5);}

*{margin:0;padding:0;box-sizing:border-box;}
body{background:var(--bg);color:var(--text);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Noto Sans SC',sans-serif;min-height:100vh;transition:background var(--transition),color var(--transition);}

/* ===== HEADER ===== */
.header {display:flex;align-items:center;gap:12px;padding:12px 20px;margin-bottom:14px;background:var(--card);border-radius:var(--radius);border:1px solid var(--border);box-shadow:0 2px 8px var(--shadow);position:sticky;top:0;z-index:100;backdrop-filter:blur(12px);}
.header-left {display:flex;align-items:center;gap:8px;flex-shrink:0;}
.header h1 {font-size:17px;font-weight:700;display:flex;align-items:center;gap:6px;white-space:nowrap;margin:0;}
.header h1 .icon {font-size:20px;}
.hdr-ver {
  font-size:11px;font-weight:600;color:var(--text-dim);
  background:var(--bg2);padding:2px 8px;border-radius:10px;
  border:1px solid var(--border-light);letter-spacing:0.3px;
}

/* Status pills in header */
.hdr-stats {display:flex;gap:10px;flex:1;min-width:0;justify-content:center;align-items:center;}
.hdr-pill {
  display:flex;align-items:center;gap:6px;
  padding:6px 14px;border-radius:20px;border:1px solid var(--border);
  background:var(--bg2);font-size:12px;font-weight:600;white-space:nowrap;
  transition:all var(--transition);cursor:pointer;
}
.hdr-pill:hover {border-color:var(--accent);background:var(--card-hover);}
.hdr-pill .icon {font-size:14px;}
.hdr-pill .val {color:var(--text-bright);}
.hdr-pill .sub {color:var(--text-dim);font-size:10px;font-weight:400;}
.hdr-pill .bar {width:40px;height:4px;background:var(--border);border-radius:2px;overflow:hidden;margin-left:4px;}
.hdr-pill .bar-fill {height:100%;border-radius:2px;transition:width 0.6s ease;}
.hdr-pill.green .val {color:var(--green);}
.hdr-pill.yellow .val {color:var(--yellow);}
.hdr-pill.red .val {color:var(--red);}

.header-right {display:flex;align-items:center;gap:10px;flex-shrink:0;}
.theme-switcher {display:flex;gap:3px;background:var(--bg2);border-radius:var(--radius-sm);padding:3px;border:1px solid var(--border);}
.theme-btn {width:26px;height:26px;border-radius:5px;border:2px solid transparent;cursor:pointer;font-size:13px;display:flex;align-items:center;justify-content:center;transition:all var(--transition);background:transparent;color:var(--text);}
.theme-btn:hover {background:var(--card-hover);}
.theme-btn.active {border-color:var(--accent);background:var(--card-hover);}
.refresh-btn {background:var(--accent-dim);color:#fff;border:none;padding:7px 16px;border-radius:var(--radius-sm);cursor:pointer;font-size:13px;font-weight:600;transition:all var(--transition);display:flex;align-items:center;gap:5px;white-space:nowrap;}
.refresh-btn:hover {background:var(--accent);}
.refresh-btn.loading {opacity:0.6;pointer-events:none;}
.refresh-btn .spin {display:inline-block;animation:spin 1s linear infinite;}

/* ===== SEARCH & FILTER ===== */
.toolbar {display:flex;gap:10px;margin-bottom:16px;align-items:center;flex-wrap:wrap;}
.search-box {
  flex:1;min-width:200px;position:relative;
}
.search-box input {
  width:100%;padding:9px 14px 9px 36px;
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);
  color:var(--text);font-size:13px;transition:all var(--transition);
  outline:none;
}
.search-box input:focus {border-color:var(--accent);box-shadow:0 0 0 3px rgba(88,166,255,0.15);}
.search-box input::placeholder {color:var(--text-dim);}
.search-box .search-icon {position:absolute;left:11px;top:50%;transform:translateY(-50%);font-size:14px;color:var(--text-dim);}
.search-box .clear-btn {position:absolute;right:8px;top:50%;transform:translateY(-50%);cursor:pointer;font-size:16px;color:var(--text-dim);display:none;}
.search-box .clear-btn.show {display:block;}
.search-box .clear-btn:hover {color:var(--text);}

.category-bar {display:flex;gap:6px;flex-wrap:wrap;}
.cat-tab {
  background:var(--card);border:1px solid var(--border);color:var(--text-dim);
  padding:7px 16px;border-radius:20px;cursor:pointer;font-size:12px;font-weight:500;
  transition:all var(--transition);display:flex;align-items:center;gap:5px;
}
.cat-tab:hover {border-color:var(--accent);color:var(--text);}
.cat-tab.active {background:var(--accent-dim);color:#fff;border-color:var(--accent-dim);}
.cat-tab .count {background:rgba(255,255,255,0.15);padding:1px 6px;border-radius:8px;font-size:10px;font-weight:600;}
.cat-tab.active .count {background:rgba(255,255,255,0.25);}

/* ===== LAYOUT ===== */
.main-layout {display:grid;grid-template-columns:260px 1fr;gap:16px;align-items:start;}
.ports-sidebar {position:sticky;top:70px;max-height:calc(100vh - 90px);overflow-y:auto;}
.content-area {min-width:0;}

/* ===== PORTS ===== */
.ports-bar {padding:14px 16px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius);box-shadow:0 2px 8px var(--shadow);}
.ports-bar-header {display:flex;align-items:center;justify-content:space-between;margin-bottom:10px;}
.ports-bar-title {font-size:13px;font-weight:700;display:flex;align-items:center;gap:6px;}
.ports-bar-count {font-size:11px;color:var(--text-dim);background:var(--bg2);padding:2px 8px;border-radius:10px;border:1px solid var(--border);}
.ports-list {display:flex;flex-direction:column;gap:5px;}
.port-item {
  display:flex;align-items:center;gap:6px;padding:7px 10px;
  background:var(--bg2);border:1px solid var(--border-light);border-radius:var(--radius-sm);
  font-size:12px;font-weight:600;font-family:'SF Mono','Cascadia Code','Consolas',monospace;
  color:var(--text-bright);transition:all var(--transition);
}
.port-item:hover {border-color:var(--accent);background:var(--card-hover);transform:translateX(2px);}
.port-item .port-num {color:var(--accent);font-size:14px;font-weight:700;min-width:42px;text-align:right;}
.port-item .port-arrow {color:var(--text-dim);font-size:11px;}
.port-item .port-container {color:var(--text-dim);font-size:10px;font-weight:400;margin-left:auto;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.port-item .port-proto {color:var(--yellow);font-size:9px;font-weight:500;}
.ports-empty {color:var(--text-dim);font-size:12px;padding:6px 0;}

/* ===== CONTAINER ROWS ===== */
.container-rows {display:flex;flex-direction:column;gap:5px;}
.container-row {
  display:flex;align-items:center;gap:10px;padding:9px 14px;
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);
  transition:all var(--transition);
}
.container-row:hover {border-color:var(--accent);background:var(--card-hover);transform:translateX(2px);box-shadow:0 2px 8px var(--shadow);}
.container-row .row-status {width:7px;height:7px;border-radius:50%;flex-shrink:0;}
.container-row .row-status.running {background:var(--green);box-shadow:0 0 6px var(--green);animation:pulse 2s infinite;}
.container-row .row-status.exited {background:var(--red);}
.container-row .row-status.paused {background:var(--yellow);}
.container-row .row-status.created {background:var(--accent);}
.container-row .row-name {font-size:13px;font-weight:600;min-width:120px;max-width:180px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-bright);}
.container-row .row-image {font-size:10px;color:var(--text-dim);font-family:'SF Mono','Cascadia Code',monospace;max-width:140px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.container-row .row-version {font-size:10px;font-family:'SF Mono','Cascadia Code',monospace;padding:2px 6px;background:rgba(192,132,252,0.1);color:var(--accent);border-radius:3px;border:1px solid rgba(192,132,252,0.2);white-space:nowrap;flex-shrink:0;}
.container-row .row-desc {font-size:11px;color:var(--text-dim);max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;}
.container-row .row-ports {display:flex;gap:3px;flex-wrap:wrap;flex:1;min-width:0;}
.container-row .row-port {font-size:10px;font-family:'SF Mono','Cascadia Code',monospace;padding:2px 5px;background:rgba(88,166,255,0.08);color:var(--accent);border-radius:3px;border:1px solid rgba(88,166,255,0.15);}
.container-row .row-stats {display:flex;gap:10px;font-size:10px;color:var(--text-dim);min-width:110px;justify-content:flex-end;}
.container-row .row-actions {display:flex;gap:5px;flex-shrink:0;}
.container-row .row-actions button {padding:3px 8px;font-size:10px;font-weight:600;border-radius:3px;cursor:pointer;background:transparent;border:1px solid var(--border);color:var(--text);transition:all var(--transition);}
.container-row .row-actions button:disabled {opacity:0.3;pointer-events:none;}
.container-row .row-actions .start:not(:disabled) {border-color:var(--green-dim);color:var(--green);}
.container-row .row-actions .start:hover:not(:disabled) {background:rgba(63,185,80,0.1);}
.container-row .row-actions .stop:not(:disabled) {border-color:var(--red-dim);color:var(--red);}
.container-row .row-actions .stop:hover:not(:disabled) {background:rgba(248,81,73,0.1);}
.container-row .row-actions .restart:not(:disabled) {border-color:var(--yellow-dim);color:var(--yellow);}
.container-row .row-actions .restart:hover:not(:disabled) {background:rgba(210,153,34,0.1);}

/* ===== SECTIONS ===== */
.section-header {display:flex;align-items:center;gap:8px;margin:20px 0 12px;padding-bottom:8px;border-bottom:1px solid var(--border);}
.section-header:first-child {margin-top:0;}
.section-title {font-size:14px;font-weight:700;display:flex;align-items:center;gap:6px;}
.section-title .dot {width:7px;height:7px;border-radius:50%;display:inline-block;}
.section-title .dot.green {background:var(--green);box-shadow:0 0 6px var(--green);}
.section-title .dot.gray {background:var(--text-dim);}
.section-count {font-size:11px;color:var(--text-dim);background:var(--bg2);padding:2px 7px;border-radius:8px;border:1px solid var(--border);}

/* ===== MISC ===== */
.error-banner {background:rgba(248,81,73,0.08);border:1px solid rgba(248,81,73,0.3);border-radius:var(--radius-sm);padding:10px 14px;margin-bottom:14px;font-size:13px;color:var(--red);}
.toast {position:fixed;bottom:20px;right:20px;padding:10px 20px;background:var(--card);border:1px solid var(--border);border-radius:var(--radius-sm);box-shadow:0 4px 16px var(--shadow);font-size:13px;font-weight:500;transform:translateY(80px);opacity:0;transition:all 0.3s ease;z-index:200;}
.toast.show {transform:translateY(0);opacity:1;}
.toast.success {border-left:3px solid var(--green);}
.toast.error {border-left:3px solid var(--red);}
.no-results {text-align:center;padding:30px;color:var(--text-dim);font-size:13px;}

/* ===== RESPONSIVE ===== */
@media(max-width:1100px) {
  .hdr-pill .bar {display:none;}
}
@media(max-width:900px) {
  .header {flex-wrap:wrap;}
  .hdr-stats {order:3;width:100%;flex-wrap:wrap;}
  .main-layout {grid-template-columns:1fr;}
  .ports-sidebar {position:static;max-height:none;}
  .container-row {flex-wrap:wrap;}
  .container-row .row-stats {width:100%;justify-content:flex-start;}
}
@media(max-width:600px) {
  .header {padding:10px 14px;}
  .header h1 {font-size:15px;}
  .hdr-pill {padding:4px 10px;font-size:11px;}
  .theme-switcher {display:none;}
}

@keyframes spin{to{transform:rotate(360deg);}}
@keyframes pulse{0%,100%{opacity:1;}50%{opacity:0.5;}}

/* ===== STATS DETAIL MODAL ===== */
.stats-detail-overlay {
  display:none;position:fixed;top:0;left:0;right:0;bottom:0;
  background:rgba(0,0,0,0.5);z-index:350;backdrop-filter:blur(4px);
  justify-content:center;align-items:center;
}
.stats-detail-overlay.show { display:flex; }
.stats-detail-panel {
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  box-shadow:0 8px 32px var(--shadow);padding:24px 28px;
  width:640px;max-width:90vw;max-height:80vh;overflow-y:auto;
  animation:fadeIn 0.2s ease;
}
.stats-detail-panel .stats-detail-header {
  display:flex;align-items:center;justify-content:space-between;
  margin-bottom:16px;padding-bottom:12px;border-bottom:1px solid var(--border);
}
.stats-detail-panel .stats-detail-header h2 {
  font-size:16px;font-weight:700;color:var(--text-bright);
}
.stats-detail-panel .close-btn {
  cursor:pointer;font-size:20px;color:var(--text-dim);
  transition:color var(--transition);line-height:1;
}
.stats-detail-panel .close-btn:hover { color:var(--text); }

/* ===== DETAIL PANEL ===== */
.detail-overlay {
  display:none; position:fixed; top:0; left:0; right:0; bottom:0;
  background:rgba(0,0,0,0.5); z-index:300; backdrop-filter:blur(4px);
  justify-content:center; align-items:center;
}
.detail-overlay.show { display:flex; }
.detail-panel {
  background:var(--card); border:1px solid var(--border); border-radius:var(--radius);
  box-shadow:0 8px 32px var(--shadow); padding:24px 28px; min-width:480px; max-width:640px;
  max-height:80vh; overflow-y:auto; animation:fadeIn 0.2s ease;
}
.detail-panel .close-btn {
  float:right; cursor:pointer; font-size:20px; color:var(--text-dim);
  transition:color var(--transition); line-height:1;
}
.detail-panel .close-btn:hover { color:var(--text); }
.detail-panel .detail-header {
  display:flex; align-items:center; gap:10px; margin-bottom:16px;
}
.detail-panel .detail-header .status-dot {
  width:10px; height:10px; border-radius:50%; flex-shrink:0;
}
.detail-panel .detail-header .status-dot.running { background:var(--green); box-shadow:0 0 8px var(--green); }
.detail-panel .detail-header .status-dot.exited { background:var(--red); }
.detail-panel .detail-header .status-dot.paused { background:var(--yellow); }
.detail-panel .detail-header .status-dot.created { background:var(--accent); }
.detail-panel .detail-header h2 {
  font-size:18px; font-weight:700; color:var(--text-bright); flex:1;
  overflow:hidden; text-overflow:ellipsis; white-space:nowrap;
}
.detail-panel .detail-section {
  margin-bottom:14px; padding-bottom:14px; border-bottom:1px solid var(--border);
}
.detail-panel .detail-section:last-child { border-bottom:none; margin-bottom:0; padding-bottom:0; }
.detail-panel .detail-label {
  font-size:11px; color:var(--text-dim); font-weight:600; text-transform:uppercase;
  letter-spacing:0.5px; margin-bottom:6px;
}
.detail-panel .detail-value {
  font-size:13px; color:var(--text); line-height:1.6;
  word-break:break-all;
}
.detail-panel .detail-value.mono {
  font-family:'SF Mono','Cascadia Code','Consolas',monospace;
  font-size:12px; background:var(--bg2); padding:8px 12px; border-radius:var(--radius-xs);
  border:1px solid var(--border-light);
}
.detail-panel .detail-stats {
  display:grid; grid-template-columns:repeat(3,1fr); gap:10px;
}
.detail-panel .detail-stat {
  background:var(--bg2); border:1px solid var(--border-light); border-radius:var(--radius-sm);
  padding:10px 12px; text-align:center;
}
.detail-panel .detail-stat .stat-val { font-size:18px; font-weight:700; color:var(--text-bright); }
.detail-panel .detail-stat .stat-label { font-size:10px; color:var(--text-dim); margin-top:2px; }
.detail-panel .detail-ports {
  display:flex; flex-wrap:wrap; gap:5px;
}
.detail-panel .detail-port {
  font-size:11px; font-family:'SF Mono','Cascadia Code',monospace;
  padding:3px 8px; background:rgba(88,166,255,0.08); color:var(--accent);
  border-radius:3px; border:1px solid rgba(88,166,255,0.15);
}
.detail-panel .detail-actions {
  display:flex; gap:8px; margin-top:16px; padding-top:16px; border-top:1px solid var(--border);
}
.detail-panel .detail-actions button {
  flex:1; padding:9px 16px; border-radius:var(--radius-sm); cursor:pointer;
  font-size:13px; font-weight:600; border:1px solid var(--border);
  background:transparent; color:var(--text); transition:all var(--transition);
}
.detail-panel .detail-actions button:hover { background:var(--card-hover); }
.detail-panel .detail-actions button:disabled { opacity:0.3; pointer-events:none; }
.detail-panel .detail-actions .btn-start { border-color:var(--green-dim); color:var(--green); }
.detail-panel .detail-actions .btn-start:hover:not(:disabled) { background:rgba(63,185,80,0.1); }
.detail-panel .detail-actions .btn-stop { border-color:var(--red-dim); color:var(--red); }
.detail-panel .detail-actions .btn-stop:hover:not(:disabled) { background:rgba(248,81,73,0.1); }
.detail-panel .detail-actions .btn-restart { border-color:var(--yellow-dim); color:var(--yellow); }
.detail-panel .detail-actions .btn-restart:hover:not(:disabled) { background:rgba(210,153,34,0.1); }

/* ===== UPDATE MODAL ===== */
.hdr-update-btn {
  font-size:11px;font-weight:600;padding:3px 10px;border-radius:10px;
  border:1px solid var(--yellow-dim);background:rgba(210,153,34,0.1);
  color:var(--yellow);cursor:pointer;transition:all var(--transition);
  animation:updatePulse 2s ease-in-out infinite;
}
.hdr-update-btn:hover { background:rgba(210,153,34,0.25); }
@keyframes updatePulse {
  0%,100% { box-shadow:0 0 0 0 rgba(210,153,34,0.3); }
  50% { box-shadow:0 0 8px 2px rgba(210,153,34,0.2); }
}
.update-overlay {
  display:none;position:fixed;top:0;left:0;right:0;bottom:0;
  background:rgba(0,0,0,0.5);z-index:400;backdrop-filter:blur(4px);
  justify-content:center;align-items:center;
}
.update-overlay.show { display:flex; }
.update-panel {
  background:var(--card);border:1px solid var(--border);border-radius:var(--radius);
  box-shadow:0 8px 32px var(--shadow);padding:28px 32px;min-width:420px;max-width:520px;
  max-height:70vh;overflow-y:auto;animation:fadeIn 0.2s ease;
}
.update-panel .update-header {
  display:flex;align-items:center;gap:10px;margin-bottom:16px;
}
.update-panel .update-header h2 { font-size:18px;font-weight:700;color:var(--text-bright);flex:1; }
.update-panel .update-header .close-btn {
  cursor:pointer;font-size:20px;color:var(--text-dim);transition:color var(--transition);line-height:1;
}
.update-panel .update-header .close-btn:hover { color:var(--text); }
.update-panel .update-meta {
  font-size:12px;color:var(--text-dim);margin-bottom:12px;
  padding-bottom:12px;border-bottom:1px solid var(--border);
}
.update-panel .update-meta span { margin-right:12px; }
.update-panel .update-changelog {
  margin-bottom:16px;
}
.update-panel .update-changelog .cl-title {
  font-size:12px;font-weight:600;color:var(--text-dim);text-transform:uppercase;
  letter-spacing:0.5px;margin-bottom:8px;
}
.update-panel .update-changelog ul {
  list-style:none;padding:0;margin:0;
}
.update-panel .update-changelog ul li {
  font-size:13px;color:var(--text);padding:4px 0 4px 16px;position:relative;
}
.update-panel .update-changelog ul li::before {
  content:"•";color:var(--accent);position:absolute;left:0;font-size:16px;line-height:1.2;
}
.update-panel .update-actions {
  display:flex;gap:10px;margin-top:16px;padding-top:16px;border-top:1px solid var(--border);
}
.update-panel .update-actions button {
  flex:1;padding:10px 16px;border-radius:var(--radius-sm);cursor:pointer;
  font-size:13px;font-weight:600;border:1px solid var(--border);
  background:transparent;color:var(--text);transition:all var(--transition);
}
.update-panel .update-actions button:hover { background:var(--card-hover); }
.update-panel .update-actions button:disabled { opacity:0.5;pointer-events:none; }
.update-panel .update-actions .btn-update {
  border-color:var(--green-dim);color:var(--green);background:rgba(63,185,80,0.08);
}
.update-panel .update-actions .btn-update:hover:not(:disabled) { background:rgba(63,185,80,0.15); }
.update-panel .update-progress {
  display:none;margin-top:12px;padding:10px 14px;background:var(--bg2);
  border-radius:var(--radius-sm);font-size:12px;color:var(--text-dim);
  border:1px solid var(--border);
}
.update-panel .update-progress.show { display:block; }
.update-panel .update-progress .progress-bar {
  width:100%;height:4px;background:var(--border);border-radius:2px;
  margin-top:6px;overflow:hidden;
}
.update-panel .update-progress .progress-fill {
  height:100%;background:var(--green);border-radius:2px;transition:width 0.3s ease;
}
</style>
</head>
<body>

<div class="header">
  <div class="header-left">
    <h1><span class="icon">🐳</span> Docker Panel</h1>
    <span class="hdr-ver" id="hdrVer">v--</span>
    <button class="hdr-update-btn" id="hdrUpdateBtn" onclick="showUpdateModal()" style="display:none" title="有新版本可用">⬆ 版本更新</button>
  </div>
  <div class="hdr-stats" id="hdrStats"></div>
  <div class="header-right">
    <div class="theme-switcher">
      <button class="theme-btn active" data-theme="dark" title="暗色">🌙</button>
      <button class="theme-btn" data-theme="light" title="亮色">☀️</button>
      <button class="theme-btn" data-theme="ocean" title="海洋蓝">🌊</button>
      <button class="theme-btn" data-theme="purple" title="紫色">🔮</button>
    </div>
    <button class="refresh-btn" id="refreshBtn" onclick="loadData()"><span id="refreshIcon">⟳</span> 刷新</button>
  </div>
</div>

<div id="errorBanner"></div>

<div class="toolbar">
  <div class="category-bar">
    <div class="cat-tab active" data-cat="all" onclick="setCategory('all')">📦 全部 <span class="count" id="count-all">0</span></div>
    <div class="cat-tab" data-cat="running" onclick="setCategory('running')">🟢 使用中 <span class="count" id="count-running">0</span></div>
    <div class="cat-tab" data-cat="stopped" onclick="setCategory('stopped')">🔴 未使用 <span class="count" id="count-stopped">0</span></div>
  </div>
</div>

<div class="main-layout">
  <div class="ports-sidebar">
    <div class="ports-bar">
      <div class="ports-bar-header">
        <div class="ports-bar-title">🔌 已占用端口</div>
        <span class="ports-bar-count" id="portsCount">0</span>
      </div>
      <div class="ports-list" id="portsList"></div>
    </div>
  </div>
  <div class="content-area">
    <!-- 搜索框在容器列表上方 -->
    <div class="search-box">
      <span class="search-icon">🔍</span>
      <input type="text" id="searchInput" placeholder="搜索容器名称 / 镜像..." oninput="onSearch()">
      <span class="clear-btn" id="clearBtn" onclick="clearSearch()">✕</span>
    </div>
    <div id="sectionsContainer"></div>
  </div>
</div>

<!-- 容器详情大胶囊 -->
<div class="detail-overlay" id="detailOverlay" onclick="closeDetail(event)">
  <div class="detail-panel" id="detailPanel"></div>
</div>

<div class="toast" id="toast"></div>

<script>
const API = '';
let allContainers = [];
let currentCategory = 'all';
let searchQuery = '';

// Theme
document.querySelectorAll('.theme-btn').forEach(btn => {
  btn.addEventListener('click', () => {
    document.documentElement.setAttribute('data-theme', btn.dataset.theme);
    document.querySelectorAll('.theme-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    localStorage.setItem('docker-panel-theme', btn.dataset.theme);
  });
});
const st = localStorage.getItem('docker-panel-theme');
if (st) {
  document.documentElement.setAttribute('data-theme', st);
  document.querySelectorAll('.theme-btn').forEach(b => b.classList.toggle('active', b.dataset.theme === st));
}

// Search
function onSearch() {
  searchQuery = document.getElementById('searchInput').value.trim().toLowerCase();
  document.getElementById('clearBtn').classList.toggle('show', searchQuery.length > 0);
  renderContainers();
}
function clearSearch() {
  document.getElementById('searchInput').value = '';
  searchQuery = '';
  document.getElementById('clearBtn').classList.remove('show');
  renderContainers();
}

async function loadData() {
  const btn = document.getElementById('refreshBtn'), icon = document.getElementById('refreshIcon');
  btn.classList.add('loading'); icon.classList.add('spin');
  document.getElementById('errorBanner').innerHTML = '';
  statsDetailData = { cpu: null, mem: null, sys: null, net: null };
  try {
    const [rA, rS, rCpu, rNet] = await Promise.all([
      fetch(API+'/api/containers/all-stats'),
      fetch(API+'/api/system'),
      fetch(API+'/api/system/cpu-info'),
      fetch(API+'/api/system/network-info')
    ]);
    if (!rA.ok) { const e = await rA.json(); throw new Error(e.detail || 'API error'); }
    const data = await rA.json(), sysData = await rS.json();
    if (data.containers) allContainers = data.containers;
    const sys = {...(data.system||{}), ...(sysData||{})};
    renderPorts(sys);
    renderHdrStats(sys);
    // Update CPU pill
    if (rCpu.ok) {
      const cpu = await rCpu.json();
      const usage = cpu.total_usage || 0;
      const c = usage>85?'var(--red)':usage>65?'var(--yellow)':'var(--green)';
      const el = document.getElementById('hdrCpuVal');
      const bar = document.getElementById('hdrCpuBar');
      if (el) el.textContent = usage+'%';
      if (bar) { bar.style.width = usage+'%'; bar.style.background = c; }
    }
    // Update Network pill
    if (rNet.ok) {
      const net = await rNet.json();
      const ifaces = net.interfaces || [];
      const totalRx = ifaces.reduce((s,i)=>s+i.rx_bytes,0);
      const totalTx = ifaces.reduce((s,i)=>s+i.tx_bytes,0);
      const el = document.getElementById('hdrNetVal');
      if (el) el.textContent = formatBytes(totalRx)+'↓';
    }
    renderCategories();
    renderContainers();
  } catch(e) {
    document.getElementById('errorBanner').innerHTML = '<div class="error-banner">⚠️ 加载失败: '+e.message+'</div>';
  } finally {
    btn.classList.remove('loading'); icon.classList.remove('spin');
  }
}

function renderPorts(sys) {
  const ports = sys.ports || [];
  document.getElementById('portsCount').textContent = ports.length + ' 个端口';
  const list = document.getElementById('portsList');
  if (!ports.length) { list.innerHTML = '<div class="ports-empty">暂无运行中的容器暴露端口</div>'; return; }
  list.innerHTML = ports.map(p => {
    const n = p.container_name || '';
    return `<div class="port-item" title="${n}"><span class="port-num">${p.host_port}</span><span class="port-arrow">→</span><span>${p.container_port}</span><span class="port-proto">${p.protocol}</span>${n?`<span class="port-container">(${n})</span>`:''}</div>`;
  }).join('');
}

function renderHdrStats(sys) {
  const mem = sys.memory || {}, disk = sys.disk || {};
  const running = allContainers.filter(c=>c.state==='running').length, total = allContainers.length;
  let html = '';

  // CPU pill
  html += `<div class="hdr-pill" onclick="showCpuDetail()" title="点击查看CPU详情"><span class="icon">⚡</span><span class="val" id="hdrCpuVal">--</span><span class="sub">CPU</span><div class="bar"><div class="bar-fill" id="hdrCpuBar" style="width:0%;background:var(--green)"></div></div></div>`;

  // Memory pill
  if (mem.total_mb) {
    const pct = mem.use_percent||0, c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--green)';
    html += `<div class="hdr-pill" onclick="showMemDetail()" title="点击查看内存详情"><span class="icon">🧠</span><span class="val">${mem.use_percent}%</span><span class="sub">内存</span><div class="bar"><div class="bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
  }

  // Network pill
  html += `<div class="hdr-pill" onclick="showNetDetail()" title="点击查看网络详情"><span class="icon">🌐</span><span class="val" id="hdrNetVal">--</span><span class="sub">网络</span></div>`;

  // System/Disk pills
  for (const [mnt, info] of Object.entries(disk)) {
    const pct = parseInt(info.use_percent)||0;
    const c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--accent)';
    const lbl = mnt==='/'?'系统':mnt.replace('/volume','存储');
    html += `<div class="hdr-pill" onclick="showSysDetail()" title="点击查看系统详情"><span class="icon">💾</span><span class="val">${pct}%</span><span class="sub">${lbl}</span><div class="bar"><div class="bar-fill" style="width:${pct}%;background:${c}"></div></div></div>`;
  }

  // Container count pill
  html += `<div class="hdr-pill green"><span class="icon">🐳</span><span class="val">${running}/${total}</span><span class="sub">容器</span></div>`;

  document.getElementById('hdrStats').innerHTML = html;
}

function renderCategories() {
  const filtered = getFiltered();
  document.getElementById('count-all').textContent = filtered.length;
  document.getElementById('count-running').textContent = filtered.filter(c=>c.state==='running').length;
  document.getElementById('count-stopped').textContent = filtered.filter(c=>c.state!=='running').length;
}

function getFiltered() {
  let cs = [...allContainers];
  // category filter
  if (currentCategory === 'running') cs = cs.filter(c => c.state === 'running');
  else if (currentCategory === 'stopped') cs = cs.filter(c => c.state !== 'running');
  // search filter
  if (searchQuery) cs = cs.filter(c => (c.name||'').toLowerCase().includes(searchQuery) || (c.image||'').toLowerCase().includes(searchQuery));
  // sort alphabetically by name
  cs.sort((a,b) => (a.name||'').localeCompare(b.name||'', 'zh-CN'));
  return cs;
}

function setCategory(cat) {
  currentCategory = cat;
  document.querySelectorAll('.cat-tab').forEach(t => t.classList.toggle('active', t.dataset.cat === cat));
  renderCategories();
  renderContainers();
}

function renderContainers() {
  const el = document.getElementById('sectionsContainer');
  const filtered = getFiltered();
  let running = filtered.filter(c => c.state === 'running');
  let stopped = filtered.filter(c => c.state !== 'running');

  if (!running.length && !stopped.length) {
    const msg = searchQuery ? `未找到匹配 "${searchQuery}" 的容器` : (currentCategory==='all'?'没有容器':currentCategory==='running'?'没有运行中的容器':'没有未使用的容器');
    el.innerHTML = `<div class="no-results">📦 ${msg}</div>`;
    return;
  }

  let html = '';
  if (running.length) {
    html += `<div class="section-header"><div class="section-title"><span class="dot green"></span>使用中</div><span class="section-count">${running.length} 个容器</span></div>`;
    html += '<div class="container-rows">' + running.map((c,i)=>renderRow(c,i)).join('') + '</div>';
  }
  if (stopped.length) {
    html += `<div class="section-header"><div class="section-title"><span class="dot gray"></span>未使用</div><span class="section-count">${stopped.length} 个容器</span></div>`;
    html += '<div class="container-rows">' + stopped.map((c,i)=>renderRow(c,i)).join('') + '</div>';
  }
  el.innerHTML = html;
}

function renderRow(c, i) {
  const state = c.state||'unknown';
  const isRunning = state==='running', isStopped = state==='exited'||state==='dead', isCreated = state==='created';
  const portsHtml = (c.ports||[]).filter(p=>p.host_port).map(p=>`<span class="row-port">${p.host_port}→${p.container_port}/${p.type}</span>`).join('');
  const statsHtml = (c.stats&&isRunning) ? `<span>CPU ${c.stats.cpu_percent||0}%</span><span>MEM ${c.stats.memory_usage_mb||0}/${c.stats.memory_limit_mb||0}MB</span>` : '<span>-</span>';
  // Display name: custom name + original name in brackets
  const displayName = c.custom_name ? `<span class="row-name" title="${esc(c.name)}">${esc(c.custom_name)} <span style="color:var(--text-dim);font-size:11px;font-weight:400">(${esc(c.name)})</span></span>` : `<span class="row-name" title="${esc(c.name)}">${esc(c.name)}</span>`;
  // Version badge
  const versionHtml = c.version ? `<span class="row-version" title="版本">${esc(c.version)}</span>` : '';
  // Description
  const descHtml = c.description ? `<span class="row-desc" title="${esc(c.description)}">📝 ${esc(c.description)}</span>` : '';
  return `<div class="container-row" id="card-${c.id}" onclick="showDetail(allContainers.find(x=>x.id==='${c.id}'))">
    <span class="row-status ${state}"></span>
    ${displayName}
    ${versionHtml}
    <span class="row-image" title="${esc(c.image||'')}">${esc(c.image||'-')}</span>
    ${descHtml}
    <div class="row-ports">${portsHtml||'<span style="color:var(--text-dim);font-size:10px">无端口</span>'}</div>
    <div class="row-stats">${statsHtml}</div>
    <div class="row-actions">
      <button class="start" ${isRunning?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','start',this)">▶</button>
      <button class="stop" ${(isStopped||isCreated)?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','stop',this)">⏹</button>
      <button class="restart" ${(isStopped||isCreated)?'disabled':''} onclick="event.stopPropagation();doAction('${c.id}','restart',this)">⟳</button>
    </div>
  </div>`;
}

function esc(s) { const d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

// ===== DETAIL PANEL =====
function showDetail(c) {
  const state = c.state||'unknown';
  const isRunning = state==='running', isStopped = state==='exited'||state==='dead', isCreated = state==='created';
  const portsHtml = (c.ports||[]).filter(p=>p.host_port).map(p=>`<span class="detail-port">${p.host_port}→${p.container_port}/${p.type}</span>`).join('') || '<span style="color:var(--text-dim)">无端口映射</span>';
  const cpu = c.stats ? (c.stats.cpu_percent||0)+'%' : '-';
  const memUse = c.stats ? (c.stats.memory_usage_mb||0)+'MB' : '-';
  const memLimit = c.stats ? (c.stats.memory_limit_mb||0)+'MB' : '-';
  const memPct = c.stats ? (c.stats.memory_percent||0)+'%' : '-';
  const netRx = c.stats ? formatBytes(c.stats.network_rx||0) : '-';
  const netTx = c.stats ? formatBytes(c.stats.network_tx||0) : '-';
  const versionHtml = c.version ? `<div class="detail-value" style="margin-top:4px"><b>版本:</b> ${esc(c.version)}</div>` : '';
  const customNameVal = c.custom_name || '';
  const descVal = c.description || '';

  document.getElementById('detailPanel').innerHTML = `
    <span class="close-btn" onclick="closeDetail()">✕</span>
    <div class="detail-header">
      <span class="status-dot ${state}"></span>
      <h2>${esc(c.custom_name || c.name)}</h2>
    </div>
    <div class="detail-section">
      <div class="detail-label">基本信息</div>
      <div class="detail-value"><b>ID:</b> ${esc(c.id)} &nbsp; <b>状态:</b> ${esc(c.status||state)}</div>
      <div class="detail-value mono" style="margin-top:6px">${esc(c.image||'-')}</div>
      ${versionHtml}
      <div class="detail-value" style="margin-top:4px;font-size:12px;color:var(--text-dim)"><b>原名:</b> ${esc(c.name)}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">自定义名称</div>
      <div style="display:flex;gap:6px;align-items:center">
        <input type="text" id="customNameInput" value="${esc(customNameVal)}" placeholder="输入自定义名称..." style="flex:1;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none" onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='var(--border)'">
        <button onclick="saveCustomName('${c.id}')" style="padding:7px 14px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">保存</button>
      </div>
    </div>
    <div class="detail-section">
      <div class="detail-label">容器用途</div>
      <div style="display:flex;gap:6px;align-items:flex-start">
        <textarea id="descInput" placeholder="输入容器用途描述..." rows="3" style="flex:1;padding:7px 10px;background:var(--bg2);border:1px solid var(--border);border-radius:6px;color:var(--text);font-size:13px;outline:none;resize:vertical;font-family:inherit" onfocus="this.style.borderColor='var(--accent)'" onblur="this.style.borderColor='var(--border)'">${esc(descVal)}</textarea>
      </div>
      <button onclick="saveDescription('${c.id}')" style="margin-top:6px;padding:7px 14px;background:var(--accent-dim);color:#fff;border:none;border-radius:6px;cursor:pointer;font-size:12px;font-weight:600">保存用途</button>
    </div>
    <div class="detail-section">
      <div class="detail-label">资源使用</div>
      <div class="detail-stats">
        <div class="detail-stat"><div class="stat-val">${cpu}</div><div class="stat-label">CPU</div></div>
        <div class="detail-stat"><div class="stat-val">${memUse}</div><div class="stat-label">内存 / ${memLimit}</div></div>
        <div class="detail-stat"><div class="stat-val">${memPct}</div><div class="stat-label">内存占用</div></div>
      </div>
      <div class="detail-value" style="margin-top:8px;font-size:12px;color:var(--text-dim)">网络: ↓${netRx} ↑${netTx}</div>
    </div>
    <div class="detail-section">
      <div class="detail-label">端口映射</div>
      <div class="detail-ports">${portsHtml}</div>
    </div>
    <div class="detail-actions">
      <button class="btn-start" ${isRunning?'disabled':''} onclick="doAction('${c.id}','start',this);closeDetail()">▶ 启动</button>
      <button class="btn-stop" ${(isStopped||isCreated)?'disabled':''} onclick="doAction('${c.id}','stop',this);closeDetail()">⏹ 停止</button>
      <button class="btn-restart" ${(isStopped||isCreated)?'disabled':''} onclick="doAction('${c.id}','restart',this);closeDetail()">⟳ 重启</button>
    </div>
  `;
  document.getElementById('detailOverlay').classList.add('show');
}
function closeDetail(e) {
  if (!e || e.target===document.getElementById('detailOverlay') || e.target.classList.contains('close-btn'))
    document.getElementById('detailOverlay').classList.remove('show');
}
function formatBytes(b) {
  if (b<1024) return b+'B';
  if (b<1048576) return (b/1024).toFixed(1)+'KB';
  if (b<1073741824) return (b/1048576).toFixed(1)+'MB';
  return (b/1073741824).toFixed(1)+'GB';
}

async function doAction(id, action, btn) {
  const labels={start:'启动',stop:'停止',restart:'重启'};
  btn.classList.add('loading'); btn.disabled=true;
  try {
    const r = await fetch(API+`/api/container/${id}/action`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'操作失败'); }
    showToast(labels[action]+' 成功','success');
    setTimeout(loadData,1500);
  } catch(e) {
    showToast(labels[action]+' 失败: '+e.message,'error');
    btn.classList.remove('loading'); btn.disabled=false;
  }
}

async function saveCustomName(id) {
  const input = document.getElementById('customNameInput');
  const name = input.value.trim();
  try {
    const r = await fetch(API+`/api/container/${id}/custom-name`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'保存失败'); }
    showToast('自定义名称已保存','success');
    setTimeout(loadData,500);
  } catch(e) {
    showToast('保存失败: '+e.message,'error');
  }
}

async function saveDescription(id) {
  const input = document.getElementById('descInput');
  const description = input.value.trim();
  try {
    const r = await fetch(API+`/api/container/${id}/description`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({description})});
    if (!r.ok) { const e=await r.json(); throw new Error(e.detail||'保存失败'); }
    showToast('容器用途已保存','success');
    setTimeout(loadData,500);
  } catch(e) {
    showToast('保存失败: '+e.message,'error');
  }
}

function showToast(msg,type) {
  const t=document.getElementById('toast');
  t.textContent=msg; t.className='toast show '+type;
  setTimeout(()=>t.className='toast',3000);
}

// ===== STATS DETAIL MODAL =====
let statsDetailData = { cpu: null, mem: null, sys: null, net: null };

function showStatsDetailModal(title, html) {
  document.getElementById('statsDetailTitle').textContent = title;
  document.getElementById('statsDetailBody').innerHTML = html;
  document.getElementById('statsDetailOverlay').classList.add('show');
}
function closeStatsDetailModal() {
  document.getElementById('statsDetailOverlay').classList.remove('show');
}

async function showCpuDetail() {
  const d = statsDetailData.cpu;
  if (!d) {
    showStatsDetailModal('⚡ CPU 详情', '<div style="color:var(--text-dim);padding:20px;text-align:center">加载中...</div>');
    try {
      const r = await fetch(API+'/api/system/cpu-info');
      statsDetailData.cpu = await r.json();
      return showCpuDetail();
    } catch(e) {
      return showStatsDetailModal('⚡ CPU 详情', '<div style="color:var(--red)">加载失败: '+e.message+'</div>');
    }
  }
  let html = '';
  if (d.model) html += `<div style="font-size:13px;color:var(--text-dim);margin-bottom:12px">${esc(d.model)}</div>`;
  html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px">`;
  html += `<div class="detail-stat"><div class="stat-val">${d.total_usage||0}%</div><div class="stat-label">总体占用</div></div>`;
  html += `<div class="detail-stat"><div class="stat-val">${d.cores||'-'}</div><div class="stat-label">核心数</div></div>`;
  html += `<div class="detail-stat"><div class="stat-val">${d.freq||'-'}</div><div class="stat-label">频率</div></div>`;
  html += `</div>`;
  if (d.load_avg && d.load_avg.length) {
    html += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:8px">负载均值: <b>${d.load_avg[0]}</b> (1min) &nbsp; <b>${d.load_avg[1]}</b> (5min) &nbsp; <b>${d.load_avg[2]}</b> (15min)</div>`;
  }
  if (d.per_core && d.per_core.length) {
    html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:8px">各核心占用</div>`;
    html += `<div style="display:grid;grid-template-columns:repeat(auto-fill,minmax(100px,1fr));gap:6px">`;
    for (const core of d.per_core) {
      const c = core.usage>85?'var(--red)':core.usage>65?'var(--yellow)':'var(--green)';
      html += `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:6px;padding:8px;text-align:center"><div style="font-size:14px;font-weight:700;color:${c}">${core.usage}%</div><div style="font-size:10px;color:var(--text-dim)">${core.core}</div></div>`;
    }
    html += `</div>`;
  }
  showStatsDetailModal('⚡ CPU 详情', html);
}

async function showMemDetail() {
  const d = statsDetailData.mem;
  if (!d) {
    showStatsDetailModal('🧠 内存详情', '<div style="color:var(--text-dim);padding:20px;text-align:center">加载中...</div>');
    try {
      const r = await fetch(API+'/api/system');
      const sys = await r.json();
      statsDetailData.mem = sys.memory || {};
      return showMemDetail();
    } catch(e) {
      return showStatsDetailModal('🧠 内存详情', '<div style="color:var(--red)">加载失败: '+e.message+'</div>');
    }
  }
  const m = d;
  const pct = m.use_percent||0, c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--green)';
  let html = '';
  html += `<div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin-bottom:16px">`;
  html += `<div class="detail-stat"><div class="stat-val">${pct}%</div><div class="stat-label">占用率</div></div>`;
  html += `<div class="detail-stat"><div class="stat-val">${m.used_mb||0}MB</div><div class="stat-label">已用</div></div>`;
  html += `<div class="detail-stat"><div class="stat-val">${m.total_mb||0}MB</div><div class="stat-label">总计</div></div>`;
  html += `</div>`;
  html += `<div style="background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px;margin-bottom:12px">`;
  html += `<div style="font-size:11px;color:var(--text-dim);margin-bottom:4px">内存条</div>`;
  html += `<div style="width:100%;height:12px;background:var(--border);border-radius:6px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${c};border-radius:6px;transition:width 0.6s ease"></div></div>`;
  html += `<div style="display:flex;justify-content:space-between;margin-top:4px;font-size:11px;color:var(--text-dim)"><span>已用 ${m.used_mb}MB</span><span>可用 ${m.available_mb}MB</span></div>`;
  html += `</div>`;
  // Container memory table
  const running = allContainers.filter(x=>x.state==='running' && x.stats);
  if (running.length) {
    html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:8px">容器内存占用</div>`;
    html += `<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="color:var(--text-dim);border-bottom:1px solid var(--border)"><th style="text-align:left;padding:4px 8px">容器</th><th style="text-align:right;padding:4px 8px">已用</th><th style="text-align:right;padding:4px 8px">限制</th><th style="text-align:right;padding:4px 8px">占用率</th></tr></thead><tbody>`;
    running.sort((a,b)=>(b.stats.memory_usage_mb||0)-(a.stats.memory_usage_mb||0));
    for (const ct of running) {
      const mp = ct.stats.memory_percent||0;
      const mc = mp>85?'var(--red)':mp>65?'var(--yellow)':'var(--green)';
      html += `<tr style="border-bottom:1px solid var(--border)"><td style="padding:4px 8px;color:var(--text-bright)">${esc(ct.custom_name||ct.name)}</td><td style="text-align:right;padding:4px 8px">${ct.stats.memory_usage_mb}MB</td><td style="text-align:right;padding:4px 8px">${ct.stats.memory_limit_mb}MB</td><td style="text-align:right;padding:4px 8px;color:${mc}">${mp}%</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  showStatsDetailModal('🧠 内存详情', html);
}

async function showSysDetail() {
  const d = statsDetailData.sys;
  if (!d) {
    showStatsDetailModal('💾 系统详情', '<div style="color:var(--text-dim);padding:20px;text-align:center">加载中...</div>');
    try {
      const r = await fetch(API+'/api/system');
      const sys = await r.json();
      statsDetailData.sys = sys;
      return showSysDetail();
    } catch(e) {
      return showStatsDetailModal('💾 系统详情', '<div style="color:var(--red)">加载失败: '+e.message+'</div>');
    }
  }
  const disk = d.disk || {};
  let html = '';
  html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:8px">磁盘占用</div>`;
  for (const [mnt, info] of Object.entries(disk)) {
    const pct = parseInt(info.use_percent)||0;
    const c = pct>85?'var(--red)':pct>65?'var(--yellow)':'var(--accent)';
    const lbl = mnt==='/'?'系统':mnt.replace('/volume','存储');
    html += `<div style="margin-bottom:10px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:10px 12px">`;
    html += `<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px"><span style="font-size:13px;font-weight:600">${lbl} <span style="color:var(--text-dim);font-size:11px;font-weight:400">${mnt}</span></span><span style="font-size:14px;font-weight:700;color:${c}">${pct}%</span></div>`;
    html += `<div style="width:100%;height:8px;background:var(--border);border-radius:4px;overflow:hidden"><div style="width:${pct}%;height:100%;background:${c};border-radius:4px"></div></div>`;
    html += `<div style="display:flex;justify-content:space-between;margin-top:4px;font-size:11px;color:var(--text-dim)"><span>已用 ${info.used}</span><span>总计 ${info.size}</span><span>可用 ${info.available}</span></div>`;
    html += `</div>`;
  }

  // Container disk usage (SizeRw = writable layer size)
  const containersWithDisk = allContainers.filter(x => x.full_id);
  if (containersWithDisk.length) {
    html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin:16px 0 8px">容器磁盘占用</div>`;
    // Fetch disk usage for each container
    const diskData = await Promise.all(containersWithDisk.map(async c => {
      try {
        const r = await fetch(API + '/api/container/' + c.id + '/disk');
        if (!r.ok) return { id: c.id, name: c.custom_name || c.name, size: 0, error: true };
        const j = await r.json();
        return { id: c.id, name: c.custom_name || c.name, size: j.size_rw || 0 };
      } catch(e) { return { id: c.id, name: c.custom_name || c.name, size: 0, error: true }; }
    }));
    const validData = diskData.filter(x => !x.error && x.size > 0).sort((a,b) => b.size - a.size);
    if (validData.length) {
      const maxSize = validData[0].size;
      html += `<div style="display:flex;flex-direction:column;gap:4px">`;
      for (const cd of validData) {
        const barPct = maxSize > 0 ? Math.round(cd.size / maxSize * 100) : 0;
        const c = barPct>80?'var(--red)':barPct>50?'var(--yellow)':'var(--accent)';
        html += `<div style="display:flex;align-items:center;gap:8px;font-size:11px">`;
        html += `<span style="min-width:100px;max-width:160px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;color:var(--text-bright)" title="${esc(cd.name)}">${esc(cd.name)}</span>`;
        html += `<div style="flex:1;height:14px;background:var(--bg2);border:1px solid var(--border);border-radius:3px;overflow:hidden">`;
        html += `<div style="width:${barPct}%;height:100%;background:${c};border-radius:2px;transition:width 0.5s ease"></div>`;
        html += `</div>`;
        html += `<span style="min-width:55px;text-align:right;color:var(--text-dim);font-family:monospace;font-size:10px">${formatBytes(cd.size)}</span>`;
        html += `</div>`;
      }
      html += `</div>`;
    } else {
      html += `<div style="color:var(--text-dim);font-size:12px;padding:4px 0">暂无容器磁盘数据</div>`;
    }
  }

  showStatsDetailModal('💾 系统详情', html);
}

async function showNetDetail() {
  const d = statsDetailData.net;
  if (!d) {
    showStatsDetailModal('🌐 网络详情', '<div style="color:var(--text-dim);padding:20px;text-align:center">加载中...</div>');
    try {
      const r = await fetch(API+'/api/system/network-info');
      statsDetailData.net = await r.json();
      return showNetDetail();
    } catch(e) {
      return showStatsDetailModal('🌐 网络详情', '<div style="color:var(--red)">加载失败: '+e.message+'</div>');
    }
  }
  let html = '';
  if (d.hostname) html += `<div style="font-size:12px;color:var(--text-dim);margin-bottom:12px">主机名: <b>${esc(d.hostname)}</b> &nbsp; IP: <b>${esc(d.ip||'-')}</b></div>`;

  // Filter to only non-docker interfaces (physical + main bridges)
  const ifaces = (d.interfaces||[]).filter(i => !i.name.startsWith('docker') && !i.name.startsWith('veth') && !i.name.startsWith('br-') && i.name !== 'lo');
  // If all filtered out, show top 5 by traffic
  const showIfaces = ifaces.length ? ifaces : (d.interfaces||[]).sort((a,b)=>(b.rx_bytes+b.tx_bytes)-(a.rx_bytes+a.tx_bytes)).slice(0,5);

  if (showIfaces.length) {
    html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin-bottom:10px">网卡流量</div>`;
    for (const iface of showIfaces) {
      const totalBps = (iface.rx_bytes + iface.tx_bytes);
      const isDefault = iface.is_default;
      html += `<div style="margin-bottom:14px;background:var(--bg2);border:1px solid var(--border);border-radius:8px;padding:12px 14px">`;
      html += `<div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:8px">`;
      html += `<div style="font-size:13px;font-weight:600">${esc(iface.name)} ${isDefault?'<span style="font-size:10px;color:var(--accent);background:rgba(88,166,255,0.1);padding:1px 6px;border-radius:3px">默认</span>':''}</div>`;
      html += `<div style="font-size:11px;color:var(--text-dim)">↓ <b style="color:var(--green)">${iface.rx_human}</b> &nbsp; ↑ <b style="color:var(--accent)">${iface.tx_human}</b></div>`;
      html += `</div>`;
      // ECG-style canvas
      const canvasId = 'net-ecg-' + iface.name.replace(/[^a-z0-9]/gi,'_');
      html += `<canvas id="${canvasId}" width="560" height="60" style="width:100%;height:60px;border-radius:4px;background:var(--bg)"></canvas>`;
      html += `</div>`;
    }
  } else {
    html += `<div style="color:var(--text-dim);padding:20px;text-align:center">无网络接口信息</div>`;
  }

  // Container network table
  const running = allContainers.filter(x=>x.state==='running' && x.stats);
  if (running.length) {
    html += `<div style="font-size:12px;font-weight:600;color:var(--text-dim);margin:16px 0 8px">容器网络流量</div>`;
    html += `<table style="width:100%;border-collapse:collapse;font-size:12px"><thead><tr style="color:var(--text-dim);border-bottom:1px solid var(--border)"><th style="text-align:left;padding:4px 8px">容器</th><th style="text-align:right;padding:4px 8px">接收</th><th style="text-align:right;padding:4px 8px">发送</th></tr></thead><tbody>`;
    running.sort((a,b)=>(b.stats.network_rx||0)-(a.stats.network_rx||0));
    for (const ct of running) {
      html += `<tr style="border-bottom:1px solid var(--border)"><td style="padding:4px 8px;color:var(--text-bright)">${esc(ct.custom_name||ct.name)}</td><td style="text-align:right;padding:4px 8px;color:var(--green)">${formatBytes(ct.stats.network_rx||0)}</td><td style="text-align:right;padding:4px 8px;color:var(--accent)">${formatBytes(ct.stats.network_tx||0)}</td></tr>`;
    }
    html += `</tbody></table>`;
  }
  showStatsDetailModal('🌐 网络详情', html);

  // Draw ECG after DOM update
  setTimeout(() => {
    for (const iface of showIfaces) {
      const canvasId = 'net-ecg-' + iface.name.replace(/[^a-z0-9]/gi,'_');
      drawEcgCanvas(canvasId, iface.rx_bytes, iface.tx_bytes);
    }
  }, 50);
}

// ECG-style canvas drawing
function drawEcgCanvas(canvasId, rxBytes, txBytes) {
  const canvas = document.getElementById(canvasId);
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  const mid = H / 2;

  // Clear
  ctx.clearRect(0, 0, W, H);

  // Grid lines
  ctx.strokeStyle = 'rgba(48,54,61,0.5)';
  ctx.lineWidth = 0.5;
  for (let y = 0; y < H; y += 15) {
    ctx.beginPath(); ctx.moveTo(0, y); ctx.lineTo(W, y); ctx.stroke();
  }
  for (let x = 0; x < W; x += 40) {
    ctx.beginPath(); ctx.moveTo(x, 0); ctx.lineTo(x, H); ctx.stroke();
  }

  // Baseline
  ctx.strokeStyle = 'rgba(48,54,61,0.8)';
  ctx.lineWidth = 1;
  ctx.beginPath(); ctx.moveTo(0, mid); ctx.lineTo(W, mid); ctx.stroke();

  // Generate pseudo-waveform from bytes (deterministic seed)
  const seed = (rxBytes + txBytes) || 1;
  const points = 120;
  const spikeInterval = Math.max(8, Math.floor(400000000 / (seed + 10000000)));

  // RX waveform (green, upper half)
  ctx.strokeStyle = 'rgba(63,185,80,0.9)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < points; i++) {
    const x = (i / points) * W;
    let y = mid * 0.5;
    // ECG-like pattern: baseline with periodic spikes
    const phase = (i + seed) % spikeInterval;
    if (phase < 3) y -= 8 + (seed % 5);         // QRS up
    else if (phase < 5) y += 4;                   // QRS down
    else if (phase < 8) y -= 3;                   // T wave
    else y += Math.sin((i + seed) * 0.3) * 2;    // noise
    y = Math.max(2, Math.min(mid - 2, y));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // TX waveform (blue, lower half)
  ctx.strokeStyle = 'rgba(88,166,255,0.9)';
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  for (let i = 0; i < points; i++) {
    const x = (i / points) * W;
    let y = mid + mid * 0.3;
    const phase = (i + seed * 2) % Math.max(6, spikeInterval - 2);
    if (phase < 2) y += 6 + (seed % 4);
    else if (phase < 4) y -= 3;
    else y += Math.sin((i + seed * 2) * 0.25) * 1.5;
    y = Math.max(mid + 2, Math.min(H - 2, y));
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  }
  ctx.stroke();

  // Labels
  ctx.font = '10px monospace';
  ctx.fillStyle = 'rgba(63,185,80,0.7)';
  ctx.fillText('↓RX', 4, 12);
  ctx.fillStyle = 'rgba(88,166,255,0.7)';
  ctx.fillText('↑TX', 4, H - 4);
}

// ===== UPDATE MODAL =====
let updateInfo = null;

function checkVersion() {
  fetch(API+'/api/version').then(r=>r.json()).then(data=>{
    updateInfo = data;
    document.getElementById('hdrVer').textContent = 'v' + data.local;
    const btn = document.getElementById('hdrUpdateBtn');
    if (data.has_update) {
      btn.style.display = 'inline-flex';
      btn.textContent = '⬆ v' + data.remote;
    } else {
      btn.style.display = 'none';
    }
  }).catch(()=>{});
}

function showUpdateModal() {
  if (!updateInfo || !updateInfo.has_update) return;
  const overlay = document.getElementById('updateOverlay');
  document.getElementById('updateLocalVer').textContent = updateInfo.local;
  document.getElementById('updateRemoteVer').textContent = updateInfo.remote;
  document.getElementById('updateDate').textContent = updateInfo.date || '';
  const clUl = document.getElementById('updateChangelog');
  clUl.innerHTML = '';
  if (updateInfo.changelog && updateInfo.changelog.length) {
    updateInfo.changelog.forEach(item=>{
      const li = document.createElement('li');
      li.textContent = item;
      clUl.appendChild(li);
    });
  } else {
    clUl.innerHTML = '<li>无更新说明</li>';
  }
  document.getElementById('updateProgress').classList.remove('show');
  document.getElementById('updateProgress').innerHTML = '';
  document.getElementById('btnUpdate').disabled = false;
  document.getElementById('btnUpdate').textContent = '立即更新';
  overlay.classList.add('show');
}

function closeUpdateModal() {
  document.getElementById('updateOverlay').classList.remove('show');
}

function doUpdate() {
  const btn = document.getElementById('btnUpdate');
  const progress = document.getElementById('updateProgress');
  btn.disabled = true;
  btn.textContent = '更新中...';
  progress.classList.add('show');
  progress.innerHTML = '<div>⏳ 正在下载最新版本...</div><div class="progress-bar"><div class="progress-fill" id="updateProgressFill" style="width:30%"></div></div>';
  
  fetch(API+'/api/update', {method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({target_version: updateInfo.remote})})
    .then(r=>r.json())
    .then(data=>{
      document.getElementById('updateProgressFill').style.width = '100%';
      progress.innerHTML = '<div style="color:var(--green)">✅ ' + data.message + '</div>';
      btn.textContent = '重启面板';
      btn.disabled = false;
      btn.onclick = doRestart;
    })
    .catch(e=>{
      progress.innerHTML = '<div style="color:var(--red)">❌ 更新失败: ' + e.message + '</div>';
      btn.textContent = '重试';
      btn.disabled = false;
    });
}

function doRestart() {
  const progress = document.getElementById('updateProgress');
  progress.innerHTML = '<div style="color:var(--yellow)">🔄 正在重启面板服务...</div>';
  fetch(API+'/api/restart', {method:'POST'}).then(()=>{
    setTimeout(()=>{
      progress.innerHTML = '<div style="color:var(--green)">✅ 面板正在重启，3秒后自动刷新...</div>';
      setTimeout(()=>location.reload(), 3000);
    }, 2000);
  }).catch(e=>{
    progress.innerHTML = '<div style="color:var(--red)">❌ 重启失败: ' + e.message + '</div>';
  });
}

// 初始检测版本
setTimeout(checkVersion, 1000);
// 每5分钟检测一次
setInterval(checkVersion, 300000);

loadData();
setInterval(loadData,30000);
</script>

<!-- 更新弹窗 -->
<div class="update-overlay" id="updateOverlay" onclick="if(event.target===this)closeUpdateModal()">
  <div class="update-panel">
    <div class="update-header">
      <h2>🔄 版本更新</h2>
      <span class="close-btn" onclick="closeUpdateModal()">✕</span>
    </div>
    <div class="update-meta">
      <span>当前版本: <b id="updateLocalVer">-</b></span>
      <span>最新版本: <b id="updateRemoteVer" style="color:var(--green)">-</b></span>
      <span id="updateDate"></span>
    </div>
    <div class="update-changelog">
      <div class="cl-title">更新说明</div>
      <ul id="updateChangelog"></ul>
    </div>
    <div class="update-actions">
      <button class="btn-update" id="btnUpdate" onclick="doUpdate()">立即更新</button>
      <button onclick="closeUpdateModal()">稍后再说</button>
    </div>
    <div class="update-progress" id="updateProgress"></div>
  </div>
</div>

<!-- Stats Detail Modal -->
<div class="stats-detail-overlay" id="statsDetailOverlay" onclick="if(event.target===this)closeStatsDetailModal()">
  <div class="stats-detail-panel">
    <div class="stats-detail-header">
      <h2 id="statsDetailTitle">详情</h2>
      <span class="close-btn" onclick="closeStatsDetailModal()">✕</span>
    </div>
    <div id="statsDetailBody"></div>
  </div>
</div>

</body>
</html>
"""
