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

    def generate_base_claim_items(
        self,
        base_definitions_path: Path = Path("data")
        / "definitions"
        / "base_definitions.json",
    ) -> List[Dict[str, Any]]:
        """生成保險理賠項目的基礎種子清單。

        以 base_definitions.json 中的基礎名詞定義作為語義錨點，
        引導 LLM 推導並輸出可跨商品重用的理賠項目定義。

        Args:
            base_definitions_path: base_definitions.json 的路徑，
                用於建構 context_summary 注入 Prompt。

        Returns:
            理賠項目定義的 dict 清單，每筆包含 code、display_name、
            base_definition、level 等欄位。
        """
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

        # 讀取基礎名詞定義，建構 context_summary 供 Prompt 使用
        context_summary = self._build_context_summary(base_definitions_path)

        prompt = f"""
        你是一位資深保險精算專家與保險法務人員，擅長將複雜的條款文字轉化為精確的邏輯運算模型。

        # 任務背景
        我們正在建立一套「保險名詞定義與理賠項目」的基礎種子庫（BASE 層）。
        下方「基礎名詞定義對照表」已定義好跨商品共用的名詞語義，
        你的任務是以這些名詞為語義錨點，推導並整理出可跨商品重用的保險「理賠項目」基礎清單。

        # 參考法規與示範條款
        - 《中華民國保險法》
        - 《住院醫療費用保險單示範條款（日額型 / 實支實付型）》
        - 《個人傷害保險單示範條款》
        - 《長期照顧保險單示範條款》
        - 《人壽保險單示範條款》
        - 《利率變動型人壽保險單示範條款》
        - 《法定傳染病保險給付附加條款》
        - 金管會標準化疾病定義

        # 基礎名詞定義對照表（語義錨點）
        下列每筆定義的 code 是本系統的標準識別碼，你在填寫理賠項目時，
        若 base_definition 或 parameter 引用到這些概念，請直接使用對應的 code 標記。

        {context_summary}

        # 提取策略（核心指令）
        1. **全面涵蓋**：涵蓋下列主要給付類型，每一類型至少輸出 2–4 筆細化項目：
           - 身故給付（Death Benefit）
           - 完全失能給付（Total Disability Benefit）
           - 住院醫療給付（Hospitalization Benefit，含日額型與實支實付型）
           - 手術給付（Surgical Benefit）
           - 重大傷病 / 重大疾病給付（Critical Illness Benefit）
           - 長期照顧給付（Long-Term Care Benefit）
           - 傷害醫療給付（Accidental Medical Benefit）
           - 年金給付（Annuity Benefit，含生存年金、保證年金）
           - 法定傳染病給付（Statutory Infectious Disease Benefit）
           - 解約金 / 退費（Surrender / Refund）
        2. **邏輯解構**：每一項給付必須能回答：
           - **誰**：受益人（引用 code，如 BENEFICIARY、INSURED）
           - **何時**：觸發條件與等待期（引用 code，如 DISEASE、ACCIDENTAL_INJURY_EVENT）
           - **給多少**：計算算式或固定金額模板
        3. **參數化**：在 `base_definition` 中，將商品特定數值以 `{{PARAM_NAME}}` 佔位，
           例如：「給付{{DAILY_BENEFIT_AMOUNT}}元」、「住院超過{{WAITING_DAYS}}日起算」。
        4. **同義詞**：在 `synonym_map` 填入業界常見的異稱（如「身故保險金」也稱「死亡給付」）。

        # 輸出規格
        - 僅輸出 JSON Array，每筆格式如下：
          {{ "type": "理賠項目定義", "code": "DEATH_BENEFIT", "display_name": "身故保險金",
             "base_definition": "...", "level": "BASE",
             "parameter": {{}}, "synonym_map": [], "description": "..." }}
        - `type` 固定為「理賠項目定義」。
        - `level` 固定為「BASE」。
        - `code` 使用大寫英文底線，並在整份輸出中保持唯一性。
        - 共輸出至少 30 筆理賠項目，涵蓋上述 10 大類型。
        """
        return self._generate(prompt, schema)

    @staticmethod
    def _build_context_summary(base_definitions_path: Path) -> str:
        """將 base_definitions.json 格式化為 Prompt 可用的摘要文字。

        Args:
            base_definitions_path: base_definitions.json 的路徑。

        Returns:
            格式化後的多行字串，每行代表一筆名詞定義的摘要。
            若檔案不存在或讀取失敗，回傳提示字串。
        """
        if not base_definitions_path.exists():
            return "（尚無基礎名詞定義，請先執行 --target base_definition）"
        try:
            raw = json.loads(base_definitions_path.read_text(encoding="utf-8"))
        except Exception:
            return "（base_definitions.json 讀取失敗，請確認格式是否正確）"

        if not isinstance(raw, list):
            return "（base_definitions.json 格式異常，預期為 JSON Array）"

        lines: List[str] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            code = item.get("code", "")
            display_name = item.get("display_name", "")
            description = item.get("description") or item.get("base_definition", "")
            # 截斷過長的描述，避免 Prompt 超出 token 限制
            if len(description) > 80:
                description = description[:80] + "…"
            lines.append(f"  - [{code}] {display_name}：{description}")

        if not lines:
            return "（base_definitions.json 內無有效資料）"
        return "\n".join(lines)


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
        # 以同目錄下的 base_definitions.json 作為語義錨點
        base_def_path = output_dir / "base_definitions.json"
        new_data = generator.generate_base_claim_items(
            base_definitions_path=base_def_path
        )

    existing = generator._load_json(output_file)
    merged = generator._merge_by_code(existing, new_data)
    generator._save_json(output_file, merged)
    print(f"Updated {output_file} (+{len(merged) - len(existing)} items)")


if __name__ == "__main__":
    main()
