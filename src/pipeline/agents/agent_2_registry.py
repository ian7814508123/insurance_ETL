import json
from typing import List
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_2_SCHEMA
from pipeline.context import PipelineContext


class BenefitRegistryAgent(BaseAgent):
    """
    Agent 2: Benefit Registry Agent
    任務：找出「有哪些給付項目」
    - 不要做公式解析。
    - 不要做附表 lookup。
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位保險商品分析師，專門負責盤點保單中的所有給付項目（Benefit Registry）。
        ## 嚴格任務限制
        1. 你的任務是列出這張保單所有的給付項目（例如：身故保險金、完全失能保險金、滿期保險金等）。
        2. 嚴禁解析公式或計算邏輯。
        3. 嚴禁進行附表查表 (Lookup) 分析。
        4. 請根據提供的「條文片段清單」，判斷每個給付項目對應到哪些 `segment_id`，並將其填入 `segment_refs` 陣列中。
        5. **強制命名規範**：請參考我提供的「標準理賠項目對照表」，若該給付項目的意義與對照表中的項目相符，**必須**直接使用該對照表中的 `code` (例如 DEATH_BENEFIT) 與 `display_name`。如果找不到相符項目，才能自行定義一個全大寫英文底線格式的 `benefit_code`。
        6. 請為每個項目標註其主要的 `payment_type` (LUMP_SUM, INSTALLMENT, RECURSIVE_INSTALLMENT, REFUND, WAIVER)。
        ## base_definition 填寫規範（重要）
        - `base_definition` 只能填入條款條文的原始文字，不得加入任何附表內容。
        - 若條款中提及「依附表一（手術項目倍數表）所列倍數給付」，請直接保留此引用語句，
          絕對不可以將附表中的項目編號、手術名稱、倍數數字等表格內容直接寫入 `base_definition`。
        - 附表的具體內容留給後續 Agent 5 查表建模階段處理。
        """
        super().__init__(
            schema=AGENT_2_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    def build_registry(
        self, segments: List[dict], context: PipelineContext
    ) -> List[dict]:
        """
        :param segments: Agent 1 輸出的條文切片列表
        :param context: PipelineContext 包含基礎理賠項目對照表
        :return: 依照 AGENT_2_SCHEMA 輸出的 Benefit Registry
        """
        base_items_summary = [
            {
                "code": item.get("code"),
                "display_name": item.get("display_name"),
                "description": item.get("description"),
            }
            for item in context.base_claim_items
            if "code" in item
        ]
        base_items_str = json.dumps(base_items_summary, ensure_ascii=False, indent=2)

        content = (
            f"--- 標準理賠項目對照表 (強制參考) ---\n"
            f"請優先使用下列清單中已有的 `code` 與 `display_name`：\n"
            f"{base_items_str}\n\n"
            f"--- 保單條文片段 ---\n"
            f"{json.dumps(segments, ensure_ascii=False, indent=2)}"
        )

        # 傳遞給 LLM
        result = self.execute([content])
        return result if result is not None else []
