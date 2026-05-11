import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

from google import genai
from google.genai import types

sys.path.append(os.getcwd())
import config


class BaseSeedGenerator:
    def __init__(
        self,
        api_key: str = config.GEMINI_API_KEY,
        model_name: str = config.DEFAULT_MODEL,
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def _generate(self, prompt: str, schema: Dict[str, Any]) -> List[Dict[str, Any]]:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=schema,
                temperature=0.1,
            ),
        )
        data = json.loads(response.text)
        return data if isinstance(data, list) else []

    @staticmethod
    def _merge_by_code(
        existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged = {x["code"]: x for x in existing if isinstance(x, dict) and "code" in x}
        for item in incoming:
            code = item.get("code")
            if not code:
                continue
            if code not in merged:
                merged[code] = item
        return list(merged.values())

    @staticmethod
    def _load_json(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        return data if isinstance(data, list) else []

    @staticmethod
    def _save_json(path: Path, data: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def generate_base_definitions(self) -> List[Dict[str, Any]]:
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "type",
                    "code",
                    "display_name",
                    "base_definition",
                    "level",
                ],
                "properties": {
                    "type": {"type": "string", "enum": ["名詞定義"]},
                    "description": {"type": "string"},
                    "code": {"type": "string"},
                    "display_name": {"type": "string"},
                    "base_definition": {"type": "string"},
                    "level": {"type": "string", "enum": ["BASE"]},
                    "parameter": {"type": "object"},
                    "synonym_map": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
        prompt = """
        作為保險法專家，請根據《中華民國保險法》及相關法規，整理出可跨商品重用的保險「名詞定義」基礎清單。
        要求:
        1. 僅輸出 JSON array。
        2. type 固定為「名詞定義」。
        3. level 固定 BASE。
        4. code 使用大寫英文底線。
        5. 每筆 base_definition 要精簡且可被規則引用。
        """
        return self._generate(prompt, schema)

    def generate_base_claim_items(self) -> List[Dict[str, Any]]:
        schema = {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "type",
                    "code",
                    "display_name",
                    "base_definition",
                    "level",
                ],
                "properties": {
                    "type": {"type": "string", "enum": ["理賠項目定義"]},
                    "description": {"type": "string"},
                    "code": {"type": "string"},
                    "display_name": {"type": "string"},
                    "base_definition": {"type": "string"},
                    "level": {"type": "string", "enum": ["BASE"]},
                    "parameter": {"type": "object"},
                    "synonym_map": {"type": "array", "items": {"type": "string"}},
                },
            },
        }
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
        return self._generate(prompt, schema)


def main() -> None:
    print("產生 base 名詞 python .\src\seed_generator.py --target base_definition")
    print("產生 base 理賠項目 python .\src\seed_generator.py --target base_claim_item")

    parser = argparse.ArgumentParser(
        description="Generate base seeds for insurance parsing."
    )
    parser.add_argument(
        "--target",
        choices=["base_definition", "base_claim_item"],
        required=True,
        help="Which base corpus to generate.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "data" / "definitions"),
        help="Base output directory.",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    generator = BaseSeedGenerator()

    if args.target == "base_definition":
        output_file = output_dir / "base_definitions.json"
        new_data = generator.generate_base_definitions()
    else:
        output_file = output_dir / "base_claim_items.json"
        new_data = generator.generate_base_claim_items()

    existing = generator._load_json(output_file)
    merged = generator._merge_by_code(existing, new_data)
    generator._save_json(output_file, merged)
    print(f"Updated {output_file} (+{len(merged) - len(existing)} items)")


if __name__ == "__main__":
    main()
