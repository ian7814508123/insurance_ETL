"""
1. 用途說明:
   多階段 Agent Pipeline 自動化處理入口 (Automation Pipeline Run Entry)。本腳本讀取指定目錄下的保險商品條款（支援 PDF 與純文字 TXT），自動調用智慧型 PDF 前處理器進行亂碼修復與前處理，然後啟動 PipelineOrchestrator 執行多階段 Agent 解析與詞庫對齊，最後輸出標準理賠精算 JSON。

2. 如何使用:
   - 啟動 Pipeline 處理特定商品目錄下的所有商品：
     python src/run_pipeline.py --input-dir ./product --level PRODUCT
   - 僅對齊並提取名詞定義，略過後續給付項目解析 (省 API 成本與執行時間)：
     python src/run_pipeline.py --input-dir ./product --definitions --level PRODUCT
   - 自訂輸出路徑：
     python src/run_pipeline.py --input-dir ./product --output-dir ./data/claim_items/products
"""

import argparse
import json
try:
    import pymupdf
except ImportError:
    pymupdf = None
from pathlib import Path
from typing import List, Dict, Any
from google.genai import types
import time

from pipeline.orchestrator import PipelineOrchestrator
from pipeline.pdf_processor import HybridPDFProcessor


def pdf_to_parts(pdf_path: Path, max_pages: int = 15) -> List[types.Part]:
    """將 PDF 轉成圖片"""
    doc = pymupdf.open(str(pdf_path))

    # --- 新增修復邏輯 ---
    # 取得 PDF 的 Catalog (目錄) 物件
    cat = doc.pdf_catalog()
    # 如果 Catalog 中存在 StructTreeRoot，將其設為空 (null)
    # 這會讓 MuPDF 渲染時無視損壞的標籤結構
    doc.xref_set_key(cat, "StructTreeRoot", "null")
    # ------------------

    parts: List[types.Part] = []
    try:
        total_pages = len(doc)
        for i in range(min(max_pages, total_pages)):
            # 渲染頁面
            pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(3, 3))
            parts.append(
                types.Part.from_bytes(data=pix.tobytes("jpg"), mime_type="image/jpeg")
            )
    finally:
        doc.close()
    return parts


from typing import List, Dict, Any, Union


def load_json_array(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def save_json(path: Path, data: Union[List[Any], Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="多階段 Agent Pipeline 理賠項目抽取")
    parser.add_argument(
        "--input-dir",
        default=str(Path.cwd() / "product"),
        help="輸入目錄 (含 .txt 或 .pdf)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "data" / "claim_items" / "products"),
        help="輸出目錄",
    )
    parser.add_argument("--level", default="PRODUCT", help="保險商品知識層級")
    parser.add_argument(
        "--definitions",
        action="store_true",
        help="是否僅提取並對齊名詞定義，略過後續給付項目解析 (節省 API與時間)",
    )

    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    files = sorted(
        f
        for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".txt", ".pdf"} and f.name != "說明.txt"
    )

    print(f"找到 {len(files)} 個檔案，準備啟動 Pipeline...")
    orchestrator = PipelineOrchestrator()
    pdf_processor = HybridPDFProcessor()  # 初始化前處理器

    for idx, fp in enumerate(files, start=1):
        print(f"\n======================================")
        print(f"[{idx}/{len(files)}] 正在處理: {fp.name}")
        print(f"======================================")

        try:
            if fp.suffix.lower() == ".pdf":
                # 使用智慧型 PDF 前處理器，自動處理亂碼並存成 Markdown
                md_content = pdf_processor.process(str(fp))
                content = [md_content]
            else:
                content = [fp.read_text(encoding="utf-8")]

            base_info = {
                "product_code": fp.stem,
                "level": args.level,
                "document_name": fp.name,
                "only_definitions": args.definitions,  # 傳遞僅提取名詞定義的 Flag
            }

            final_items = orchestrator.process(content, base_info)

            # ─── 自動後處理：參數來源三向診斷與命名一致性即時修復 ───
            try:
                from diagnose_parameters import ParameterDiagnoser
                diagnoser = ParameterDiagnoser(output_dir.parent)
                final_items, warnings, modified = diagnoser.process_data(final_items, fix_mode=True)
                if modified and warnings:
                    print(f"  -> [後處理] 自動標記與對齊完成，共修復/標記 {len(warnings)} 處變數。")
            except Exception as pe:
                print(f"  -> [後處理警告] 執行自動對齊診斷失敗: {pe}")
            # ──────────────────────────────────────────────────

            target_file = output_dir / f"{fp.stem}.json"
            save_json(target_file, final_items)
            print(f"\n[成功] 已將結果寫入: {target_file}")

        except Exception as exc:
            print(f"\n[失敗] {fp.name} 發生錯誤: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
