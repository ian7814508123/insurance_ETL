import json
import os
import argparse
from pathlib import Path
from typing import List, Dict, Any

# 查看名詞定義 python src/manager.py --target definition list
# 查看理賠項目 python src/manager.py --target claim_item list
# 查看定義內容 python src/manager.py --target {definition | claim_item} review {CODE}
# 晉升定義內容 python src/manager.py --target {definition | claim_item} promote {CODE}


class DefinitionManager:
    """管理名詞定義與理賠項目的晉升與同步。"""

    def __init__(self, data_dir: str, target_type: str):
        self.data_dir = Path(data_dir)
        self.target_type = target_type

        # 商品萃取結果統一放置於 data/claim_items/products
        self.products_dir = self.data_dir / "claim_items" / "products"

        if target_type == "definition":
            self.base_file = self.data_dir / "definitions" / "base_definitions.json"
            self.data_key = "global_definitions"
        elif target_type == "claim_item":
            self.base_file = self.data_dir / "definitions" / "base_claim_items.json"
            self.data_key = "claim_items"
        else:
            raise ValueError(
                "不支援的 target_type。請使用 'definition' 或 'claim_item'"
            )

    def load_json(self, path: Path) -> Any:
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return None

    def save_json(self, path: Path, data: Any):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def find_promotion_candidates(self) -> Dict[str, List[Dict[str, Any]]]:
        """找出所有商品中標記為 NEW_GENERAL 的名詞或理賠項目。"""
        candidates = {}
        if not self.products_dir.exists():
            print(f"找不到商品資料目錄: {self.products_dir}")
            return candidates

        for file in self.products_dir.glob("*.json"):
            file_data = self.load_json(file)
            if not file_data or not isinstance(file_data, dict):
                continue

            # 讀取對應的陣列 (global_definitions 或 claim_items)
            items = file_data.get(self.data_key, [])
            for d in items:
                if d.get("classification") == "NEW_GENERAL":
                    code = d.get("code")
                    if not code:
                        continue
                    if code not in candidates:
                        candidates[code] = []
                    candidates[code].append({**d, "from_file": file.name})
        return candidates

    def promote(self, code: str, chosen_index: int = 0):
        """將指定的項目晉升至基本層。"""
        candidates = self.find_promotion_candidates()
        if code not in candidates:
            print(f"找不到代號: {code}")
            return

        # 選擇一個定義作為基礎
        winner = candidates[code][chosen_index]
        base_data = self.load_json(self.base_file)
        if not isinstance(base_data, list):
            base_data = []

        # 準備併入 base 的資料
        if self.target_type == "definition":
            new_base_entry = {
                "type": "名詞定義",
                "description": winner.get("description", ""),
                "code": winner.get("code"),
                "display_name": winner.get("display_name"),
                "base_definition": winner.get("base_definition"),
                "parameter": winner.get("parameter", {}),
                "synonym_map": winner.get("synonym_map", []),
                "level": "BASE",
            }
        else:
            # claim_item: 根據需求保留 logic_structure 與 parameters
            new_base_entry = {
                "type": "理賠項目定義",
                "code": winner.get("code"),
                "display_name": winner.get("display_name"),
                "level": "BASE",
                "classification": "NEW_GENERAL",
                "description": winner.get("description", ""),
                "base_definition": winner.get("base_definition", ""),
                "logic_structure": winner.get("logic_structure", {}),
                "parameters": winner.get("parameters", []),
                "synonym_map": winner.get("synonym_map", []),
            }
            if "payment_type" in winner:
                new_base_entry["payment_type"] = winner["payment_type"]

        # 併入並儲存
        # 檢查是否已存在
        for i, existing in enumerate(base_data):
            if existing.get("code") == code:
                base_data[i] = new_base_entry
                break
        else:
            base_data.append(new_base_entry)

        self.save_json(self.base_file, base_data)
        print(f"已將 {code} 晉升至基本層 ({self.base_file.name})。")

        # 更新所有商品層的標籤
        for file in self.products_dir.glob("*.json"):
            file_data = self.load_json(file)
            if not file_data or not isinstance(file_data, dict):
                continue

            items = file_data.get(self.data_key, [])
            updated = False
            for d in items:
                if d.get("code") == code:
                    d["classification"] = "EXISTING_MATCH"
                    # d["level"] = "PRODUCT" # 保持原商品的層級，只改狀態
                    updated = True

            if updated:
                self.save_json(file, file_data)
                print(f"已更新商品檔案: {file.name}")


def main():
    parser = argparse.ArgumentParser(description="管理名詞定義與理賠項目的晉升機制")
    parser.add_argument(
        "--target",
        choices=["definition", "claim_item"],
        required=True,
        help="選擇要管理的對象: definition (名詞定義) 或 claim_item (理賠項目)",
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).parent.parent / "data"),
        help="專案資料目錄 (預設為專案的 data 目錄)",
    )

    subparsers = parser.add_subparsers(dest="cmd", help="指令")

    # list command
    subparsers.add_parser("list", help="列出所有待審核的 NEW_GENERAL 項目")

    # review command
    review_parser = subparsers.add_parser(
        "review", help="查看該名詞在各商品中的定義差異"
    )
    review_parser.add_argument("code", help="要審查的代號")

    # promote command
    promote_parser = subparsers.add_parser("promote", help="執行晉升至基本層")
    promote_parser.add_argument("code", help="要晉升的代號")
    promote_parser.add_argument(
        "--index",
        type=int,
        default=0,
        help="如果有衝突，選擇哪一個檔案的定義作為基礎 (預設 0)",
    )

    args = parser.parse_args()

    if not args.cmd:
        parser.print_help()
        return

    manager = DefinitionManager(args.data_dir, args.target)

    if args.cmd == "list":
        candidates = manager.find_promotion_candidates()
        if not candidates:
            print(f"目前沒有任何標記為 NEW_GENERAL 的 {args.target}。")
            return

        sorted_candidates = sorted(
            candidates.items(), key=lambda x: len(x[1]), reverse=True
        )
        print(f"\n[{args.target.upper()}] 待晉升清單:")
        print(f"{'Code':<35} | {'出現次數':<8} | {'建議顯示名稱'}")
        print("-" * 65)
        for code, items in sorted_candidates:
            display_name = items[0].get("display_name", "N/A")
            print(f"{code:<35} | {len(items):<8} | {display_name}")
        print(
            f"\n提示: 使用 'python src/manager.py --target {args.target} review [CODE]' 查看詳細內容並決定是否晉升。"
        )

    elif args.cmd == "review":
        candidates = manager.find_promotion_candidates()
        if args.code not in candidates:
            print(f"找不到代號: {args.code}")
        else:
            items = candidates[args.code]
            display_name = items[0].get("display_name", "N/A")
            print(
                f"\n=== 審核 [{args.target.upper()}] : {args.code} ({display_name}) ==="
            )
            for i, item in enumerate(items):
                print(f"\n[{i}] 來源檔案: {item['from_file']}")
                print(f"白話描述: {item.get('description', '無')}")
                print(f"原始定義: {item.get('base_definition', '無')}")
            print(
                f"\n執行建議: 如果定義一致且具備通用性，請執行 'python src/manager.py --target {args.target} promote {args.code}'"
            )

    elif args.cmd == "promote":
        manager.promote(args.code, args.index)


if __name__ == "__main__":
    main()
