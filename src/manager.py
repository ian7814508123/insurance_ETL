import json
import os
from pathlib import Path
from typing import List, Dict, Any


class DefinitionManager:
    """管理名詞定義的晉升與同步。"""

    def __init__(self, definitions_dir: str):
        self.base_file = os.path.join(definitions_dir, "base.json")
        self.products_dir = os.path.join(definitions_dir, "products")

    def load_json(self, path: str) -> List[Dict[str, Any]]:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        return []

    def save_json(self, path: str, data: List[Dict[str, Any]]):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def find_promotion_candidates(self) -> Dict[str, List[Dict[str, Any]]]:
        """找出所有商品中標記為 NEW_GENERAL 的名詞。"""
        candidates = {}
        for file in Path(self.products_dir).glob("*.json"):
            defs = self.load_json(str(file))
            for d in defs:
                if d.get("classification") == "NEW_GENERAL":
                    code = d["code"]
                    if code not in candidates:
                        candidates[code] = []
                    candidates[code].append({**d, "from_file": file.name})
        return candidates

    def promote(self, code: str, chosen_index: int = 0):
        """將指定的名詞晉升至基本層。"""
        candidates = self.find_promotion_candidates()
        if code not in candidates:
            print(f"找不到名詞代號: {code}")
            return

        # 選擇一個定義作為基礎
        winner = candidates[code][chosen_index]
        base_defs = self.load_json(self.base_file)

        # 準備併入 base 的資料
        new_base_entry = {
            "type": "名詞定義",
            "description": winner.get("description", ""),
            "code": winner["code"],
            "display_name": winner["display_name"],
            "base_definition": winner["base_definition"],
            "parameter": winner.get("parameter", {}),
            "synonym_map": winner.get("synonym_map", []),
            "level": "BASE",
        }

        # 併入並儲存 base.json
        base_defs.append(new_base_entry)
        self.save_json(self.base_file, base_defs)
        print(f"已將 {code} 晉升至基本層。")

        # 更新所有商品層的標籤
        for file in Path(self.products_dir).glob("*.json"):
            p_defs = self.load_json(str(file))
            updated = False
            for d in p_defs:
                if d["code"] == code:
                    d["classification"] = "EXISTING_MATCH"
                    d["level"] = "PRODUCT"
                    updated = True
            if updated:
                self.save_json(str(file), p_defs)
                print(f"已更新商品檔案: {file.name}")


if __name__ == "__main__":
    import sys

    manager = DefinitionManager(r"c:\Users\User\Downloads\保費試算\data\definitions")

    if len(sys.argv) < 2:
        print("使用方式:")
        print(
            "  python src/manager.py list           # 列出所有待審核的 NEW_GENERAL (按頻率排序)"
        )
        print("  python src/manager.py review {CODE}  # 查看該名詞在各商品中的定義差異")
        print("  python src/manager.py promote {CODE} # 執行晉升至基本層")
    else:
        cmd = sys.argv[1]
        if cmd == "list":
            candidates = manager.find_promotion_candidates()
            # 按出現次數排序
            sorted_candidates = sorted(
                candidates.items(), key=lambda x: len(x[1]), reverse=True
            )

            print(f"\n{'Code':<35} | {'出現次數':<8} | {'建議顯示名稱'}")
            print("|:---:|:---:|:---:|")
            for code, items in sorted_candidates:
                print(f"{code:<35} | {len(items):<8} | {items[0]['display_name']}")
            print(
                f"\n提示: 使用 'python src/manager.py review [CODE]' 查看詳細內容並決定是否晉升。"
            )

        elif cmd == "review" and len(sys.argv) > 2:
            code = sys.argv[2]
            candidates = manager.find_promotion_candidates()
            if code not in candidates:
                print(f"找不到名詞代號: {code}")
            else:
                items = candidates[code]
                print(f"\n=== 審核名詞: {code} ({items[0]['display_name']}) ===")
                for i, item in enumerate(items):
                    print(f"\n來源檔案: {item['from_file']}")
                    print(f"白話描述: {item.get('description', '無')}")
                    print(f"原始定義: {item['base_definition']}")
                print(
                    f"\n執行建議: 如果定義一致且具備通用性，請執行 'python src/manager.py promote {code}'"
                )

        elif cmd == "promote" and len(sys.argv) > 2:
            manager.promote(sys.argv[2])
