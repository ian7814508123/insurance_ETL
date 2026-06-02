"""
1. 用途說明:
   名詞定義與理賠項目變數對齊靜態分析工具。本腳本載入 5 大類名詞定義子詞庫的所有 Code 代號，並掃描 5 大類理賠項目子詞庫中的 `parameters` 變數名稱，進行雙向一致性檢查，主動捕捉「引用了不存在於名詞定義庫中」的斷鍵變數，同時檢測理賠項目與名詞定義是否存在 Code 命名重疊，以防對齊混淆。

2. 如何使用:
   - 執行一致性靜態分析：
     python scratch/analyze_keys.py
"""

import json
import re
from pathlib import Path

def analyze_keys():
    definitions_dir = Path("data/definitions")
    
    # 1. 讀取所有的名詞定義 code 及其 display_name
    def_files = [
        "base_definitions_general.json",
        "base_definitions_health.json",
        "base_definitions_injury.json",
        "base_definitions_life_annuity.json",
        "base_definition_investment.json"
    ]
    
    all_def_codes = set()
    def_code_to_name = {}
    for fname in def_files:
        path = definitions_dir / fname
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            code = item.get("code")
                            if code:
                                all_def_codes.add(code)
                                def_code_to_name[code] = item.get("display_name", "")
            except Exception as e:
                print(f"無法讀取名詞定義 {fname}: {e}")

    print(f"載入的名詞定義總數: {len(all_def_codes)}")
    
    # 2. 讀取所有的理賠項目，並分析其 parameters 與 logic_structure 中的變數
    claim_files = [
        "base_claim_items_general.json",
        "base_claim_items_health.json",
        "base_claim_items_injury.json",
        "base_claim_items_life_annuity.json",
        "base_claim_items_investment.json"
    ]
    
    print("\n--- 理賠項目 parameters 變數與名詞定義對齊分析 ---")
    broken_params = []
    
    for fname in claim_files:
        path = definitions_dir / fname
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        for item in data:
                            code = item.get("code")
                            display_name = item.get("display_name")
                            
                            # 檢查 parameters
                            parameters = item.get("parameters", [])
                            for param in parameters:
                                param_name = param.get("param_name")
                                # 如果 parameter 名稱不在名詞定義中，代表出現了「引用的變數找不到名詞定義」
                                if param_name and param_name not in all_def_codes:
                                    # 排除常見的系統常數或輸入，如 AMOUNT 等
                                    broken_params.append({
                                        "claim_file": fname,
                                        "claim_code": code,
                                        "claim_name": display_name,
                                        "broken_param": param_name,
                                        "param_display": param.get("display_name", "")
                                    })
            except Exception as e:
                print(f"無法讀取理賠項目 {fname}: {e}")

    if not broken_params:
        print("所有理賠項目中的 parameters 變數都已與名詞定義 code 完美對齊！")
    else:
        print(f"共發現 {len(broken_params)} 個未在名詞定義庫中的 parameters 變數：\n")
        # 按理賠項目分組列出
        grouped = {}
        for p in broken_params:
            key = (p["claim_code"], p["claim_name"], p["claim_file"])
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(p)
            
        for key, items in grouped.items():
            print(f"理賠項目: {key[0]} ({key[1]}) -> 檔案: {key[2]}")
            for item in items:
                print(f"   未對齊變數: {item['broken_param']} ({item['param_display']})")
            print()

    # 3. 檢查理賠項目本身的 code 是否也在名詞定義中 (雙向去重或代號重複檢測)
    overlap = all_def_codes.intersection({item.get("code") for fname in claim_files for item in json.load(open(definitions_dir / fname, "r", encoding="utf-8")) if (definitions_dir / fname).exists()})
    if overlap:
        print("\n[警告] 發現理賠項目的 code 與名詞定義的 code 重複 (這可能導致 Pipeline 對齊混淆)：")
        for c in overlap:
            print(f"  重複的 Code: {c}")
    else:
        print("\n理賠項目與名詞定義的 Code 沒有任何重複，命名空間完全隔離。")

if __name__ == "__main__":
    analyze_keys()
