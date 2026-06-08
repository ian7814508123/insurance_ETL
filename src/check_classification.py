"""
1. 用途說明:
   分類正確性抽樣檢查工具 (Classification Sample Checker)。
   本腳本為詞庫維護階段開發的語義審查工具，隨機從已分類的 5 大類子詞庫中抽選 10% 的項目（最少 5 個，最多 30 個），
   調用 Gemini API 重新判定其歸屬險種，輸出「分類一致率報告」與「歧異案例分析」，提供 manager.py 重新分類 (reclassify) 與跨庫去重的覆核指引。
   支援名詞定義 (definition) 與理賠項目 (claim_item) 雙對象。

2. 如何使用:
   - 執行名詞分類隨機抽樣與語義覆核：
     python src/check_classification.py --target definition
   - 執行理賠項目分類隨機抽樣與語義覆核：
     python src/check_classification.py --target claim_item
"""

import json
import os
import random
import sys
import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple
from google import genai
from google.genai import types

# 確保在 Windows 環境下輸出 UTF-8 不崩潰
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 加入專案根目錄至 sys.path
root_path = Path(__file__).parent.parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

import config

# 定義子詞庫的檔名與類別對照 (名詞定義)
DEF_CATEGORY_FILES = {
    "general": "base_definitions_general.json",
    "health": "base_definitions_health.json",
    "injury": "base_definitions_injury.json",
    "investment": "base_definition_investment.json",
    "life_annuity": "base_definitions_life_annuity.json",
}

# 定義子詞庫的檔名與類別對照 (理賠給付項目)
CLAIM_CATEGORY_FILES = {
    "general": "base_claim_items_general.json",
    "health": "base_claim_items_health.json",
    "injury": "base_claim_items_injury.json",
    "investment": "base_claim_items_investment.json",
    "life_annuity": "base_claim_items_life_annuity.json",
}


def llm_classify(client: genai.Client, item: Dict[str, Any], target_type: str) -> str:
    """使用 Gemini API 對項目進行精準語意分類。"""
    schema = {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "enum": ["general", "health", "injury", "investment", "life_annuity"],
                "description": "分類類別",
            },
            "reason": {"type": "STRING", "description": "分類的白話理由"},
        },
        "required": ["category", "reason"],
    }

    if target_type == "claim_item":
        system_instruction = (
            "你是一個保險領域的中文理賠給付項目分類專家。請根據給定的理賠給付條款與公式邏輯描述，將其精準歸入以下 5 個險種類別之一：\n"
            "1. general (通用型)：不論險種，所有保單皆可能出現的通用或法律給付（如身故保險金、完全失能保險金、解約金、保險費退還）。\n"
            "2. health (健康保險型)：圍繞疾病、醫療行為、住院、手術、健保、癌症、特定傷病、長期照顧、生理/認知功能障礙等理賠給付。\n"
            "3. injury (傷害意外保險型)：強調因「外來、突發、非疾病」的意外傷害事故所導致的傷害、骨折、意外身故或意外住院等給付。\n"
            "4. investment (投資型保險型)：涉及金融市場、專設帳戶配置、資產評價等具備投資性質的理賠或給付項目。\n"
            "5. life_annuity (壽險與年金保險型)：圍繞生存與壽險返還、生存保險金、滿期保險金、定期生存年金、保證年金給付等項目。\n"
            "請特別注意：如果給付項目極為通用，請優先歸入 general；如具有明確的專屬險種特性，則歸入對應險種類別。"
        )
        input_data = (
            f"代碼: {item.get('code')}\n"
            f"顯示名稱: {item.get('display_name')}\n"
            f"白話描述: {item.get('description')}\n"
            f"給付類型: {item.get('payment_type')}\n"
            f"邏輯公式: {json.dumps(item.get('logic_structure', {}), ensure_ascii=False)}\n"
            f"原始條款給付原文: {item.get('base_definition')}\n"
        )
    else:
        system_instruction = (
            "你是一個保險領域的中文名詞分類專家。請根據給定的保險名詞定義，將其精準歸入以下 5 個類別之一：\n"
            "1. general (通用型)：所有險種皆會出現的底層法律關係、基本權利義務或共通名詞（如要保人、被保險人、受益人、復效、寬限期、保險費等）。\n"
            "2. health (健康保險型)：圍繞疾病、醫療行為、住院、手術、健保、癌症、特定傷病、長期照顧、生理/認知功能障礙等。\n"
            "3. injury (傷害意外險型)：強調遭受「外來、突發、非疾病」的意外傷害事故，致身體蒙受傷害、骨折、失能或死亡，或與職業等級相關之名詞。\n"
            "4. investment (投資型保險型)：充滿金融市場、證券投資、帳戶配置、單位淨值、分離帳戶、資產評價等名詞。\n"
            "5. life_annuity (壽險與年金保險型)：圍繞生存或身故生命保險事故，或資金返還（如保單價值準備金、解約金、宣告/預定利率、年金給付等）。\n"
            "請特別注意區分：如果一個名詞極為通用（如被保險人），請優先歸入 general；如具有明確的專屬險種特性，則歸入對應險種類別。"
        )
        input_data = (
            f"代碼: {item.get('code')}\n"
            f"顯示名稱: {item.get('display_name')}\n"
            f"白話描述: {item.get('description')}\n"
            f"原始條款定義: {item.get('base_definition')}\n"
        )

    try:
        response = client.models.generate_content(
            model=config.DEFAULT_MODEL,
            contents=[input_data],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                system_instruction=system_instruction,
                temperature=0.1,
            ),
        )
        res_data = json.loads(response.text)
        return res_data.get("category", "general")
    except Exception as e:
        print(f"LLM 分類出錯 ({item.get('code')}): {e}，預設歸為 general。")
        return "general"


