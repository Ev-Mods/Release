import os, sys, time
from pathlib import Path
import shutil
import multiprocessing
import queue
import requests

SPEED_TEST_REPO_ID = "google-bert/bert-base-uncased"
SPEED_TEST_FILENAME = "model.safetensors"
CACHE_DIR = Path(sys.executable).parent / ".speedtest_cache" if getattr(sys, 'frozen', False) else Path(".speedtest_cache")

def test_connection(url, timeout=5):
    """
    Tests connectivity to a given URL by sending a HEAD request.
    Returns:
        bool: True if the connection is successful (HTTP 200), False otherwise.
    """
    try:
        response = requests.head(url, timeout=timeout)
        return response.status_code == 200
    except requests.RequestException:
        return False

def determine_mirror_setting():
    """
    Checks connection to Hugging Face and its mirror. Recommends the mirror
    only if the main site is unreachable and the mirror is reachable.
    
    Returns:
        bool: True if the mirror should be used, False otherwise.
    """
    if not test_connection("https://huggingface.co"):
        if test_connection("https://hf-mirror.com"):
            return True
    return False

def setup_hf_environment(use_mirror):
    """Sets Hugging Face environment variables for the test."""
    os.environ["HF_HOME"] = str(CACHE_DIR)
    
    os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
    if use_mirror:
        os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'
    elif 'HF_ENDPOINT' in os.environ:
        # Ensure mirror endpoint is not set if not requested
        del os.environ['HF_ENDPOINT']

def cleanup_cache():
    """Removes the speed test cache directory."""
    if CACHE_DIR.is_dir():
        try:
            shutil.rmtree(CACHE_DIR)
        except OSError:
            pass

def calculate_speed_mbps(start_time, end_time, downloaded_bytes):
    """Calculates download speed in Megabits per second (Mbps)."""
    duration = end_time - start_time
    if duration > 0 and downloaded_bytes > 0:
        bits_downloaded = downloaded_bytes * 8
        speed_bps = bits_downloaded / duration
        speed_mbps = speed_bps / 1_000_000
        return speed_mbps
    return 0.0

def download_target(repo_id, filename, result_queue):
    """Target function for the download process. To be run in a separate process."""
    from huggingface_hub import hf_hub_download, get_hf_file_metadata, hf_hub_url
    
    try:
        start_time = time.time()
        hf_hub_download(
            repo_id=repo_id,
            filename=filename
        )
        end_time = time.time()

        url = hf_hub_url(repo_id=repo_id, filename=filename)
        metadata = get_hf_file_metadata(url=url)
        file_size = metadata.size

        result_queue.put({
            "size": file_size,
            "start_time": start_time,
            "end_time": end_time
        })
    except Exception as e:
        result_queue.put(e)

def measure_download_speed(use_mirror, timeout=20):
    setup_hf_environment(use_mirror)
    cleanup_cache()

    result_queue = multiprocessing.Queue()
    download_process = multiprocessing.Process(
        target=download_target,
        args=(SPEED_TEST_REPO_ID, SPEED_TEST_FILENAME, result_queue)
    )

    try:
        download_process.start()
        download_process.join(timeout)

        if download_process.is_alive():
            download_process.terminate()
            download_process.join()
            raise TimeoutError(f"Test timed out after {timeout} seconds. Connection may be too slow.")

        result = result_queue.get_nowait()
        
        if isinstance(result, Exception):
            raise result

        if isinstance(result, dict):
            speed_mbps = calculate_speed_mbps(result["start_time"], result["end_time"], result["size"])
            return speed_mbps
        else:
            raise ValueError("Download process returned an unexpected result.")

    except Exception as e:
        raise e
    finally:
        if download_process.is_alive():
            download_process.terminate()
            download_process.join()
        cleanup_cache()
