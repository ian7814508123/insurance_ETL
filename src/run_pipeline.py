import argparse
import json
import pymupdf
from pathlib import Path
from typing import List, Dict, Any
from google.genai import types
import time

from pipeline.orchestrator import PipelineOrchestrator


def pdf_to_parts(pdf_path: Path, max_pages: int = 15) -> List[types.Part]:
    """將 PDF 轉成圖片 (同原本 claim_item_extractor 的邏輯)"""
    doc = pymupdf.open(str(pdf_path))
    parts: List[types.Part] = []
    try:
        for i in range(min(max_pages, len(doc))):
            pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(3, 3))
            parts.append(
                types.Part.from_bytes(data=pix.tobytes("jpg"), mime_type="image/jpeg")
            )
    finally:
        doc.close()
    return parts


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


def save_json_array(path: Path, data: List[Dict[str, Any]]) -> None:
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

    for idx, fp in enumerate(files, start=1):
        print(f"\n======================================")
        print(f"[{idx}/{len(files)}] 正在處理: {fp.name}")
        print(f"======================================")

        try:
            if fp.suffix.lower() == ".pdf":
                content = pdf_to_parts(fp)
            else:
                content = [fp.read_text(encoding="utf-8")]

            base_info = {
                "product_code": fp.stem,
                "level": args.level,
                "document_name": fp.name,
            }

            final_items = orchestrator.process(content, base_info)

            target_file = output_dir / f"{fp.stem}.json"
            save_json_array(target_file, final_items)
            print(f"\n[成功] 已將結果寫入: {target_file}")

        except Exception as exc:
            print(f"\n[失敗] {fp.name} 發生錯誤: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
