import time
from tqdm import tqdm


def custom_collecting_os_info():
    # 設定參數
    total_size_gb = 8.3
    total_size_bytes = total_size_gb * 1024 * 1024 * 1024
    speed_mb_per_sec = 100
    speed_bytes_per_sec = speed_mb_per_sec * 1024 * 1024

    frames = ["|", "/", "-", "\\"]
    update_interval = 0.3

    # 建立進度條，unit 指定為 Byte，tqdm 會自動處理 GB/MB 顯示
    with tqdm(
        total=total_size_bytes, unit="B", unit_scale=True, unit_divisor=1024
    ) as pbar:
        current_progress = 0
        last_spinner_update = time.time()
        frame_idx = 0

        while current_progress < total_size_bytes:
            time.sleep(update_interval)
            chunk_size = speed_bytes_per_sec * update_interval
            if current_progress + chunk_size > total_size_bytes:
                chunk_size = total_size_bytes - current_progress

            current_progress += chunk_size
            pbar.update(chunk_size)

            # 2. 處理 Spinner 轉速 (獨立邏輯)
            # 這裡可以根據需要調整 frame_idx，讓它跟進度解耦
            frame_idx = (frame_idx + 1) % len(frames)
            pbar.set_description(f"Collecting osinfo...... {frames[frame_idx]}")


custom_collecting_os_info()
