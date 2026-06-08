"""
1. 用途說明:
   名詞定義與理賠項目管理中心 (Definition & Claim Item Manager)。本腳本提供控制台命令列 (CLI) 介面，供管理人員對商品解析出的 NEW_GENERAL 候選名詞與理賠項目進行晉升 (promote) 審核，寫入對應的 5 大類子詞庫，並執行跨詞庫防禦性去重與聯動更新商品狀態。同時提供既存項目的重新分類 (reclassify) 與刪除功能。

2. 如何使用:
   - 列出所有待晉升的名詞或理賠項目候選代號：
     python src/manager.py --target definition list
     python src/manager.py --target claim_item list
   - 審查特定代號在各商品中的定義差異：
     python src/manager.py --target definition review [CODE]
   - 晉升特定代號至基本層子詞庫（附帶人機互動審查與去重）：
     python src/manager.py --target claim_item promote [CODE]
   - 調整既存項目的分類歸屬或徹底刪除項目（自動執行防禦性去重）：
     python src/manager.py --target definition reclassify [CODE]
   - 互動審核模式（由人工確認分類）:
     python src/manager.py --target claim_item batch-promote data/claim_items/claim_item_clusters.json
   - 自動晉升模式:
     python src/manager.py --target claim_item batch-promote data/claim_items/claim_item_clusters.json --auto

"""

import json
import os
import sys
import argparse
from pathlib import Path
from typing import List, Dict, Any

# 確保在 Windows 等環境下 print UTF-8 字元時不會因為編碼問題崩潰
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