def load_classified_items(definitions_dir: Path, target_type: str) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    """載入所有已分類項目，並建立 code ➜ category 的對照。"""
    files_dict = CLAIM_CATEGORY_FILES if target_type == "claim_item" else DEF_CATEGORY_FILES
    all_items = []
    item_to_cat = {}

    for cat, file_name in files_dict.items():
        file_path = definitions_dir / file_name
        if not file_path.exists() or file_path.stat().st_size == 0:
            continue

        with open(file_path, "r", encoding="utf-8") as f:
            try:
                items = json.load(f)
                if isinstance(items, list):
                    for item in items:
                        if not isinstance(item, dict):
                            continue
                        code = item.get("code")
                        if not code:
                            continue
                        all_items.append(item)
                        item_to_cat[code] = cat
            except Exception as e:
                print(f"讀取 {file_name} 失敗: {e}")
                
    return all_items, item_to_cat


def main():
    parser = argparse.ArgumentParser(description="語義抽樣分類正確性檢查工具")
    parser.add_argument(
        "--target",
        choices=["definition", "claim_item"],
        default="definition",
        help="檢查對象: definition (名詞定義) 或 claim_item (理賠給付項目)"
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).parent.parent / "data"),
        help="資料目錄"
    )
    
    args = parser.parse_args()
    
    data_dir = Path(args.data_dir)
    definitions_dir = data_dir / "definitions"
    
    # 1. 載入資料
    all_classified_items, item_to_current_cat = load_classified_items(definitions_dir, args.target)

    if not all_classified_items:
        print(
            f"錯誤：未找到任何已分類的 {args.target}。請先確認詞庫初始化狀態。"
        )
        sys.exit(1)

    # 抽樣比例為 10%，最少抽 5 個，最多抽 30 個，避免過度耗費 token
    total_count = len(all_classified_items)
    sample_size = max(5, min(30, int(total_count * 0.10)))

    target_name = "理賠項目" if args.target == "claim_item" else "名詞定義"
    print(
        f"目前共有 {total_count} 個已分類{target_name}。隨機抽選 {sample_size} 個項目進行語意歸類比對..."
    )
    
    samples = random.sample(all_classified_items, sample_size)

    # 初始化 Gemini Client
    client = genai.Client(api_key=config.GEMINI_API_KEY)

    matches = 0
    discrepancies: List[Dict[str, Any]] = []

    print("\n--- 開始抽樣比對 ---")
    for i, item in enumerate(samples):
        code = item.get("code")
        display_name = item.get("display_name")
        current_cat = item_to_current_cat[code]

        # 呼叫 LLM 重新判定
        print(f"[{i+1}/{sample_size}] 重新審核: {code} ({display_name})...")
        llm_cat = llm_classify(client, item, args.target)

        if current_cat == llm_cat:
            matches += 1
            print(f"  ✓ 一致：{current_cat}")
        else:
            discrepancies.append(
                {
                    "code": code,
                    "display_name": display_name,
                    "current": current_cat,
                    "llm": llm_cat,
                    "base_definition": item.get("base_definition"),
                }
            )
            print(f"  ✗ 歧異！當前歸類: {current_cat} | LLM 建議重新歸類: {llm_cat}")

    # 計算正確率
    accuracy = (matches / sample_size) * 100
    print("\n====================================")
    print(f"      {target_name}分類正確性報告 (PoC)      ")
    print("====================================")
    print(f"總抽樣個數: {sample_size}")
    print(f"一致個數  : {matches}")
    print(f"歧異個數  : {len(discrepancies)}")
    print(f"分類一致率: {accuracy:.2f}%")

    if discrepancies:
        print("\n--- 歧異案例詳細分析 ---")
        for idx, case in enumerate(discrepancies):
            print(f"\n[{idx+1}] {case['code']} ({case['display_name']})")
            print(f"    - 當前子詞庫歸類 : {case['current']}")
            print(f"    - LLM 建議重新歸類   : {case['llm']}")
            def_text = case.get('base_definition') or ""
            print(f"    - 原始定義摘要   : {def_text[:100]}...")
            print(f"    💡 [去重與重歸類指引]: 建議執行 'python src/manager.py --target {args.target} reclassify {case['code']}' 進行覆核與去重搬移")


if __name__ == "__main__":
    main()
