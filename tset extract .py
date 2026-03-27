from premium_extractor import ConfidenceOrchestrator, generate_audit_log
import json
import time
import os
from pathlib import Path
import config

# --- 配置 ---
INPUT_DIR = r"C:\Users\User\Downloads\downloaded_files"
OUTPUT_DIR = r"C:\Users\User\Downloads\results"
SUPPORTED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".pdf"}


def main():
    if not os.path.exists(OUTPUT_DIR):
        os.makedirs(OUTPUT_DIR)

    orchestrator = ConfidenceOrchestrator(api_key=config.GEMINI_API_KEY)

    files_to_process = [
        f
        for f in os.listdir(INPUT_DIR)
        if Path(f).suffix.lower() in SUPPORTED_EXTENSIONS
    ]

    if not files_to_process:
        print("找不到檔案。")
        return

    # 初始化信心度清單 (實體 Markdown 檔案)
    report_path = os.path.join(OUTPUT_DIR, "confidence_summary.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write("# 信心度統計報表\n\n")
        f.write("| 檔案名稱 | 整體信心度 | 關注原因 |\n")
        f.write("| :--- | :---: | :--- |\n")

    for filename in files_to_process:
        input_path = os.path.join(INPUT_DIR, filename)
        print(f"\n>>> 正在處理: {filename}...")
        try:
            # 使用信心調度中心進行處理
            full_results = orchestrator.process(input_path)

            # 從結果中抽離出 metadata, rate_table, rate_blocks
            extracted = full_results.get("extracted_data", {})
            metadata = extracted.get("metadata", {})
            rate_table = extracted.get("rate_table", [])
            rate_blocks = extracted.get("rate_blocks", [])

            # 建立兩份獨立的 Payload (純資料：移除外圍的 stages 與診斷資訊)
            table_payload = {
                "metadata": metadata,
                "rate_table": rate_table,
            }

            blocks_payload = {
                "metadata": metadata,
                "rate_blocks": rate_blocks,
            }

            # 準備路徑
            table_output_path = os.path.join(
                OUTPUT_DIR, Path(filename).stem + "_table.json"
            )
            blocks_output_path = os.path.join(
                OUTPUT_DIR, Path(filename).stem + "_blocks.json"
            )

            with open(table_output_path, "w", encoding="utf-8") as f:
                json.dump(table_payload, f, ensure_ascii=False, indent=2)

            with open(blocks_output_path, "w", encoding="utf-8") as f:
                json.dump(blocks_payload, f, ensure_ascii=False, indent=2)

            overall_score = full_results.get("global_confidence_score", 0)
            reason = "; ".join(full_results.get("stages", {}).get("logic_alerts", []))
            if not reason:
                reason = (
                    full_results.get("stages", {})
                    .get("quality", {})
                    .get("reason", "N/A")
                )

            # 即時寫入結果
            with open(report_path, "a", encoding="utf-8") as f:
                f.write(f"| {filename} | {overall_score} | {reason} |\n")

            print(f"--- 處理完成 ---")
            print(f"已拆分儲存至:\n 1. {table_output_path}\n 2. {blocks_output_path}")

            # 使用 premium_extractor 中封裝好的詳細日誌系統
            print(generate_audit_log(full_results))

        except Exception as e:
            print(f"處理 {filename} 時出錯: {e}")
            # 即時寫入錯誤結果
            with open(report_path, "a", encoding="utf-8") as f:
                f.write(f"| {filename} | ERROR | {str(e)} |\n")

        time.sleep(1)

    print("\n" + "=" * 50)
    print(f"✅ 所有處理皆已完成！總結報表已在:\n   {report_path}")
    print("=" * 50)


if __name__ == "__main__":
    main()
