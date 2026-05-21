from typing import Any, Dict

# Agent 1: 條文切片與給付定位 (Clause Segmenter)
AGENT_1_SCHEMA = {
    "type": "array",
    "description": "保單條文切片集合",
    "items": {
        "type": "object",
        "required": ["segment_id", "section", "text"],
        "properties": {
            "segment_id": {
                "type": "string",
                "description": "唯一的條文切片ID，例如 SEG_001",
            },
            "section": {
                "type": "string",
                "description": "完整的章節或條款標題(不可省略或修改)，必須包含條號與完整名稱，例如 '第十五條：加護病房保險金的給付'",
            },
            "text": {"type": "string", "description": "條款原文內容"},
        },
    },
}

# Agent 2: 建立給付項目清單 (Benefit Registry)
AGENT_2_SCHEMA = {
    "type": "array",
    "description": "保單包含的給付項目清單",
    "items": {
        "type": "object",
        "required": ["benefit_code", "display_name", "payment_type", "segment_refs", "base_definition"],
        "properties": {
            "benefit_code": {
                "type": "string",
                "description": "唯一代碼，大寫英文底線格式 (如 DEATH_BENEFIT)",
            },
            "display_name": {
                "type": "string",
                "description": "給付項目的中文名稱 (如 身故保險金)",
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
                "description": "給付形式",
            },
            "segment_refs": {
                "type": "array",
                "items": {"type": "string"},
                "description": "對應的 segment_id 列表，說明該給付項目由哪些條文定義",
            },
            "base_definition": {
                "type": "string",
                "description": "該給付項目的原始條文描述(精密影印，排除前置宣告文字)",
            },
        },
    },
}

# Agent 3: 邏輯解析 (Logic Parser)
AGENT_3_SCHEMA = {
    "type": "object",
    "description": "單一給付項目的核心邏輯解析",
    "required": ["benefit_code", "logic_structure"],
    "properties": {
        "benefit_code": {"type": "string"},
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
                "effective_condition": {"type": "string", "description": "生效條件"},
                "termination_condition": {
                    "type": "string",
                    "description": "停止給付條件",
                },
                "formula_template": {
                    "type": "object",
                    "required": ["syntax_type", "expression"],
                    "properties": {
                        "syntax_type": {
                            "type": "string",
                            "enum": ["DSL", "PYTHON_EXPR"],
                        },
                        "expression": {"type": "string", "description": "結構化公式"},
                    },
                },
                "python_logic_eval": {
                    "type": "string",
                    "description": "可直接執行的 Python expression",
                },
                "is_recursive": {
                    "type": "boolean",
                    "description": "是否有遞迴計算或滾存",
                },
                "payment_period": {
                    "type": "object",
                    "properties": {
                        "frequency": {
                            "type": "string",
                            "enum": ["ONCE", "MONTHLY", "QUARTERLY", "YEARLY", "DAILY"],
                        },
                        "waiting_period": {"type": "string"},
                        "coverage_period": {"type": "string"},
                        "max_period": {"type": "string"},
                        "max_payment_count": {"type": "integer"},
                    },
                },
                "conditions": {
                    "type": "array",
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
    },
}

# Agent 4: 變數與相依性解析 (Parameter Graph Builder)
AGENT_4_SCHEMA = {
    "type": "object",
    "description": "單一給付項目的參數清單，並負責校正邏輯中的變數命名",
    "required": ["benefit_code", "parameters"],
    "properties": {
        "benefit_code": {"type": "string"},
        "logic_structure": {
            "type": "object",
            "description": "如果 Agent 3 提供的邏輯中變數命名與標準不符，請在此回傳修正後的完整 logic_structure。若無須修正則可省略。",
        },
        "parameters": {
            "type": "array",
            "items": {
                "type": "object",
                "required": [
                    "param_name",
                    "param_description",
                    "source_type",
                    "value_binding",
                ],
                "properties": {
                    "param_name": {"type": "string", "pattern": "^[A-Z0-9_]+$"},
                    "display_name": {"type": "string"},
                    "param_description": {"type": "string"},
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
                    "data_type": {
                        "type": "string",
                        "enum": ["STRING", "INTEGER", "FLOAT", "BOOLEAN", "DATE"],
                    },
                    "value_binding": {
                        "type": "object",
                        "description": "說明此參數的值如何取得：由使用者填入、條款已定值、或公式推導",
                        "required": ["binding_type"],
                        "properties": {
                            "binding_type": {
                                "type": "string",
                                "enum": [
                                    "USER_INPUT",
                                    "POLICY_FIXED",
                                    "SYSTEM_CALCULATED",
                                ],
                                "description": (
                                    "USER_INPUT: 使用者在報價/試算時需要主動提供的參數。"
                                    "POLICY_FIXED: 條款書上已有明確數值，系統直接帶入，不向使用者詢問。"
                                    "SYSTEM_CALCULATED: 由其他參數依公式推導，不需手動填入。"
                                ),
                            },
                            "fixed_value": {
                                "type": "string",
                                "description": "binding_type 為 POLICY_FIXED 時，填入條款所載的確切值（如 '0.025'、'90' 等）。其他類型留空。",
                            },
                        },
                    },
                    "depends_on": {"type": "array", "items": {"type": "string"}},
                    "formula_definition": {
                        "type": "string",
                        "description": "如果來源為 FORMULA，其推導公式",
                    },
                },
            },
        },
    },
}

# Agent 5: 附表與查表建模 (Lookup Modeler)
AGENT_5_SCHEMA = {
    "type": "object",
    "description": "單一給付項目的查表(Table Lookup)解析",
    "required": ["benefit_code", "lookup_tables"],
    "properties": {
        "benefit_code": {"type": "string"},
        "lookup_tables": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["table_name", "table_index"],
                "properties": {
                    "table_name": {
                        "type": "string",
                        "description": "表格名稱 (例如 附表一)",
                    },
                    "table_code": {"type": "string"},
                    "lookup_expression": {
                        "type": "string",
                        "description": "例如 LOOKUP(Table_Name, {Dim1, Dim2})",
                    },
                    "lookup_keys": {"type": "array", "items": {"type": "string"}},
                    "table_index": {
                        "type": "object",
                        "properties": {
                            "source_location": {
                                "type": "object",
                                "properties": {
                                    "page_start": {"type": "integer"},
                                    "page_end": {"type": "integer"},
                                    "table_caption": {"type": "string"},
                                    "cross_page": {"type": "boolean"},
                                    "merged_cells_exist": {"type": "boolean"},
                                },
                            },
                            "dimensions": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "required": ["dimension_name", "dimension_type"],
                                    "properties": {
                                        "dimension_name": {"type": "string"},
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
                                        "source_location": {"type": "string"},
                                    },
                                },
                            },
                        },
                    },
                },
            },
        },
    },
}

# Agent 6: 最終結果組合 (Final Schema Composer)
# 這個 Schema 基本就是原先 claim_item_extractor 的 self.schema
FINAL_SCHEMA = {
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
            "logic_structure": AGENT_3_SCHEMA["properties"]["logic_structure"],
            "parameters": AGENT_4_SCHEMA["properties"]["parameters"],
            "lookup_tables": AGENT_5_SCHEMA["properties"]["lookup_tables"],
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
