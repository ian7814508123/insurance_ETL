import argparse
import json
import pymupdf
from pathlib import Path
from typing import List, Dict, Any
from google.genai import types
import time

from pipeline.orchestrator import PipelineOrchestrator
from pipeline.pdf_processor import HybridPDFProcessor

import pymupdf
from pathlib import Path
from typing import List


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
            }

            final_items = orchestrator.process(content, base_info)

            target_file = output_dir / f"{fp.stem}.json"
            save_json(target_file, final_items)
            print(f"\n[成功] 已將結果寫入: {target_file}")

        except Exception as exc:
            print(f"\n[失敗] {fp.name} 發生錯誤: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
