import json
import os
import sys
from pathlib import Path
from typing import Dict, Any, List
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

# 定義子詞庫的檔名與類別對照
# 依據使用者指示，investment 類別保留原本的 base_definition_investment.json 檔名，其餘為 base_definitions_{category}.json
CATEGORY_FILES = {
    "general": "base_definitions_general.json",
    "health": "base_definitions_health.json",
    "injury": "base_definitions_injury.json",
    "investment": "base_definition_investment.json",
    "life_annuity": "base_definitions_life_annuity.json",
}


def rule_classify(item: Dict[str, Any]) -> str:
    """使用關鍵字與 Code 規則快速分類名詞定義。如果無法確定，返回空字串。"""
    code = item.get("code", "").upper()
    display_name = item.get("display_name", "")
    desc = item.get("description", "")
    base_def = item.get("base_definition", "")

    full_text = f"{code} {display_name} {desc} {base_def}".lower()

    # 1. 優先處理極其明確的通用保險關係人/契約基本概念
    if code in [
        "POLICY_HOLDER",
        "INSURED",
        "BENEFICIARY",
        "INSURER",
        "INSURANCE_CONTRACT",
        "GRACE_PERIOD",
        "REINSTATEMENT",
        "PREMIUM",
        "INSURANCE_ACCIDENT",
        "INS_ACT_ART_1",
        "INS_ACT_ART_2",
        "INS_ACT_ART_3",
        "INS_ACT_ART_4",
        "INS_ACT_ART_5",
        "THIS_CONTRACT",
        "THE_COMPANY",
    ]:
        return "general"

    # 2. 投資型保險
    investment_kws = [
        "專設帳戶",
        "投資配置",
        "資產評價",
        "單位淨值",
        "分離帳戶",
        "超額保險費",
        "目標保險費",
        "目標保費",
        "投資型",
        "全權委託",
        "投資帳戶",
        "投資標的",
        "投資機構",
        "保管銀行",
        "基金申購",
        "配置比例",
        "評價日",
        "淨資產價值",
    ]
    if any(kw in full_text for kw in investment_kws) or "INVESTMENT" in code:
        return "investment"

    # 3. 傷害意外險
    injury_kws = [
        "意外傷害",
        "非由疾病引起",
        "外來突發",
        "突發事故",
        "職業等級",
        "骨折",
        "意外事故",
        "失明",
        "殘廢",
        "失能程度",
        "給付表",
        "意外身故",
        "意外住院",
        "大眾運輸",
        "交通工具",
        "交通事故",
        "食物中毒",
        "機車",
        "電梯",
        "公共建築物",
    ]
    if any(kw in full_text for kw in injury_kws) or "ACCIDENT" in code or "INJURY" in code:
        # 排除包含特定長期照顧或慢性疾病的項目
        if "長期照顧" not in full_text and "慢性疾病" not in full_text:
            return "injury"

    # 4. 健康保險
    health_kws = [
        "疾病",
        "健保",
        "手術",
        "住院",
        "日間留院",
        "日間照護",
        "日間住院",
        "負壓",
        "隔離病房",
        "加護病房",
        "醫院",
        "醫師",
        "長照",
        "長期照顧",
        "巴氏量表",
        "癌症",
        "惡性腫瘤",
        "日間留院",
        "日間照護",
        "精神衛生法",
        "專科醫師",
        "生理功能障礙",
        "認知功能障礙",
        "失智",
        "日常生活自理",
        "醫療法",
        "傳染病",
    ]
    if any(kw in full_text for kw in health_kws) or "HOSPITAL" in code or "DISEASE" in code or "CANCER" in code:
        return "health"

    # 5. 壽險與年金險
    life_annuity_kws = [
        "年金",
        "生存金",
        "生存年金",
        "滿期金",
        "身故",
        "壽險",
        "保單價值準備金",
        "解約金",
        "宣告利率",
        "預定利率",
        "生存年金",
        "保險金額",
        "基本保額",
        "累計增加保險金額",
        "淨危險保額",
        "增額繳清",
        "展期定期",
        "年金生命表",
        "保證期間",
        "保證金額",
    ]
    if any(kw in full_text for kw in life_annuity_kws) or "ANNUITY" in code or "SURVIVAL" in code or "DEATH" in code:
        return "life_annuity"

    return ""


