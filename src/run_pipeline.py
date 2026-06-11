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


import re
from fuse_product import ProductFuser


def find_matching_rate_file(claim_file: Path, rate_dir: Path) -> Path:
    """在指定的費率目錄中，尋找與條款檔案最匹配的費率 PDF 或圖片檔案。"""
    if not rate_dir or not rate_dir.exists():
        return None

    # 1. 檔名完全相同
    exact_match = rate_dir / claim_file.name
    if exact_match.exists():
        return exact_match

    # 2. 去除常見修飾詞（如 _條款、_主契約 等）後的前綴模糊匹配
    clean_stem = re.sub(
        r"(_條款|_條款書|_主約|_主契約|_benefit|_claim_items)$",
        "",
        claim_file.stem,
        flags=re.IGNORECASE,
    )

    # 遍歷 rate_dir 尋找匹配
    for f in rate_dir.iterdir():
        if f.is_file() and f.suffix.lower() in {".pdf", ".png", ".jpg", ".jpeg"}:
            # 若費率檔名包含 clean_stem，或者 clean_stem 包含在費率檔名中
            if f.stem.startswith(clean_stem) or clean_stem in f.stem:
                # 排除自己本身，以防 input 和 rate 目錄設為同一個
                if f.resolve() != claim_file.resolve():
                    return f
    return None


def main():
    parser = argparse.ArgumentParser(description="多階段 Agent Pipeline 理賠項目與費率抽取融合")
    parser.add_argument(
        "--input-dir",
        default=str(Path.cwd() / "product"),
        help="輸入目錄 (含條款 .txt 或 .pdf)",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "data" / "claim_items" / "products"),
        help="輸出目錄",
    )
    parser.add_argument(
        "--rate-dir",
        default=None,
        help="費率 PDF 所在目錄，若指定則會自動尋找對應商品的費率表進行解析與 UAP 融合",
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
    rate_dir = Path(args.rate_dir) if args.rate_dir else None

    files = sorted(
        f
        for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".txt", ".pdf"} and f.name != "說明.txt"
    )

    print(f"找到 {len(files)} 個條款檔案，準備啟動 Pipeline...")
    orchestrator = PipelineOrchestrator()
    pdf_processor = HybridPDFProcessor()  # 初始化前處理器

    for idx, fp in enumerate(files, start=1):
        print(f"\n======================================")
        print(f"[{idx}/{len(files)}] 正在處理條款: {fp.name}")
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

            # ─── 自動串接費率解析與融合 ───
            if rate_dir and not args.definitions:
                rate_file = find_matching_rate_file(fp, rate_dir)
                if rate_file:
                    print(f"  -> [費率解析] 找到對應的費率文件: {rate_file.name}")
                    
                    # 1. 提取標準 parameters 作為 alignment_hints
                    alignment_hints = {}
                    for item in final_items.get("claim_items", []):
                        for param in item.get("parameters", []):
                            if param.get("source_type") == "DEFINITION_ALIGN":
                                alignment_hints[param["param_name"]] = param["display_name"]

                    # 2. 呼叫 premium_extractor 進行引導解析
                    try:
                        from premium_extractor import ConfidenceOrchestrator
                        rate_orchestrator = ConfidenceOrchestrator()
                        print("  -> [費率解析] 啟動引導式費率數據提取 (利用條款參數進行對齊)...")
                        rate_results = rate_orchestrator.process(
                            str(rate_file), alignment_hints=alignment_hints
                        )
                        
                        # 3. 融合為 Unified Actuarial Package (UAP)
                        print("  -> [UAP 融合] 開始將條款與費率融合...")
                        fuser = ProductFuser()
                        final_items = fuser.fuse_claim_and_rates(final_items, rate_results)
                        print("  -> [UAP 融合] 融合與交叉勾稽完成！")
                    except Exception as re_err:
                        print(f"  -> [費率解析/融合警告] 失敗: {re_err}")
                else:
                    print(f"  -> [費率解析] 在 {rate_dir} 下未找到對應 {fp.stem} 的費率文件，跳過費率融合。")

            target_file = output_dir / f"{fp.stem}.json"
            save_json(target_file, final_items)
            print(f"\n[成功] 已將結果寫入: {target_file}")

        except Exception as exc:
            print(f"\n[失敗] {fp.name} 發生錯誤: {exc}")
            import traceback

            traceback.print_exc()


if __name__ == "__main__":
    main()
