import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pymupdf
from google import genai
from google.genai import types

import config


class ClaimItemExtractor:
    """Extract claim-item definitions from product documents."""

    def __init__(
        self,
        api_key: str = config.GEMINI_API_KEY,
        model_name: str = config.DEFAULT_MODEL,
    ):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name
        self.schema = self.schema = {
            "type": "array",
            "description": "保險理賠/給付項目定義集合",
            "items": {
                "type": "object",
                "required": [
                    "type",
                    "code",
                    "display_name",
                    "level",
                    "classification",
                    "logic_structure",
                    "parameters",
                ],
                "properties": {
                    "type": {"type": "string", "enum": ["理賠項目定義"]},
                    "code": {
                        "type": "string",
                        "description": "唯一代碼，大寫英文底線格式",
                        "pattern": "^[A-Z0-9_]+$",
                    },
                    "display_name": {
                        "type": "string",
                        "description": "保單條款中的給付項目名稱",
                    },
                    "aliases": {
                        "type": "array",
                        "description": "同義名稱或條款別稱",
                        "items": {"type": "string"},
                    },
                    "description": {
                        "type": "string",
                        "description": "給付邏輯白話摘要",
                    },
                    "level": {
                        "type": "string",
                        "enum": ["BASE", "CATEGORY", "PRODUCT"],
                        "description": "知識庫層級",
                    },
                    "classification": {
                        "type": "string",
                        "enum": [
                            "NEW_GENERAL",
                            "PRODUCT_SPECIFIC",
                            "OVERRIDE",
                            "EXISTING_MATCH",
                        ],
                    },
                    "payment_type": {
                        "type": "string",
                        "enum": [
                            "LUMP_SUM",
                            "INSTALLMENT",
                            "RECURSIVE_INSTALLMENT",
                            "REFUND",
                            "WAIVER",
                        ],
                    },
                    "base_definition": {
                        "type": "string",
                        "description": "完整條款原文",
                    },
                    "source_reference": {
                        "type": "object",
                        "description": "原始文件定位資訊",
                        "properties": {
                            "document_name": {"type": "string"},
                            "page_start": {"type": "integer"},
                            "page_end": {"type": "integer"},
                            "section_title": {"type": "string"},
                            "clause_id": {"type": "string"},
                        },
                    },
                    "logic_structure": {
                        "type": "object",
                        "required": [
                            "trigger_condition",
                            "formula_template",
                            "python_logic_eval",
                            "is_recursive",
                        ],
                        "properties": {
                            "trigger_condition": {
                                "type": "string",
                                "description": "理賠或給付觸發條件",
                            },
                            "effective_condition": {
                                "type": "string",
                                "description": "生效條件",
                            },
                            "termination_condition": {
                                "type": "string",
                                "description": "停止給付條件",
                            },
                            "payment_period": {
                                "type": "object",
                                "properties": {
                                    "frequency": {
                                        "type": "string",
                                        "enum": [
                                            "ONCE",
                                            "MONTHLY",
                                            "QUARTERLY",
                                            "YEARLY",
                                            "DAILY",
                                        ],
                                    },
                                    "waiting_period": {
                                        "type": "string",
                                        "description": "等待期",
                                    },
                                    "coverage_period": {
                                        "type": "string",
                                        "description": "給付期間",
                                    },
                                    "max_period": {
                                        "type": "string",
                                        "description": "最長給付期間",
                                    },
                                    "max_payment_count": {
                                        "type": "integer",
                                        "description": "最大給付次數",
                                    },
                                },
                            },
                            "formula_template": {
                                "type": "object",
                                "required": ["syntax_type", "expression"],
                                "properties": {
                                    "syntax_type": {
                                        "type": "string",
                                        "enum": ["DSL", "PYTHON_EXPR"],
                                    },
                                    "expression": {
                                        "type": "string",
                                        "description": "結構化公式",
                                    },
                                    "expression_tree": {
                                        "type": "object",
                                        "description": "AST格式公式樹（可選）",
                                    },
                                },
                            },
                            "python_logic_eval": {
                                "type": "string",
                                "description": "可直接執行的 Python expression",
                            },
                            "is_recursive": {"type": "boolean"},
                            "recursive_basis": {
                                "type": "string",
                                "description": "遞迴依據",
                            },
                            "conditions": {
                                "type": "array",
                                "description": "特殊限制條件",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "condition_type": {
                                            "type": "string",
                                            "enum": [
                                                "WAITING_PERIOD",
                                                "EXCLUSION",
                                                "LIMITATION",
                                                "MAX_LIMIT",
                                                "MIN_LIMIT",
                                                "AGE_LIMIT",
                                                "SURVIVAL_REQUIREMENT",
                                                "CLAIM_INTERVAL",
                                                "OTHER",
                                            ],
                                        },
                                        "description": {"type": "string"},
                                        "formula_ref": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                    "parameters": {
                        "type": "array",
                        "description": "公式涉及之所有變數",
                        "items": {
                            "type": "object",
                            "required": [
                                "param_name",
                                "param_description",
                                "source_type",
                            ],
                            "properties": {
                                "param_name": {
                                    "type": "string",
                                    "pattern": "^[A-Z0-9_]+$",
                                },
                                "display_name": {"type": "string"},
                                "param_description": {"type": "string"},
                                "data_type": {
                                    "type": "string",
                                    "enum": [
                                        "STRING",
                                        "INTEGER",
                                        "FLOAT",
                                        "BOOLEAN",
                                        "DATE",
                                    ],
                                },
                                "value_type": {
                                    "type": "string",
                                    "enum": [
                                        "PERCENTAGE",
                                        "CURRENCY",
                                        "MULTIPLIER",
                                        "INTEGER",
                                        "YEAR",
                                        "AGE",
                                        "RATE",
                                        "COUNT",
                                        "DURATION",
                                    ],
                                },
                                "unit": {"type": "string"},
                                "nullable": {"type": "boolean"},
                                "default_value": {},
                                "example_values": {"type": "array", "items": {}},
                                "source_type": {
                                    "type": "string",
                                    "enum": [
                                        "INPUT",
                                        "CONSTANT",
                                        "FORMULA",
                                        "TABLE_LOOKUP",
                                        "SYSTEM_DERIVED",
                                    ],
                                },
                                "depends_on": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                },
                                "formula_definition": {
                                    "type": "string",
                                    "description": "若來源為 FORMULA，其推導公式",
                                },
                                "lookup_details": {
                                    "type": "object",
                                    "properties": {
                                        "table_name": {"type": "string"},
                                        "table_code": {"type": "string"},
                                        "lookup_expression": {
                                            "type": "string",
                                            "description": "LOOKUP(Table, {A,B,C})",
                                        },
                                        "lookup_keys": {
                                            "type": "array",
                                            "items": {"type": "string"},
                                        },
                                        "table_version": {"type": "string"},
                                        "table_index": {
                                            "type": "object",
                                            "properties": {
                                                "source_location": {
                                                    "type": "object",
                                                    "properties": {
                                                        "page_start": {
                                                            "type": "integer"
                                                        },
                                                        "page_end": {"type": "integer"},
                                                        "section_title": {
                                                            "type": "string"
                                                        },
                                                        "table_caption": {
                                                            "type": "string"
                                                        },
                                                        "ocr_hint": {"type": "string"},
                                                        "cross_page": {
                                                            "type": "boolean"
                                                        },
                                                        "merged_cells_exist": {
                                                            "type": "boolean"
                                                        },
                                                    },
                                                },
                                                "dimensions": {
                                                    "type": "array",
                                                    "items": {
                                                        "type": "object",
                                                        "required": [
                                                            "dimension_name",
                                                            "dimension_type",
                                                        ],
                                                        "properties": {
                                                            "dimension_name": {
                                                                "type": "string"
                                                            },
                                                            "dimension_type": {
                                                                "type": "string",
                                                                "enum": [
                                                                    "ROW",
                                                                    "COLUMN",
                                                                    "PAGE_LEVEL",
                                                                    "CONDITION",
                                                                ],
                                                            },
                                                            "data_type": {
                                                                "type": "string",
                                                                "enum": [
                                                                    "STRING",
                                                                    "INTEGER",
                                                                    "FLOAT",
                                                                    "DATE",
                                                                ],
                                                            },
                                                            "description": {
                                                                "type": "string"
                                                            },
                                                            "value_examples": {
                                                                "type": "array",
                                                                "items": {},
                                                            },
                                                            "source_location": {
                                                                "type": "string",
                                                                "description": "例如：表頭、左側列標題、頁面標題",
                                                            },
                                                            "is_required": {
                                                                "type": "boolean"
                                                            },
                                                            "is_range": {
                                                                "type": "boolean"
                                                            },
                                                            "range_format": {
                                                                "type": "string",
                                                                "description": "例如：20-30歲",
                                                            },
                                                        },
                                                    },
                                                },
                                                "data_unit": {"type": "string"},
                                            },
                                        },
                                    },
                                },
                            },
                        },
                    },
                    "benefit_limits": {
                        "type": "object",
                        "properties": {
                            "max_claim_amount": {"type": "string"},
                            "min_claim_amount": {"type": "string"},
                            "annual_limit": {"type": "string"},
                            "lifetime_limit": {"type": "string"},
                            "claim_count_limit": {"type": "integer"},
                        },
                    },
                    "override_info": {
                        "type": "object",
                        "properties": {
                            "base_code": {"type": "string"},
                            "override_reason": {"type": "string"},
                            "override_fields": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                    },
                    "metadata": {
                        "type": "object",
                        "properties": {
                            "parser_version": {"type": "string"},
                            "schema_version": {"type": "string"},
                            "created_at": {"type": "string"},
                            "confidence_score": {"type": "number"},
                            "review_status": {
                                "type": "string",
                                "enum": [
                                    "AUTO_EXTRACTED",
                                    "HUMAN_REVIEWED",
                                    "APPROVED",
                                ],
                            },
                        },
                    },
                },
            },
        }

    @staticmethod
    def load_json_array(path: Path) -> List[Dict[str, Any]]:
        if not path.exists():
            return []
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        return []

    @staticmethod
    def save_json_array(path: Path, data: List[Dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    @staticmethod
    def merge_items(
        base: List[Dict[str, Any]], incoming: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        merged = {x["code"]: x for x in base if "code" in x}
        for item in incoming:
            code = item.get("code")
            if not code:
                continue
            if code not in merged:
                merged[code] = item
                continue

            old = merged[code]
            if len(item.get("base_definition", "")) > len(
                old.get("base_definition", "")
            ):
                old["base_definition"] = item["base_definition"]
            if item.get("description"):
                old["description"] = item["description"]
            old["display_name"] = item.get(
                "display_name", old.get("display_name", code)
            )
            old["classification"] = item.get(
                "classification", old.get("classification")
            )

            old_synonyms = set(old.get("synonym_map", []))
            new_synonyms = set(item.get("synonym_map", []))
            old["synonym_map"] = sorted(old_synonyms.union(new_synonyms))

            old_param = old.get("parameter", {})
            old_param.update(item.get("parameter", {}))
            old["parameter"] = old_param

            if "origin_product" in item:
                old["origin_product"] = item["origin_product"]
        return list(merged.values())

    @staticmethod
    def pdf_to_parts(pdf_path: Path, max_pages: int = 15) -> List[types.Part]:
        doc = pymupdf.open(str(pdf_path))
        parts: List[types.Part] = []
        try:
            for i in range(min(max_pages, len(doc))):
                pix = doc[i].get_pixmap(matrix=pymupdf.Matrix(3, 3))
                parts.append(
                    types.Part.from_bytes(
                        data=pix.tobytes("jpg"), mime_type="image/jpeg"
                    )
                )
        finally:
            doc.close()
        return parts

    def extract(
        self,
        content: Any,
        context_claim_items: List[Dict[str, Any]],
        level: str,
        product_code: Optional[str],
    ) -> List[Dict[str, Any]]:
        context_summary = [
            {"code": d["code"], "display_name": d.get("display_name", d["code"])}
            for d in context_claim_items
            if "code" in d
        ]
        prompt = f"""
        # Role

        你是一位資深保險精算師、保險法務專家與保險數據架構專家，專長於：

        1. 將保險條款（壽險、傷病險、年金險、意外險等）轉換為結構化給付演算法
        2. 拆解複雜保單公式與階段式理賠邏輯
        3. 建立多維度附表查表模型（N-Dimensional Lookup Model）
        4. 將自然語言條款轉譯為可程式化的公式模板

        你的任務是從保險商品文件中，完整提取所有「理賠、給付、退還、豁免」項目，並建立具工程可執行性的結構化輸出。

        ---

        # Task

        請從提供的「保險商品文件」中：

        1. 提取所有理賠、給付、退還、豁免項目
        2. 建立完整給付邏輯鏈
        3. 拆解所有公式中的參數與條件
        4. 還原附表查表邏輯
        5. 建立 N 維度 table lookup 模型
        6. 將公式轉譯為可直接程式化執行的結構化格式

        ---

        # 核心提取原則

        ## 1. 完整邏輯鏈結（Logical Completeness）

        ### 必須完整識別：
        - 保單年度差異
        - 年齡區間差異
        - 事故發生階段差異
        - 給付期間差異
        - 保單狀態變化
        - 宣告利率變化
        - 已領取給付後的遞迴變化

        ### 嚴格要求：
        若給付邏輯隨條件變動，必須完整保留 IF-ELSE 分支。

        ### 禁止：
        - 合併不同年度邏輯
        - 簡化階段式公式
        - 遺漏條件限制

        ### 正確範例
        IF(policy_year <= 5)
        THEN insured_amount * 1.2
        ELSE insured_amount * 1.5
        ENDIF

        ---

        ## 2. 深度參數追溯（Deep Parameter Tracing）

        ### 必須識別：

        #### 外生變數（External Variables）
        例如：
        - 性別
        - 投保年齡
        - 職業等級
        - 保額
        - 宣告利率
        - 手術等級
        - 保單年度
        - 經過年度

        #### 內生參數（Internal Parameters）
        例如：
        - 保單價值準備金
        - 累積紅利
        - 已領年金
        - 保費總和
        - 解約金比例

        ---

        ## 3. 附表引用檢索（Table Lookup Resolution）

        若條文出現以下語意：
        - 詳見附表
        - 按附表比例
        - 依附表數值
        - 依保單年度表
        - 給付倍數表
        - 費率表

        則必須：

        ### 標記：
        "source_type": "TABLE_LOOKUP"

        ### 並建立：
        - lookup_details
        - table_index
        - dimensions

        ---

        ## 多維度查表解析規則（N-Dimensional Table Modeling）

        ### 不可將表格視為單純二維表

        請拆解所有隱含維度。

        ---

        ## 維度分類

        ### ROW
        表格列索引

        例如：
        - 經過年度
        - 投保年齡
        - 年齡區間

        ---

        ### COLUMN
        表格欄索引

        例如：
        - 手術等級
        - 保單年度
        - 給付比例

        ---

        ### PAGE_LEVEL
        不同子表或分頁

        例如：
        - 男性表 / 女性表
        - 主約 / 附約
        - 不同險種

        ---

        ### CONDITION
        隱含條件維度

        例如：
        - 保額級距
        - 是否高齡
        - 特定疾病
        - 職業類別

        ---

        # 查表公式標準化

        所有查表邏輯統一表示為：LOOKUP(Table_Name, {{Dim1, Dim2, Dim3}})

        ---

        # 複雜公式轉譯規則

        ## 必須使用結構化 formula_template

        允許：

        ### IF-ELSE

        IF(condition)
        THEN value_A
        ELSE value_B
        ENDIF

        ### 巢狀邏輯
        IF(A)
        THEN
            IF(B)
            THEN X
            ELSE Y
            ENDIF
        ELSE Z
        ENDIF

        ### 冪次: base ^ exponent
        ### 遞迴: payment_n = payment_(n-1) * factor

        ---

        # 遞迴給付判定

        若：
        - 後一期給付依賴前一期
        - 年金逐期增減
        - 利率滾存
        - 累積增值

        則："is_recursive": true

        ---

        # 給付類型分類

        payment_type 只能使用：
        - LUMP_SUM
        - INSTALLMENT
        - RECURSIVE_INSTALLMENT
        - REFUND
        - WAIVER

        ---

        # Classification 判定規則

        - NEW_GENERAL: 發現新的通用給付模型
        - EXISTING_MATCH: 與基礎庫完全一致
        - OVERRIDE:基礎庫存在，但：公式不同、維度不同、給付上限不同、查表方式不同
        - PRODUCT_SPECIFIC: 商品獨有邏輯

        ---

        # Schema 輸出要求

        ## 所有出現在以下位置的變數：

        - formula_template
        - lookup expression
        - trigger_condition
        - payment_period

        都必須存在於: parameters[]

        ---

        # OCR 工程支援要求

        請詳細描述: table_index.dimensions[].source_location

        需說明：
        - 維度位於表頭 / 列標題 / 頁面標題
        - 是否跨頁
        - 是否為合併儲存格
        - 是否為隱含條件

        以利後續 OCR 區域切割。

        ---

        # 輸出格式

        請嚴格遵守指定 JSON Schema。

        ---

        # 既有基礎庫摘要

        {context_summary}

        ---

        # 待處理商品文本

        {content}
    """

        contents = [prompt]
        if isinstance(content, str):
            contents.append(content)
        else:
            contents.extend(content)

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=self.schema,
                temperature=0.1,
            ),
        )
        try:
            items = json.loads(response.text)
        except json.JSONDecodeError:
            return []

        for item in items:
            item["level"] = level
            if product_code:
                item["origin_product"] = product_code
        return items


def process_product_directory(
    input_dir: Path,
    output_dir: Path,
    level: str = "PRODUCT",
    sleep_seconds: float = 1.0,
) -> None:
    extractor = ClaimItemExtractor()
    base_file = output_dir / "base.json"
    base_items = extractor.load_json_array(base_file)

    files = sorted(
        f
        for f in input_dir.iterdir()
        if f.is_file() and f.suffix.lower() in {".txt", ".pdf"} and f.name != "說明.txt"
    )

    print(f"Found {len(files)} files.")
    for idx, fp in enumerate(files, start=1):
        print(f"[{idx}/{len(files)}] Processing {fp.name}")
        try:
            if fp.suffix.lower() == ".pdf":
                content = extractor.pdf_to_parts(fp)
            else:
                content = fp.read_text(encoding="utf-8")

            product_code = fp.stem if level != "BASE" else None
            target_file = (
                base_file
                if level == "BASE"
                else output_dir / "products" / f"{fp.stem}.json"
            )

            current_items = extractor.load_json_array(target_file)
            new_items = extractor.extract(
                content, base_items, level=level, product_code=product_code
            )
            merged = extractor.merge_items(current_items, new_items)
            extractor.save_json_array(target_file, merged)
            print(f"Updated {target_file.name}: +{len(new_items)} items")
            if idx < len(files):
                time.sleep(sleep_seconds)
        except Exception as exc:
            print(f"Failed: {fp.name} -> {exc}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Extract claim items from documents.")
    parser.add_argument(
        "--mode",
        choices=["BASE", "PRODUCT"],
        default="PRODUCT",
        help="Extraction layer mode.",
    )
    parser.add_argument(
        "--input-dir",
        default=str(Path.cwd() / "product"),
        help="Input directory containing .txt/.pdf files.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(Path.cwd() / "data" / "claim_items"),
        help="Output directory for claim-item JSON.",
    )
    args = parser.parse_args()

    process_product_directory(
        input_dir=Path(args.input_dir),
        output_dir=Path(args.output_dir),
        level=args.mode,
    )