def llm_classify(client: genai.Client, item: Dict[str, Any]) -> str:
    """使用 Gemini API 對名詞進行精準語意分類。"""
    schema = {
        "type": "OBJECT",
        "properties": {
            "category": {
                "type": "STRING",
                "enum": ["general", "health", "injury", "investment", "life_annuity"],
                "description": "名詞分類類別",
            },
            "reason": {"type": "STRING", "description": "分類的白話理由"},
        },
        "required": ["category", "reason"],
    }

    system_instruction = (
        "你是一個保險領域的中文名詞分類專家。請根據給定的保險名詞定義，將其精準歸入以下 5 個類別之一：\n"
        "1. general (通用型)：所有險種皆會出現的底層法律關係、基本權利義務或共通名詞（如要保人、被保險人、受益人、復效、寬限期、保險費等）。\n"
        "2. health (健康保險型)：圍繞疾病、醫療行為、住院、手術、健保、癌症、長期照顧、生理/認知功能障礙等。\n"
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


def main():
    import argparse
    parser = argparse.ArgumentParser(description="分類並拆分名詞定義詞庫")
    parser.add_argument("--pure-rules", action="store_true", help="啟用純規則離線分類，不呼叫 Gemini API")
    args = parser.parse_args()

    data_dir = Path(__file__).parent.parent / "data"
    definitions_dir = data_dir / "definitions"
    base_file = definitions_dir / "base_definitions.json"

    if not base_file.exists():
        print(f"錯誤：找不到 base_definitions.json，路徑為: {base_file}")
        sys.exit(1)

    with open(base_file, "r", encoding="utf-8") as f:
        items = json.load(f)

    print(f"讀取到 {len(items)} 個名詞定義，開始進行分類 (純規則模式: {args.pure_rules})...")

    # 初始化 Gemini Client
    client = None
    if not args.pure_rules:
        try:
            client = genai.Client(api_key=config.GEMINI_API_KEY)
        except Exception as e:
            print(f"無法初始化 Gemini Client: {e}。自動切換至純規則離線模式。")
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
        category = rule_classify(item)
        if category:
            rule_count += 1
            print(f"[{i+1}/{len(items)}] 規則分類: {code} ({display_name}) ➜ {category}")
        else:
            if args.pure_rules:
                category = "general"
                rule_count += 1
                print(f"[{i+1}/{len(items)}] 離線預設: {code} ({display_name}) ➜ {category}")
            else:
                # 規則無法判定，改用 LLM
                llm_count += 1
                category = llm_classify(client, item)
                print(f"[{i+1}/{len(items)}] LLM 分類: {code} ({display_name}) ➜ {category}")

        # 附加類別屬性並歸類
        classified_item = {**item, "level": "BASE"}
        classified_data[category].append(classified_item)

    print("\n--- 分類統計結果 ---")
    for cat, list_items in classified_data.items():
        print(f"類別 {cat:<12}: 共 {len(list_items)} 個名詞")
    print(f"規則引擎分類數: {rule_count}")
    print(f"LLM 引擎分類數: {llm_count}")

    # 寫入各子 JSON 檔案
    print("\n開始寫入各子 JSON 檔案...")
    for cat, list_items in classified_data.items():
        file_name = CATEGORY_FILES[cat]
        target_path = definitions_dir / file_name
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(list_items, f, ensure_ascii=False, indent=2)
        print(f"已寫入 {file_name} ({len(list_items)} 個名詞)。")

    print("\n分類拆分完成！")


if __name__ == "__main__":
    main()
