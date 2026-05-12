import sys
from pathlib import Path
from typing import List, Any

# Ensure pipeline modules can be imported
src_path = Path(__file__).parent.parent
if str(src_path) not in sys.path:
    sys.path.append(str(src_path))

from pipeline.agents.agent_1_segmenter import ClaimLocatorAgent
from pipeline.agents.agent_2_registry import BenefitRegistryAgent
from pipeline.agents.agent_3_logic_parser import LogicParserAgent
from pipeline.agents.agent_4_param_builder import ParameterGraphBuilderAgent
from pipeline.agents.agent_5_lookup_modeler import LookupModelerAgent
from pipeline.context import PipelineContext
from definition_extractor import DefinitionExtractor
import datetime


class PipelineOrchestrator:
    def __init__(self):
        self.agent_1 = ClaimLocatorAgent()
        self.agent_2 = BenefitRegistryAgent()
        self.agent_3 = LogicParserAgent()
        self.agent_4 = ParameterGraphBuilderAgent()
        self.agent_5 = LookupModelerAgent()

    def process(self, document_contents: List[Any], base_info: dict) -> dict:
        print("[0/6] Running Definition Extractor: 萃取名詞定義...")
        context = PipelineContext()
        def_extractor = DefinitionExtractor()
        
        # 讀取基礎定義檔，供 DefinitionExtractor 比對 (EXISTING_MATCH, OVERRIDE, NEW_GENERAL)
        base_def_path = Path(__file__).parent.parent.parent / "data" / "definitions" / "base_definitions.json"
        base_defs = def_extractor.load_definitions(str(base_def_path))
        print(f"  -> 載入 {len(base_defs)} 筆基礎名詞定義做為比對基準")

        # 讀取理賠項目的基礎定義檔，供 Agent 2 參考對齊
        base_claim_items_path = Path(__file__).parent.parent.parent / "data" / "definitions" / "base_claim_items.json"
        import json
        try:
            with open(base_claim_items_path, "r", encoding="utf-8") as f:
                context.base_claim_items = json.load(f)
            print(f"  -> 載入 {len(context.base_claim_items)} 筆基礎理賠項目做為對齊基準")
        except Exception as e:
            print(f"Warning: Failed to load base_claim_items.json: {e}")

        extracted_defs = def_extractor.extract_definitions(
            content=document_contents,
            context_definitions=base_defs,
            level=base_info.get("level", "PRODUCT"),
            product_code=base_info.get("product_code")
        )
        context.global_definitions = extracted_defs or []
        print(f"  -> 成功萃取 {len(context.global_definitions)} 筆名詞定義")

        print("[1/6] Running Agent 1: 條文切片...")
        segments = self.agent_1.extract_segments(document_contents)
        if not segments:
            print("Warning: Agent 1 未回傳任何段落。")

        print(
            f"[2/6] Running Agent 2: 建立 Benefit Registry... (共 {len(segments)} 段條文)"
        )
        registry = self.agent_2.build_registry(segments, context)
        if not registry:
            print("Warning: Agent 2 未找到任何給付項目。")

        benefits_data = []
        total_benefits = len(registry)

        for idx, item in enumerate(registry, start=1):
            benefit_code = item.get("benefit_code", f"UNKNOWN_{idx}")
            print(f"\n--- 處理給付項目 ({idx}/{total_benefits}): {benefit_code} ---")

            # 關聯段落
            seg_refs = item.get("segment_refs", [])
            related_segments = [s for s in segments if s.get("segment_id") in seg_refs]

            # Agent 3: 邏輯解析
            print(f"  -> [3/6] Agent 3: 邏輯解析")
            logic_struct = self.agent_3.parse_logic(benefit_code, related_segments, context)

            # Agent 4: 參數變數
            print(f"  -> [4/6] Agent 4: 變數與相依性")
            params = self.agent_4.build_parameters(
                benefit_code, logic_struct, related_segments, context
            )
            # 將 Agent 4 生成的變數註冊回 Context
            if params and "parameters" in params:
                context.register_parameters_from_agent4(params["parameters"])

            # Agent 5: 附表查表
            print(f"  -> [5/6] Agent 5: 附表查表建模")
            lookups = self.agent_5.model_lookup(benefit_code, params, document_contents)

            # 彙整單一給付項目 IR
            benefit_data = {
                "registry_info": item,
                "logic": logic_struct,
                "parameters": params,
                "lookups": lookups,
                "related_segments": related_segments,
            }
            benefits_data.append(benefit_data)

        print("\n[6/6] 腳本執行: 組裝最終 JSON Schema ...")
        final_output = self._compose_final_schema(base_info, benefits_data, context)
        return final_output

    def _compose_final_schema(self, base_info: dict, benefits_data: list, context: PipelineContext) -> dict:
        """
        取代原有的 Agent 6，透過 Python dict map 的方式組合 JSON，避免 LLM 幻覺與 Token 浪費
        """
        final_items = []
        for b_data in benefits_data:
            reg = b_data.get("registry_info", {})
            log = b_data.get("logic", {})
            param = b_data.get("parameters", {})
            lookup = b_data.get("lookups", {})
            segs = b_data.get("related_segments", [])

            # 組裝原文
            base_def = "\n".join([s.get("text", "") for s in segs])

            # 定位資訊 (取第一筆段落)
            source_ref = {}
            if segs:
                first_seg = segs[0]
                source_ref = {
                    "document_name": base_info.get("document_name"),
                    "page_start": first_seg.get("page_start"),
                    "page_end": first_seg.get("page_end"),
                    "section_title": first_seg.get("section"),
                }

            # 判斷分類 (是否存在於基礎對照表中)
            benefit_code = reg.get("benefit_code", "")
            base_codes = {b.get("code") for b in context.base_claim_items}
            classification = "EXISTING_MATCH" if benefit_code in base_codes else "NEW_GENERAL"

            item = {
                "type": "理賠項目定義",
                "code": benefit_code,
                "display_name": reg.get("display_name", ""),
                "level": base_info.get("level", "PRODUCT"),
                "classification": classification,
                "payment_type": reg.get("payment_type"),
                "base_definition": base_def,
                "source_reference": source_ref,
                "logic_structure": log.get("logic_structure", {}),
                "parameters": param.get("parameters", []),
                "lookup_tables": lookup.get("lookup_tables", []),
                "metadata": {
                    "parser_version": "2.0.0-agent-pipeline",
                    "schema_version": "1.0.0",
                    "created_at": datetime.datetime.now().isoformat(),
                    "review_status": "AUTO_EXTRACTED",
                },
            }
            final_items.append(item)

        return {
            "global_definitions": context.global_definitions,
            "claim_items": final_items
        }
