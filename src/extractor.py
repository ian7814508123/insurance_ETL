"""
整合提取器入口 (Unified Extractor Entry Point)

提供統一的 CLI 介面，整合以下兩個提取器：
  - DefinitionExtractor  → 提取保險商品「名詞定義」
  - ClaimItemExtractor   → 提取保險商品「理賠項目與計算公式」

使用方式（單檔模式）：
  python src/extractor.py --target product_definition  --input-file ./product/xxx.pdf --product UC099
  python src/extractor.py --target product_claim_item  --input-file ./product/xxx.pdf --product UC099

使用方式（批次模式）：
  python src/extractor.py --target product_definition  --input-dir ./product
  python src/extractor.py --target product_claim_item  --input-dir ./product

使用方式（指定輸出路徑）：
  python src/extractor.py --target product_definition  --input-dir ./product --output-dir ./data/definitions
  python src/extractor.py --target product_claim_item  --input-dir ./product --output-dir ./data/claim_items
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

# 確保可以從任意工作目錄正確 import 根目錄的 config 與 src 下的模組
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

import config
from definition_extractor import DefinitionExtractor
from claim_item_extractor import ClaimItemExtractor


# ---------------------------------------------------------------------------
# 常數：預設輸出路徑
# ---------------------------------------------------------------------------
_DEFAULT_DEF_OUTPUT = _PROJECT_ROOT / "data" / "definitions"
_DEFAULT_CLAIM_OUTPUT = _PROJECT_ROOT / "data" / "claim_items"

# 批次處理時，每個檔案間的冷卻秒數（避免觸發 API Rate Limit）
_SLEEP_BETWEEN_FILES = 2.0


# ---------------------------------------------------------------------------
# 工具函式
# ---------------------------------------------------------------------------

def _collect_input_files(input_dir: Path) -> list[Path]:
    """收集目錄下所有可處理的 .pdf / .txt 檔案（排除 說明.txt）。"""
    return sorted(
        f
        for f in input_dir.iterdir()
        if f.is_file()
        and f.suffix.lower() in {".pdf", ".txt"}
        and f.name != "說明.txt"
    )


def _resolve_output_dir(base_dir: Optional[str], default: Path) -> Path:
    """解析輸出目錄；若未指定則使用預設值。"""
    return Path(base_dir) if base_dir else default


# ---------------------------------------------------------------------------
# 名詞定義提取流程
# ---------------------------------------------------------------------------

def _run_definition_single(
    extractor: DefinitionExtractor,
    input_file: Path,
    product_code: str,
    output_dir: Path,
) -> None:
    """單檔模式：提取一份文件的名詞定義。

    Args:
        extractor: DefinitionExtractor 實例。
        input_file: 輸入的 PDF 或文字檔路徑。
        product_code: 商品代碼（作為輸出檔名與 origin_product 欄位）。
        output_dir: 輸出根目錄（含 base.json）。
    """
    base_file = output_dir / "base.json"
    base_defs = extractor.load_definitions(str(base_file))
    target_file = output_dir / "products" / f"{product_code}.json"
    current_defs = extractor.load_definitions(str(target_file))

    print(f"  單檔模式：{input_file.name} -> {product_code}")

    if input_file.suffix.lower() == ".pdf":
        content = extractor.extract_images_from_pdf(str(input_file))
    else:
        content = input_file.read_text(encoding="utf-8")

    new_defs = extractor.extract_definitions(
        content, base_defs, level="PRODUCT", product_code=product_code
    )

    if not new_defs:
        print("  [警告] LLM 回傳無效 JSON，跳過本次結果。")
    print(f"  -> 提取 {len(new_defs)} 筆名詞定義")

    merged = extractor.merge_definitions(current_defs, new_defs)
    extractor.save_definitions(merged, str(target_file))
    print(f"  -> 已寫入 {target_file}")


def _run_definition_batch(
    extractor: DefinitionExtractor,
    input_dir: Path,
    output_dir: Path,
) -> None:
    """批次模式：處理目錄內所有文件的名詞定義。

    Args:
        extractor: DefinitionExtractor 實例。
        input_dir: 包含 .pdf/.txt 條款的目錄。
        output_dir: 輸出根目錄。
    """
    base_file = output_dir / "base.json"
    base_defs = extractor.load_definitions(str(base_file))
    files = _collect_input_files(input_dir)

    print(f"共找到 {len(files)} 個檔案，目標：product_definition")

    for i, fp in enumerate(files):
        print(f"\n[{i + 1}/{len(files)}] 處理：{fp.name}")
        try:
            if fp.suffix.lower() == ".pdf":
                content = extractor.extract_images_from_pdf(str(fp))
            else:
                content = fp.read_text(encoding="utf-8")

            product_code = fp.stem
            target_file = output_dir / "products" / f"{product_code}.json"
            current_defs = extractor.load_definitions(str(target_file))

            new_defs = extractor.extract_definitions(
                content, base_defs, level="PRODUCT", product_code=product_code
            )

            if not new_defs:
                print("  [警告] LLM 回傳無效 JSON，跳過本次結果。")
            print(f"  -> 提取 {len(new_defs)} 筆名詞定義")

            merged = extractor.merge_definitions(current_defs, new_defs)
            extractor.save_definitions(merged, str(target_file))
            print(f"  -> 已寫入 {target_file}")

        except Exception as exc:
            print(f"  [錯誤] {fp.name}: {exc}")

        if i < len(files) - 1:
            time.sleep(_SLEEP_BETWEEN_FILES)


# ---------------------------------------------------------------------------
# 理賠項目提取流程
# ---------------------------------------------------------------------------

def _run_claim_single(
    extractor: ClaimItemExtractor,
    input_file: Path,
    product_code: str,
    output_dir: Path,
) -> None:
    """單檔模式：提取一份文件的理賠項目與計算公式。

    Args:
        extractor: ClaimItemExtractor 實例。
        input_file: 輸入的 PDF 或文字檔路徑。
        product_code: 商品代碼。
        output_dir: 輸出根目錄（含 base.json）。
    """
    base_file = output_dir / "base.json"
    base_items = extractor.load_json_array(base_file)
    target_file = output_dir / "products" / f"{product_code}.json"
    current_items = extractor.load_json_array(target_file)

    print(f"  單檔模式：{input_file.name} -> {product_code}")

    if input_file.suffix.lower() == ".pdf":
        content = extractor.pdf_to_parts(input_file)
    else:
        content = input_file.read_text(encoding="utf-8")

    new_items = extractor.extract(
        content, base_items, level="PRODUCT", product_code=product_code
    )

    if not new_items:
        print("  [警告] LLM 回傳無效 JSON，跳過本次結果。")
    print(f"  -> 提取 {len(new_items)} 筆理賠項目")

    merged = extractor.merge_items(current_items, new_items)
    extractor.save_json_array(target_file, merged)
    print(f"  -> 已寫入 {target_file}")


def _run_claim_batch(
    extractor: ClaimItemExtractor,
    input_dir: Path,
    output_dir: Path,
) -> None:
    """批次模式：處理目錄內所有文件的理賠項目。

    Args:
        extractor: ClaimItemExtractor 實例。
        input_dir: 包含 .pdf/.txt 條款的目錄。
        output_dir: 輸出根目錄。
    """
    base_file = output_dir / "base.json"
    base_items = extractor.load_json_array(base_file)
    files = _collect_input_files(input_dir)

    print(f"共找到 {len(files)} 個檔案，目標：product_claim_item")

    for i, fp in enumerate(files):
        print(f"\n[{i + 1}/{len(files)}] 處理：{fp.name}")
        try:
            if fp.suffix.lower() == ".pdf":
                content = extractor.pdf_to_parts(fp)
            else:
                content = fp.read_text(encoding="utf-8")

            product_code = fp.stem
            target_file = output_dir / "products" / f"{product_code}.json"
            current_items = extractor.load_json_array(target_file)

            new_items = extractor.extract(
                content, base_items, level="PRODUCT", product_code=product_code
            )

            if not new_items:
                print("  [警告] LLM 回傳無效 JSON，跳過本次結果。")
            print(f"  -> 提取 {len(new_items)} 筆理賠項目")

            merged = extractor.merge_items(current_items, new_items)
            extractor.save_json_array(target_file, merged)
            print(f"  -> 已寫入 {target_file}")

        except Exception as exc:
            print(f"  [錯誤] {fp.name}: {exc}")

        if i < len(files) - 1:
            time.sleep(_SLEEP_BETWEEN_FILES)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    """建立 CLI 參數解析器。"""
    parser = argparse.ArgumentParser(
        prog="extractor.py",
        description="保險商品條款提取整合入口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  # 批次提取名詞定義
  python src/extractor.py --target product_definition --input-dir ./product

  # 批次提取理賠項目
  python src/extractor.py --target product_claim_item --input-dir ./product

  # 單檔提取（指定商品代碼）
  python src/extractor.py --target product_claim_item \\
      --input-file ./product/UC099.pdf --product UC099

  # 自訂輸出目錄
  python src/extractor.py --target product_definition \\
      --input-dir ./product --output-dir ./data/definitions
        """,
    )

    parser.add_argument(
        "--target",
        required=True,
        choices=["product_definition", "product_claim_item"],
        help="提取目標：名詞定義 或 理賠項目",
    )

    # --- 路徑相關 ---
    parser.add_argument(
        "--base-dir",
        default=None,
        help="專案根目錄（預設：extractor.py 所在的上層目錄）",
    )
    parser.add_argument(
        "--input-dir",
        default=None,
        help="【批次模式】含 .pdf/.txt 條款的輸入目錄",
    )
    parser.add_argument(
        "--input-file",
        default=None,
        help="【單檔模式】單一條款檔案路徑",
    )
    parser.add_argument(
        "--product",
        default=None,
        help="【單檔模式】商品代碼（作為 JSON 檔名與 origin_product 欄位）",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="輸出根目錄（預設：data/definitions 或 data/claim_items）",
    )
    parser.add_argument(
        "--batch-output-dir",
        default=None,
        help="（同 --output-dir，保留向下相容）",
    )

    return parser


