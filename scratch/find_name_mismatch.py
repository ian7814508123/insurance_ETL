"""
1. 用途說明:
   同義中文概念但 Code 命名不對齊靜態掃描工具。本腳本建立名詞定義的 `display_name` ➜ `code` 對照表，並掃描理賠項目 parameters 中與名詞定義中文名稱相同但 Code 不同的項目（例如：理賠參數中的 `INSURANCE_AMOUNT` vs 名詞定義中的 `SUM_INSURED`），列出這些命名空間不對齊的潛在斷鍵項目，以便管理人員進行對齊重構。

2. 如何使用:
   - 執行不對齊代號掃描：
     python scratch/find_name_mismatch.py
"""

import json
from pathlib import Path

def find_mismatches():
    definitions_dir = Path("data/definitions")
    
    # 1. 載入所有名詞定義，建立 display_name -> code 的對照表
    def_files = [
        "base_definitions_general.json",
        "base_definitions_health.json",
        "base_definitions_injury.json",
        "base_definitions_life_annuity.json",
        "base_definition_investment.json"
    ]
    
    name_to_def_codes = {}
    for fname in def_files:
        path = definitions_dir / fname
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        code = item.get("code")
                        name = item.get("display_name")
                        if name and code:
                            if name not in name_to_def_codes:
                                name_to_def_codes[name] = set()
                            name_to_def_codes[name].add(code)

    # 2. 載入所有理賠項目，比對其 parameters 的 param_name 與名詞定義的 code
    claim_files = [
        "base_claim_items_general.json",
        "base_claim_items_health.json",
        "base_claim_items_injury.json",
        "base_claim_items_life_annuity.json",
        "base_claim_items_investment.json"
    ]
    
    mismatches = []
    
    for fname in claim_files:
        path = definitions_dir / fname
        if path.exists():
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    for item in data:
                        claim_code = item.get("code")
                        claim_name = item.get("display_name")
                        
                        parameters = item.get("parameters", [])
                        for param in parameters:
                            p_name = param.get("param_name")
                            p_desc = param.get("param_description", "")
                            # 試圖從 param_description 或 display_name 來找對照的名詞名稱
                            # 我們可以使用 display_name 或是描述中的關鍵中文，這裡用 param_description 的前幾個字或精確匹配
                            # 為了精確，我們比對 param_description 與名詞定義的 display_name
                            
                            # 找出理賠項目 parameters 中與名詞定義 display_name 相同但 code 不同的情況
                            # 比如，param 的描述是「保險金額」，而名詞定義中有一個叫「保險金額」的名詞，但兩者 code 不同的情況
                            for def_name, def_codes in name_to_def_codes.items():
                                # 檢查描述中是否包含該名詞，或者 param 描述/顯示名稱就是該名詞
                                if def_name == p_desc or def_name in p_desc:
                                    for d_code in def_codes:
                                        if p_name != d_code and p_name != "INSURANCE_AMOUNT" and d_code == "SUM_INSURED":
                                            # 特別關注保險金額
                                            pass
                                        if def_name == "保險金額" and p_name != d_code:
                                            mismatches.append({
                                                "claim_file": fname,
                                                "claim_code": claim_code,
                                                "claim_name": claim_name,
                                                "param_name": p_name,
                                                "param_desc": p_desc,
                                                "matched_def_name": def_name,
                                                "matched_def_code": d_code
                                            })
                                        elif p_name != d_code and def_name in ["保險金額", "被保險人", "要保人", "受益人", "保險費", "實際住院日數", "意外傷害事故"]:
                                            # 對於常見核心概念
                                            mismatches.append({
                                                "claim_file": fname,
                                                "claim_code": claim_code,
                                                "claim_name": claim_name,
                                                "param_name": p_name,
                                                "param_desc": p_desc,
                                                "matched_def_name": def_name,
                                                "matched_def_code": d_code
                                            })

    print("\n=== 掃描到相同中文概念但 Code 不一致的斷鍵項目 ===")
    seen = set()
    for m in mismatches:
        key = (m["claim_code"], m["param_name"], m["matched_def_code"])
        if key not in seen:
            seen.add(key)
            print(f"理賠項目: {m['claim_code']} ({m['claim_name']})")
            print(f"  理賠參數: {m['param_name']} (說明: {m['param_desc']})")
            print(f"  名詞對應: {m['matched_def_code']} (名稱: {m['matched_def_name']})")
            print(f"  來源檔案: {m['claim_file']}")
            print("-" * 50)

if __name__ == "__main__":
    find_mismatches()
