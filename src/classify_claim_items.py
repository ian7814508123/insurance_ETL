"""
1. 用途說明:
   理賠項目詞庫分類與無損拆分工具。本腳本讀取 `data/definitions/base_claim_items.json`，套用關鍵字規則引擎與 Gemini API 語意分類器，將既存的理賠給付項目依照 5 大險種分類（通用、醫療、意外、壽險年金、投資型）無損拆分，並寫入對應的 5 個子 JSON 檔案中，以維持資料單一事實來源 (SSOT)。

2. 如何使用:
   - 執行自動險種分類（優先使用規則，無法判定時調用 Gemini API）：
     python src/classify_claim_items.py
   - 執行純離線規則分類（不呼叫 Gemini API，無法判定時預設歸為 general）：
     python src/classify_claim_items.py --pure-rules
"""

import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List

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
from google import genai
from google.genai import types

CATEGORY_FILES = {
    "general": "base_claim_items_general.json",
    "health": "base_claim_items_health.json",
    "injury": "base_claim_items_injury.json",
    "investment": "base_claim_items_investment.json",
    "life_annuity": "base_claim_items_life_annuity.json",
}


def rule_classify_claim_item(item: Dict[str, Any]) -> str:
    """使用規則引擎快速分類理賠項目。如果無法確定，返回空字串。"""
    code = item.get("code", "").upper()
    display_name = item.get("display_name", "")
    desc = item.get("description", "")
    base_def = item.get("base_definition", "")

    full_text = f"{code} {display_name} {desc} {base_def}".lower()

    # 1. 優先判定是否為意外險給付 (Injury)
    # 只要有意外、外來突發、骨折且非疾病，通常是傷害保險
    injury_kws = [
        "意外傷害",
        "意外骨折",
        "骨折未住院",
        "意外醫療",
        "意外身故",
        "意外完全失能",
        "意外住院",
        "意外事故",
        "非由疾病引起",
        "外來突發",
        "突發事故",
        "職業等級",
    ]
    if (
        any(kw in full_text for kw in injury_kws)
        or "ACCIDENT" in code
        or "INJURY" in code
    ):
        # 排除完全通用的身故/完全失能
        if not (
            code in ["DEATH_BENEFIT_BASIC", "TOTAL_PERMANENT_DISABILITY_BENEFIT"]
            and "意外" not in display_name
        ):
            return "injury"

    # 2. 判定是否為壽險與年金給付 (Life / Annuity)
    # 生存金、年金、滿期金、壽險生存給付
    life_annuity_kws = [
        "生存保險金",
        "年金給付",
        "滿期保險金",
        "生存年金",
        "保證年金",
        "年金身故",
        "生存金",
        "期滿金",
        "宣告利率",
        "預定利率",
    ]
    if (
        any(kw in full_text for kw in life_annuity_kws)
        or "ANNUITY" in code
        or "SURVIVAL" in code
        or "MATURITY" in code
    ):
        return "life_annuity"

    # 3. 判定是否為投資型 (Investment)
    investment_kws = [
        "專設帳戶",
        "投資配置",
        "資產評價",
        "單位淨值",
        "分離帳戶",
        "投資型",
    ]
    if any(kw in full_text for kw in investment_kws) or "INVESTMENT" in code:
        return "investment"

    # 4. 優先判定是否為通用型給付 (General)
    # 底層保單通用給付（身故保險金、完全失能、解約金、退費）
    # 注意：這些項目不帶有明確的意外/疾病特性
    general_codes = [
        "DEATH_BENEFIT_BASIC",
        "DEATH_BENEFIT_TERM",
        "TOTAL_PERMANENT_DISABILITY_BENEFIT",
        "SURRENDER_VALUE_PAYMENT",
        "PREMIUM_REFUND_DEATH",
        "PREMIUM_REFUND_CANCELLATION",
        "SURVIVAL_BENEFIT",  # 生存保險金：還本型最基本的生存給付，有些也定義為 general
    ]
    if code in general_codes or any(c in code for c in ["SURRENDER", "REFUND"]):
        return "general"

    # 5. 判定健康醫療型 (Health)
    # 疾病、住院、手術、健保、癌症、日間留院、特定傷病、長期照顧等
    health_kws = [
        "疾病",
        "住院",
        "手術",
        "健保",
        "癌症",
        "醫療日額",
        "日間留院",
        "日間照護",
        "加護病房",
        "長期照顧",
        "長照",
        "法定傳染病",
        "確診保險金",
        "負壓隔離",
        "急診",
        "門診",
        "救護車",
        "器官移植",
        "義肢義齒",
        "居家護理",
        "復健治療",
        "生理功能障礙",
        "認知功能障礙",
        "失智",
        "巴氏量表",
        "醫院",
        "醫師",
    ]
    if (
        any(kw in full_text for kw in health_kws)
        or "HOSPITAL" in code
        or "DISEASE" in code
        or "CANCER" in code
        or "SURGICAL" in code
        or "MEDICAL" in code
        or "LTC" in code
    ):
        return "health"

    return ""


