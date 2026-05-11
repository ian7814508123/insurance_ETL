import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from google import genai
from google.genai import types

sys.path.append(os.getcwd())
import config


class ClaimSeedGenerator:
    """Generate base claim-item definitions."""

    def __init__(
        self,
        api_key: str = config.GEMINI_API_KEY,
        model_name: str = config.DEFAULT_MODEL,
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "code",
                    "display_name",
                    "type",
                    "level",
                    "classification",
                    "logic_structure",
                ],
                "properties": {
                    "type": {"type": "string", "enum": ["理賠項目定義"]},
                    "code": {
                        "type": "string",
                        "description": "大寫英文底線編碼，例如：ANNUITY_INSTALLMENT",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "商品條款中的正式給付名稱",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["BASE", "CATEGORY", "PRODUCT"],
                    },
                    "classification": {
                        "type": "string",
                        "enum": [
                            "EXISTING_MATCH",
                            "OVERRIDE",
                            "NEW_GENERAL",
                            "PRODUCT_SPECIFIC",
                        ],
                    },
                    "description": {
                        "type": "string",
                        "description": "該給付項目的白話摘要",
                    },
                    "base_definition": {
                        "type": "string",
                        "description": "完整的條款原始文字",
                    },
                    "logic_structure": {
                        "type": "object",
                        "description": "理賠的核心邏輯架構",
                        "properties": {
                            "trigger_condition": {
                                "type": "string",
                                "description": "觸發理賠的條件，例如：被保險人生存且達年金給付日",
                            },
                            "payment_period": {
                                "type": "string",
                                "description": "給付期間限制，例如：至111歲歲末或身故為止",
                            },
                            "formula_template": {
                                "type": "string",
                                "description": "抽象化的計算公式，例如：Base * Factor",
                            },
                            "is_recursive": {
                                "type": "boolean",
                                "description": "是否為遞迴給付（後一期金額依前一期調整）",
                            },
                        },
                    },
                    "parameters": {
                        "type": "array",
                        "description": "公式中涉及的參數列表",
                        "items": {
                            "type": "object",
                            "required": ["param_name", "param_description"],
                            "properties": {
                                "param_name": {
                                    "type": "string",
                                    "description": "參數代碼",
                                },
                                "param_description": {
                                    "type": "string",
                                    "description": "參數的業務含意",
                                },
                            },
                        },
                    },
                    "synonym_map": {"type": "array", "items": {"type": "string"}},
                },
            },
        }

    def generate_core_claim_items(self) -> List[Dict[str, Any]]:
        prompt = """
        作為保險法專家，請根據《中華民國保險法》及相關法規，整理出最核心的理賠項目，作為基礎參照清單。

       # Role
        你是一位資深保險精算專家與保險法務人員，擅長將複雜的條款文字轉化為精確的邏輯運算模型。

        # Task
        請從提供的「保險商品條款」或「商品簡介」文本中，提取所有【理賠與給付項目】。

        # Extraction Strategy (核心指令)
        1. **深度掃描**：不要只看「保障內容」表格，請深入閱讀「年金的給付」、「身故的處理」等章節，確保不遺漏細微的給付規則。
        2. **邏輯解構**：將每一項給付拆解為：
            - **誰** (受益人)
            - **何時** (觸發條件與期間)
            - **給多少** (計算算式或固定金額)
        3. **樣板化處理**：在 `formula_template` 中，請將具體數字替換為參數名稱（例如：將「111歲」替換為 `{{MAX_AGE}}`）。
        4. **辨識遞迴**：若給付金額會逐年調整（如調整係數），請明確標註 `is_recursive: true`。

        # Classification Rules
        - **EXISTING_MATCH**: 與基本層 {context_summary} 的理賠邏輯完全相同。
        - **OVERRIDE**: 屬於常見給付項目，但此商品有特殊限制（例如：年金給付上限年齡特別高）。
        - **NEW_GENERAL**: 發現具備通用性的新給付項目（如：利率變動型商品特有的宣告利率相關給付）。
        - **PRODUCT_SPECIFIC**: 僅限此商品的特定計畫或特約給付。

        # Output Requirement
        - 僅輸出符合 JSON Schema 的 JSON Array。
        - 確保 `code` 在同一商品內具備唯一性。

        # Context Summary (基本層對照)
        {context_summary}

        # 待處理文本
        ---
        {text}
        ---
        """
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=self.schema,
                temperature=0.1,
            ),
        )
        return json.loads(response.text)

    @staticmethod
    def merge_to_base(new_items: List[Dict[str, Any]], base_path: Path) -> int:
        if base_path.exists():
            try:
                existing = json.loads(base_path.read_text(encoding="utf-8"))
            except Exception:
                existing = []
        else:
            existing = []

        if not isinstance(existing, list):
            existing = []

        existing_by_code = {
            x["code"]: x for x in existing if isinstance(x, dict) and "code" in x
        }
        merged_count = 0

        for item in new_items:
            code = item.get("code")
            if not code:
                continue
            if code in existing_by_code:
                continue
            existing.append(item)
            existing_by_code[code] = item
            merged_count += 1

        base_path.parent.mkdir(parents=True, exist_ok=True)
        base_path.write_text(
            json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return merged_count


if __name__ == "__main__":
    generator = ClaimSeedGenerator()
    base_file = Path.cwd() / "data" / "definitions" / "claim_items_base.json"

    print("Generating base claim-item definitions...")
    seeds = generator.generate_core_claim_items()
    merged = generator.merge_to_base(seeds, base_file)
    print(f"Merged {merged} new base claim items into {base_file}")