class DefinitionManager:
    """管理名詞定義與理賠項目的晉升與同步。"""

    def __init__(self, data_dir: str, target_type: str):
        self.data_dir = Path(data_dir)
        self.target_type = target_type

        # 商品萃取結果統一放置於 data/claim_items/products
        self.products_dir = self.data_dir / "claim_items" / "products"

        self.category_files = {
            "general": "base_definitions_general.json",
            "health": "base_definitions_health.json",
            "injury": "base_definitions_injury.json",
            "investment": "base_definition_investment.json",
            "life_annuity": "base_definitions_life_annuity.json",
        }

        self.claim_category_files = {
            "general": "base_claim_items_general.json",
            "health": "base_claim_items_health.json",
            "injury": "base_claim_items_injury.json",
            "investment": "base_claim_items_investment.json",
            "life_annuity": "base_claim_items_life_annuity.json",
        }

        if target_type == "definition":
            self.data_key = "global_definitions"
            self.base_files = {
                cat: self.data_dir / "definitions" / fname
                for cat, fname in self.category_files.items()
            }
        elif target_type == "claim_item":
            self.data_key = "claim_items"
            self.base_files = {
                cat: self.data_dir / "definitions" / fname
                for cat, fname in self.claim_category_files.items()
            }
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

    def load_base_data(self) -> List[Dict[str, Any]]:
        """載入基本層數據，自動載入並合併所有子詞庫"""
        merged = []
        for cat, path in self.base_files.items():
            if path.exists():
                data = self.load_json(path)
                if isinstance(data, list):
                    merged.extend(data)
        return merged

    def get_category_for_definition(self, item: Dict[str, Any]) -> str:
        """根據規則與語義判定名詞定義或理賠項目應歸入的子類別"""
        try:
            src_dir = Path(__file__).parent
            if str(src_dir) not in sys.path:
                sys.path.append(str(src_dir))

            if self.target_type == "claim_item":
                from classify_claim_items import rule_classify_claim_item

                cat = rule_classify_claim_item(item)
                if not cat:
                    # 如果規則引擎無法判定，使用 LLM 判定
                    try:
                        from classify_claim_items import llm_classify_claim_item
                        from google import genai
                        import config

                        client = genai.Client(api_key=config.GEMINI_API_KEY)
                        cat = llm_classify_claim_item(client, item)
                    except Exception:
                        cat = "general"
            else:
                from classify_definitions import rule_classify

                cat = rule_classify(item)

            if cat in self.base_files:
                return cat
        except Exception:
            pass
        return "general"

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
            if not isinstance(items, list):
                continue
            for d in items:
                if not isinstance(d, dict):
                    continue
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
            files_dict = self.category_files
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
            files_dict = self.claim_category_files

        # 進行分類與儲存
        cat = self.get_category_for_definition(new_base_entry)

        # 顯示詳細結構
        print(f"\n=== 審核 [{self.target_type.upper()}] 晉升候選項目 ===")
        print(f"  代碼 (Code)      : {new_base_entry['code']}")
        print(f"  顯示名稱 (Name)   : {new_base_entry['display_name']}")
        print(f"  白話描述 (Desc)   : {new_base_entry.get('description', '無')}")

        if self.target_type == "claim_item":
            print(f"  理賠類型 (Type)   : {new_base_entry.get('payment_type', '無')}")
            print(
                f"  邏輯結構 (Logic)  : {json.dumps(new_base_entry.get('logic_structure', {}), ensure_ascii=False, indent=4)}"
            )
            print(
                f"  參數清單 (Params) : {json.dumps(new_base_entry.get('parameters', []), ensure_ascii=False, indent=4)}"
            )
        else:
            print(
                f"  原始定義 (Def)    : {new_base_entry.get('base_definition', '無')}"
            )

        print(f"\n[自動分析結果] 系統推薦分類: 【{cat}】")
        print("  請選擇操作:")
        print(f"    [1] 同意系統推薦 (將其寫入 {files_dict[cat]})")
        print("    [2] 手動變更分類 (由人員決定)")
        print("    [3] 取消晉升")

        choice = "1"
        if sys.stdin.isatty():
            try:
                choice = input("  請輸入選擇 [1-3] (預設 1): ").strip()
            except Exception:
                choice = "1"
        else:
            choice = "1"

        if choice == "3":
            print("已取消晉升。")
            return
        elif choice == "2":
            print("\n  可選的分類子詞庫:")
            print("    1. general      (通用底層契約關係)")
            print("    2. health       (健康醫療/住院手術/長照)")
            print("    3. injury       (傷害意外)")
            print("    4. investment   (投資型保險)")
            print("    5. life_annuity (壽險與年金險)")
            cat_choices = {
                "1": "general",
                "2": "health",
                "3": "injury",
                "4": "investment",
                "5": "life_annuity",
            }
            sub_choice = "1"
            if sys.stdin.isatty():
                try:
                    sub_choice = input("  請輸入分類編號 [1-5]: ").strip()
                except Exception:
                    sub_choice = "1"
            cat = cat_choices.get(sub_choice, "general")
            print(f"  -> 手動選擇分類: 【{cat}】")
        else:
            print(f"  -> 使用推薦分類: 【{cat}】")

        target_file = self.base_files[cat]

        cat_data = self.load_json(target_file)
        if not isinstance(cat_data, list):
            cat_data = []

        # 檢查是否已存在於該子詞庫中
        for i, existing in enumerate(cat_data):
            if existing.get("code") == code:
                cat_data[i] = new_base_entry
                break
        else:
            cat_data.append(new_base_entry)

        # 防禦性清理：將同 code 的舊項目從其他子詞庫中移除，避免重複
        for other_cat, other_path in self.base_files.items():
            if other_cat == cat:
                continue
            if other_path.exists():
                other_data = self.load_json(other_path)
                if isinstance(other_data, list):
                    filtered = [d for d in other_data if d.get("code") != code]
                    if len(filtered) != len(other_data):
                        self.save_json(other_path, filtered)

        self.save_json(target_file, cat_data)
        print(
            f"已將 {code} 晉升至基本層子詞庫 {cat} ({target_file.name}) 並執行跨詞庫去重。"
        )

        # 更新所有商品層的標籤
        for file in self.products_dir.glob("*.json"):
            file_data = self.load_json(file)
            if not file_data or not isinstance(file_data, dict):
                continue

            items = file_data.get(self.data_key, [])
            if not isinstance(items, list):
                continue
            updated = False
            for d in items:
                if not isinstance(d, dict):
                    continue
                if d.get("code") == code:
                    d["classification"] = "EXISTING_MATCH"
                    # d["level"] = "PRODUCT" # 保持原商品的層級，只改狀態
                    updated = True

            if updated:
                self.save_json(file, file_data)
                print(f"已更新商品檔案: {file.name}")

    def reclassify(self, code: str):
        """重新調整既存項目的分類歸屬與防禦性去重。"""
        # 1. 尋找既存項目在哪個子詞庫中
        found_cat = None
        found_item = None
        for cat, path in self.base_files.items():
            if path.exists():
                data = self.load_json(path)
                if isinstance(data, list):
                    for item in data:
                        if item.get("code") == code:
                            found_cat = cat
                            found_item = item
                            break
            if found_cat:
                break

        if not found_cat:
            print(f"錯誤：在所有既存子詞庫中找不到代號: {code}")
            return

        files_dict = (
            self.category_files
            if self.target_type == "definition"
            else self.claim_category_files
        )

        print(f"\n=== 重新調整分類 : {code} ({found_item.get('display_name', '')}) ===")
        print(f"當前分類子詞庫: 【{found_cat}】 ({files_dict[found_cat]})")
        print(f"白話描述: {found_item.get('description', '')}")

        if self.target_type == "claim_item":
            print(
                f"邏輯結構: {json.dumps(found_item.get('logic_structure', {}), ensure_ascii=False, indent=2)}"
            )
            print(
                f"參數清單: {json.dumps(found_item.get('parameters', []), ensure_ascii=False, indent=2)}"
            )
        else:
            print(f"原始定義: {found_item.get('base_definition', '')[:120]}...")

        print("\n請選擇操作:")
        print("  [1] 保持不變並退出")
        print("  [2] 調整至其他子詞庫分類")
        print("  [3] 刪除此項目")

        choice = input("請輸入選擇 [1-3]: ").strip()

        if choice == "1":
            print("保持不變，已退出。")
            return

        elif choice == "3":
            confirm = (
                input(f"確定要徹底刪除 {code} 嗎？此操作不可逆 [y/N]: ").strip().lower()
            )
            if confirm == "y":
                # 從所有子詞庫中移除
                for cat, path in self.base_files.items():
                    if path.exists():
                        data = self.load_json(path)
                        if isinstance(data, list):
                            filtered = [d for d in data if d.get("code") != code]
                            if len(filtered) != len(data):
                                self.save_json(path, filtered)
                print(f"已成功從詞庫中刪除 {code}。")
            else:
                print("已取消刪除。")
            return

        elif choice == "2":
            print("\n可選的分類子詞庫:")
            print("  1. general      (通用底層契約關係)")
            print("  2. health       (健康醫療/住院手術/長照)")
            print("  3. injury       (傷害意外)")
            print("  4. investment   (投資型保險)")
            print("  5. life_annuity (壽險與年金險)")
            cat_choices = {
                "1": "general",
                "2": "health",
                "3": "injury",
                "4": "investment",
                "5": "life_annuity",
            }
            sub_choice = input("請輸入目標分類編號 [1-5]: ").strip()
            new_cat = cat_choices.get(sub_choice)
            if not new_cat:
                print("輸入錯誤，已取消操作。")
                return

            if new_cat == found_cat:
                print(f"新分類與當前分類一致，無須調整。")
                return

            # 開始搬移與防禦性去重
            # a. 載入目標詞庫
            target_path = self.base_files[new_cat]
            target_data = self.load_json(target_path)
            if not isinstance(target_data, list):
                target_data = []

            # 將項目加入目標詞庫 (確保不重複加入)
            if not any(d.get("code") == code for d in target_data):
                target_data.append(found_item)
                self.save_json(target_path, target_data)

            # b. 從原詞庫以及「所有其他詞庫」中移除，徹底杜絕重複
            for cat, path in self.base_files.items():
                if cat == new_cat:
                    continue
                if path.exists():
                    data = self.load_json(path)
                    if isinstance(data, list):
                        filtered = [d for d in data if d.get("code") != code]
                        if len(filtered) != len(data):
                            self.save_json(path, filtered)

            print(
                f"\n已成功將 {code} 從 【{found_cat}】 調整至 【{new_cat}】 並自動執行跨詞庫去重清理！"
            )

    def batch_promote(self, clusters_file: str, auto_mode: bool = False):
        """批次讀取聚類結果並晉升。"""
        clusters_path = Path(clusters_file)
        if not clusters_path.exists():
            print(f"錯誤：找不到聚類結果檔案，路徑為: {clusters_path}")
            return

        clusters = self.load_json(clusters_path)
        if not isinstance(clusters, list):
            print("錯誤：聚類結果格式不正確，應為陣列（list）。")
            return

        print(f"\n載入聚類結果，共有 {len(clusters)} 個群組。")

        files_dict = (
            self.category_files
            if self.target_type == "definition"
            else self.claim_category_files
        )

        for c_idx, cluster in enumerate(clusters):
            cluster_id = cluster.get("cluster_id", f"CLUSTER_{c_idx + 1}")
            rep = cluster.get("representative", {})
            members = cluster.get("members", [])

            if not rep or not isinstance(rep, dict):
                print(f"[{cluster_id}] 警告：代表項目格式無效，跳過。")
                continue

            code = rep.get("code")
            display_name = rep.get("display_name")
            if not code or not display_name:
                print(f"[{cluster_id}] 警告：代表項目缺少 code 或 display_name，跳過。")
                continue

            print(f"\n==================================================")
            print(f"  群組 ID: {cluster_id}")
            print(f"  代表代碼 (Code)      : {code}")
            print(f"  建議顯示名稱 (Name)   : {display_name}")
            print(f"  白話描述 (Desc)   : {rep.get('description', '無')}")
            print(f"  包含成員商品數量 : {len(members)} 個")

            if self.target_type == "claim_item":
                print(f"  理賠類型 (Type)   : {rep.get('payment_type', '無')}")
                print(
                    f"  邏輯結構 (Logic)  : {json.dumps(rep.get('logic_structure', {}), ensure_ascii=False, indent=2)}"
                )
                print(
                    f"  參數清單 (Params) : {json.dumps(rep.get('parameters', []), ensure_ascii=False, indent=2)}"
                )
            else:
                print(f"  原始定義 (Def)    : {rep.get('base_definition', '無')}")

            # 推薦分類
            cat = self.get_category_for_definition(rep)
            print(f"  系統推薦分類       : 【{cat}】")

            # 互動選擇
            if auto_mode:
                print(f"  [Auto Mode] 自動同意推薦分類: 【{cat}】")
                choice = "1"
            else:
                print(f"\n  請選擇操作:")
                print(
                    f"    [1] 同意系統推薦並晉升整個群組 (將其寫入 {files_dict[cat]})"
                )
                print("    [2] 手動變更分類並晉升整個群組")
                print("    [3] 跳過此群組")

                choice = "1"
                if sys.stdin.isatty():
                    try:
                        choice = input("  請輸入選擇 [1-3] (預設 1): ").strip()
                    except Exception:
                        choice = "1"
                else:
                    choice = "1"

            if choice == "3":
                print(f"已跳過群組 {cluster_id}。")
                continue
            elif choice == "2":
                print("\n  可選的分類子詞庫:")
                print("    1. general      (通用底層契約關係)")
                print("    2. health       (健康醫療/住院手術/長照)")
                print("    3. injury       (傷害意外)")
                print("    4. investment   (投資型保險)")
                print("    5. life_annuity (壽險與年金險)")
                cat_choices = {
                    "1": "general",
                    "2": "health",
                    "3": "injury",
                    "4": "investment",
                    "5": "life_annuity",
                }
                sub_choice = "1"
                if sys.stdin.isatty():
                    try:
                        sub_choice = input("  請輸入分類編號 [1-5]: ").strip()
                    except Exception:
                        sub_choice = "1"
                cat = cat_choices.get(sub_choice, "general")
                print(f"  -> 手動選擇分類: 【{cat}】")
            else:
                if not auto_mode:
                    print(f"  -> 使用推薦分類: 【{cat}】")

            # 準備併入 base 的資料
            if self.target_type == "definition":
                new_base_entry = {
                    "type": "名詞定義",
                    "description": rep.get("description", ""),
                    "code": code,
                    "display_name": display_name,
                    "base_definition": rep.get("base_definition"),
                    "parameter": rep.get("parameter", {}),
                    "synonym_map": rep.get("synonym_map", []),
                    "level": "BASE",
                }
            else:
                new_base_entry = {
                    "type": "理賠項目定義",
                    "code": code,
                    "display_name": display_name,
                    "level": "BASE",
                    "classification": "EXISTING_MATCH",
                    "description": rep.get("description", ""),
                    "base_definition": rep.get("base_definition", ""),
                    "logic_structure": rep.get("logic_structure", {}),
                    "parameters": rep.get("parameters", []),
                    "synonym_map": rep.get("synonym_map", []),
                }
                if "payment_type" in rep:
                    new_base_entry["payment_type"] = rep["payment_type"]

            target_file = self.base_files[cat]
            cat_data = self.load_json(target_file)
            if not isinstance(cat_data, list):
                cat_data = []

            # 檢查並更新
            for i, existing in enumerate(cat_data):
                if existing.get("code") == code:
                    cat_data[i] = new_base_entry
                    break
            else:
                cat_data.append(new_base_entry)

            # 跨詞庫防禦性清理
            for other_cat, other_path in self.base_files.items():
                if other_cat == cat:
                    continue
                if other_path.exists():
                    other_data = self.load_json(other_path)
                    if isinstance(other_data, list):
                        filtered = [d for d in other_data if d.get("code") != code]
                        if len(filtered) != len(other_data):
                            self.save_json(other_path, filtered)

            self.save_json(target_file, cat_data)
            print(
                f"  -> 已將代表項目 {code} 寫入基本層子詞庫 {cat} ({target_file.name})。"
            )

            # 聯動更新所有成員商品的 JSON
            updated_files_count = 0

            # 成員分類查找 map
            members_by_product = {}
            for member in members:
                p_code = member.get("origin_product")
                if not p_code:
                    continue
                if p_code not in members_by_product:
                    members_by_product[p_code] = []
                members_by_product[p_code].append(member)

            for p_code, p_members in members_by_product.items():
                file_path = self.products_dir / f"{p_code}.json"
                if not file_path.exists():
                    continue

                file_data = self.load_json(file_path)
                if not file_data or not isinstance(file_data, dict):
                    continue

                items = file_data.get(self.data_key, [])
                if not isinstance(items, list):
                    continue

                updated = False
                for d in items:
                    if not isinstance(d, dict):
                        continue

                    is_match = False
                    for m in p_members:
                        m_code = m.get("code")
                        m_display = m.get("display_name", "").strip()
                        m_desc = m.get("description", "").strip()

                        if m_code and d.get("code") == m_code:
                            is_match = True
                            break

                        d_display = d.get("display_name", "").strip()
                        if m_display and d_display == m_display:
                            d_desc = (
                                d.get("description", "").strip()
                                or d.get("base_definition", "").strip()
                            )
                            m_desc_full = m_desc or m.get("base_definition", "").strip()
                            if d_desc == m_desc_full or not d_desc or not m_desc_full:
                                is_match = True
                                break

                    if is_match:
                        d["code"] = code
                        d["classification"] = "EXISTING_MATCH"
                        updated = True

                if updated:
                    self.save_json(file_path, file_data)
                    updated_files_count += 1

            print(
                f"  -> 已成功聯動更新與對齊 {updated_files_count} 個商品的 JSON 檔案。"
            )

        print(f"\n群組批次晉升完成！")


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

    # reclassify command
    reclassify_parser = subparsers.add_parser(
        "reclassify", help="重新調整既存名詞定義的分類歸屬與防禦性去重"
    )
    reclassify_parser.add_argument("code", help="要調整分類的既存名詞代號")

    # batch-promote command
    batch_promote_parser = subparsers.add_parser(
        "batch-promote", help="批次讀取聚類結果並進行晉升"
    )
    batch_promote_parser.add_argument("clusters_file", help="聚類結果 JSON 檔案路徑")
    batch_promote_parser.add_argument(
        "--auto",
        action="store_true",
        help="自動同意系統推薦險種分類並晉升，無需逐筆確認",
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

    elif args.cmd == "reclassify":
        manager.reclassify(args.code)

    elif args.cmd == "batch-promote":
        manager.batch_promote(args.clusters_file, args.auto)


if __name__ == "__main__":
    main()
