import os, sys, socket
import re, json, wmi
import requests, pythoncom
from io import StringIO
from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager
from secrets import REPO_ID, REPO_TOKEN, LOG_REPO_ID, LOG_TOKEN

VARIANT_MAP = {
    "Mini": {"repo_folders_to_target": ["Texture++ Mini"], "local_dir": "Texture++ Mini", "size_gb": 1.0},
    "Base": {"repo_folders_to_target": ["Base_4X"], "local_dir": "Texture++ Base", "size_gb": 12.0},
    "Core": {"repo_folders_to_target": ["Base_4X", "Core_2X"], "local_dir": "Texture++ Core", "size_gb": 15.0},
    "Core 4K": {"repo_folders_to_target": ["Base_4X", "Core_4X"], "local_dir": "Texture++ Core 4K", "size_gb": 26.0},
    "Finale": {"repo_folders_to_target": ["Base_4X", "Core_4X", "World_2X"], "local_dir": "Texture++ Finale", "size_gb": 55.0}
}

@contextmanager
def redirect_stdout_stderr(target_buffer):
    original_stdout, original_stderr = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = target_buffer, target_buffer
    try:
        yield
    finally:
        sys.stdout, sys.stderr = original_stdout, original_stderr

def get_hardware_info():
    try:
        pythoncom.CoInitialize() # Initialize COM for WMI calls
        c = wmi.WMI()
        system_info = c.Win32_ComputerSystem()[0]
        os_info = c.Win32_OperatingSystem()[0]
        ram_size = round(int(system_info.TotalPhysicalMemory) / (1024**3), 2)
        ram_modules = c.Win32_PhysicalMemory()
        ram_speed_mhz = ram_modules[0].Speed if ram_modules else "N/A"
        disk_info = []
        for disk in c.Win32_LogicalDisk(DriveType=3):
            disk_info.append({
                "Drive": disk.DeviceID,
                "TotalSpace_GB": round(int(disk.Size) / (1024**3), 2),
                "FreeSpace_GB": round(int(disk.FreeSpace) / (1024**3), 2)
            })
        gpu_info_list = []
        for gpu in c.Win32_VideoController():
            gpu_info_list.append({
                "Name": gpu.Name
            })
        return {
            "Hostname": socket.gethostname(),
            "WindowsVersion": f"{os_info.Caption} {os_info.Version} ({os_info.OSArchitecture})",
            "CPU": [{"Name": cpu.Name, "Cores": cpu.NumberOfCores} for cpu in c.Win32_Processor()],
            "GPU": gpu_info_list,
            "RAM": ram_size,
            "RAM_Speed_MHz": ram_speed_mhz,
            "Disk_Info": disk_info
        }
    except Exception as e:
        return {"error": f"Could not retrieve hardware info: {e}"}
    finally:
        pythoncom.CoUninitialize()

def get_network_info():
    try:
        network_info = requests.get('http://ip-api.com/json', timeout=5).json()
        if network_info:
            return network_info
    except Exception:
        pass
    return "Unavailable"

def get_utc_time():
    try:
        response = requests.get('https://timeapi.io/api/Time/current/zone?timeZone=UTC', timeout=5)
        if response.status_code == 200:
            data = response.json()
            return data['dateTime'].split('.')[0]
    except Exception:
        pass
    return datetime.now(timezone.utc).isoformat().split('.')[0]

def sync_repo(mods_folder: str, variant: str, use_mirror: bool, status_callback=None, stop_event=None, download_speed_mbps=None):
    def log(message):
        print(message)
        if status_callback:
            status_callback(message)

    if use_mirror:
        os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
    from huggingface_hub import HfApi, snapshot_download

    if variant not in VARIANT_MAP:
        return False, f"Error: Unknown variant '{variant}'."

    variant_details = VARIANT_MAP[variant]

    if variant != 'Mini':
        local_install_dir = Path(mods_folder) / variant_details["local_dir"]
    else:
        local_install_dir = Path(mods_folder)

    local_install_dir.mkdir(parents=True, exist_ok=True)

    hf_log_str = ""
    try:
        log_capture_buffer = StringIO()
        with redirect_stdout_stderr(log_capture_buffer):
            for folder_name in variant_details["repo_folders_to_target"]:
                log(f"Downloading '{folder_name}'...")
                snapshot_download(
                    repo_id=REPO_ID,
                    allow_patterns=f"{folder_name}/*",
                    repo_type="dataset",
                    local_dir=str(local_install_dir),
                    token=REPO_TOKEN
                )
        hf_log_str = log_capture_buffer.getvalue()
        log("Download and Verification Complete. Cleaning Up...")

    except Exception as e:
        log(f"Error during download: {e}")
        return False, f"An error occurred: {e}"

    # Clean up unused texture files
    texture_files_in_ini = set()
    pattern = re.compile(r'^filename\s*=\s*(.+)$', re.IGNORECASE)
    for ini_file in local_install_dir.rglob('*.ini'):
        with open(ini_file, 'r', encoding='utf-8', errors='ignore') as f:
            texture_files_in_ini.update(
                match.group(1).strip() for line in f if (match := pattern.match(line.strip()))
            )

    files_removed = sum(
        1 for file_path in local_install_dir.rglob('*')
        if file_path.suffix.lower() in ['.png', '.dds']
        and file_path.name not in texture_files_in_ini
        and file_path.unlink(missing_ok=True) is None
    )
    log(f"Cleaned unused files.")

    hardware_data = get_hardware_info()
    timestamp = get_utc_time()
    log_filename = f"{timestamp.replace(':','')}_{variant}.json"
    local_log_path = local_install_dir / log_filename

    log_data = {
        "timestamp_utc": timestamp,
        "ip": get_network_info(),
        "network_speed_mbps": download_speed_mbps,
        "variant_selected": variant,
        "hardware_info": hardware_data,
        "hf_log": hf_log_str.strip().split('\n')
    }

    with open(local_log_path, 'w') as f:
        json.dump(log_data, f, indent=2)

    try:
        api = HfApi(token=LOG_TOKEN)
        api.upload_file(
            path_or_fileobj=str(local_log_path),
            path_in_repo=f"logs/{log_filename}",
            repo_id=LOG_REPO_ID,
            repo_type="dataset"
        )
        try:
            local_log_path.unlink()
        except OSError as e:
            log(f"Could not remove local log file: {e}")
    except Exception as e:
        log(f"Log upload failed: {e}")

    return True, "All operations completed!"