def llm_classify_claim_item(client: genai.Client, item: Dict[str, Any]) -> str:
    """使用 Gemini API 對理賠項目進行精準語意分類。"""
    schema = {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "enum": ["general", "health", "injury", "investment", "life_annuity"],
                "description": "理賠項目分類類別",
            },
            "reason": {"type": "STRING", "description": "分類的白話理由"},
        },
        "required": ["category", "reason"],
    }

    system_instruction = (
        "你是一個保險領域的中文理賠項目分類專家。請根據給定的保險理賠項目定義與表達式，將其精準歸入以下 5 個類別之一：\n"
        "1. general (通用型)：跨險種最底層的保險責任或契約通用給付/退費（如普通身故保險金、定期壽險身故、完全失能保險金、解約金、契約撤銷退保費、身故退還保費等，不具備特定醫療或意外屬性）。\n"
        "2. health (健康保險型)：圍繞疾病、醫療行為、住院、手術、健保、癌症、特定傷病、長期照顧、傳染病、急診、門診等以健康損害與醫療費用補償為主的理賠給付。\n"
        "3. injury (傷害意外險型)：以遭受「外來、突發、非疾病」的意外傷害事故所致之傷害、意外身故、意外失能、意外骨折、意外醫療實支實付、意外住院日額或與職業等級相關之意外給付項目。\n"
        "4. investment (投資型保險型)：涉及金融市場、專設帳戶配置、單位淨值、分離帳戶、資產評價等具備自主投資特性的理賠或給付項目。\n"
        "5. life_annuity (壽險與年金保險型)：生存與保險期滿返還（如生存保險金、滿期保險金、生存年金、保證年金給付等定期生存領取項目）。\n"
        "請特別注意區分：如果一個理賠項目是保單最基本的共通給付（如一般身故保險金），請優先歸入 general；如具有明確意外或健康險特質（如意外身故保險金），請歸入對應類別。"
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


def main():
    import argparse

    parser = argparse.ArgumentParser(description="分類並拆分理賠項目詞庫")
    parser.add_argument(
        "--pure-rules",
        action="store_true",
        help="啟用純規則離線分類，不呼叫 Gemini API",
    )
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data"
    definitions_dir = data_dir / "definitions"
    base_file = definitions_dir / "base_claim_items.json"

    if not base_file.exists():
        print(f"錯誤：找不到 base_claim_items.json，路徑為: {base_file}")
        sys.exit(1)

    with open(base_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(
        f"讀取到 {len(items)} 個理賠項目，開始進行分類 (純規則模式: {args.pure_rules})..."
    )

    # 初始化 Gemini Client
    client = None
    if not args.pure_rules:
        try:
            client = genai.Client(api_key=config.GEMINI_API_KEY)
        except Exception as e:
            print(f"無法初始化 Gemini Client: {e}。")
            args.pure_rules = True

    classified_data: Dict[str, List[Dict[str, Any]]] = {
        "general": [],
        "health": [],
        "injury": [],
        "investment": [],
        "life_annuity": [],
    }

    rule_count = 0
    llm_count = 0

    for i, item in enumerate(items):
        code = item.get("code")
        display_name = item.get("display_name")

        # 優先嘗試規則分類
        category = rule_classify_claim_item(item)
        if category:
            rule_count += 1
            print(
                f"[{i + 1}/{len(items)}] 規則分類: {code} ({display_name}) ➜ {category}"
            )
        else:
            if args.pure_rules:
                category = "general"
                rule_count += 1
                print(
                    f"[{i + 1}/{len(items)}] 離線預設: {code} ({display_name}) ➜ {category}"
                )
            else:
                # 規則無法判定，改用 LLM
                llm_count += 1
                category = llm_classify_claim_item(client, item)
                print(
                    f"[{i + 1}/{len(items)}] LLM 分類: {code} ({display_name}) ➜ {category}"
                )

        # 附加類別屬性並歸類，保留原始完整資料
        classified_item = {**item, "level": "BASE"}
        classified_data[category].append(classified_item)

    print("\n--- 理賠項目分類統計結果 ---")
    total_count = 0
    for cat, list_items in classified_data.items():
        print(f"類別 {cat:<12}: 共 {len(list_items)} 個理賠項目")
        total_count += len(list_items)
    print(f"總分類數: {total_count} (原始檔案總數: {len(items)})")
    print(f"規則引擎分類數: {rule_count}")
    print(f"LLM 引擎分類數: {llm_count}")

    if total_count != len(items):
        print("警告：分類後的總量與原始總量不一致！")

    # 寫入各子 JSON 檔案
    print("\n開始寫入各子 JSON 檔案...")
    for cat, list_items in classified_data.items():
        file_name = CATEGORY_FILES[cat]
        target_path = definitions_dir / file_name
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(list_items, f, ensure_ascii=False, indent=2)
        print(f"已寫入 {file_name} ({len(list_items)} 個理賠項目)。")

    print("\n理賠項目分類拆分完成！")


if __name__ == "__main__":
    main()