def main() -> None:
    """CLI 主流程：解析參數並分派至對應提取器。"""
    parser = _build_parser()
    args = parser.parse_args()

    # --- 解析輸出目錄（--batch-output-dir 與 --output-dir 擇一使用）---
    raw_output = args.output_dir or args.batch_output_dir

    # --- 分派執行 ---
    if args.target == "product_definition":
        extractor = DefinitionExtractor()
        default_out = _DEFAULT_DEF_OUTPUT
        output_dir = _resolve_output_dir(raw_output, default_out)

        if args.input_file:
            # 單檔模式
            if not args.product:
                parser.error("單檔模式下 --product 為必填參數")
            _run_definition_single(
                extractor,
                input_file=Path(args.input_file),
                product_code=args.product,
                output_dir=output_dir,
            )
        elif args.input_dir:
            # 批次模式
            _run_definition_batch(
                extractor,
                input_dir=Path(args.input_dir),
                output_dir=output_dir,
            )
        else:
            parser.error("請指定 --input-dir（批次）或 --input-file（單檔）")

    elif args.target == "product_claim_item":
        extractor = ClaimItemExtractor()
        default_out = _DEFAULT_CLAIM_OUTPUT
        output_dir = _resolve_output_dir(raw_output, default_out)

        if args.input_file:
            # 單檔模式
            if not args.product:
                parser.error("單檔模式下 --product 為必填參數")
            _run_claim_single(
                extractor,
                input_file=Path(args.input_file),
                product_code=args.product,
                output_dir=output_dir,
            )
        elif args.input_dir:
            # 批次模式
            _run_claim_batch(
                extractor,
                input_dir=Path(args.input_dir),
                output_dir=output_dir,
            )
        else:
            parser.error("請指定 --input-dir（批次）或 --input-file（單檔）")


if __name__ == "__main__":
    main()
