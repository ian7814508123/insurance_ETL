import json
from typing import List
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_2_SCHEMA


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
        5. 請為每個給付項目定義一個全大寫英文底線格式的 `benefit_code` (如 DEATH_BENEFIT)。
        6. 請為每個項目標註其主要的 `payment_type` (LUMP_SUM, INSTALLMENT, RECURSIVE_INSTALLMENT, REFUND, WAIVER)。
        """
        super().__init__(
            schema=AGENT_2_SCHEMA, system_instruction=instruction, **kwargs
        )

    def build_registry(self, segments: List[dict]) -> List[dict]:
        """
        :param segments: Agent 1 輸出的條文切片列表
        :return: 依照 AGENT_2_SCHEMA 輸出的 Benefit Registry
        """
        # 將 segments 轉換為 JSON 字串，以確保 LLM 能夠清晰讀取結構
        content = json.dumps(segments, ensure_ascii=False, indent=2)

        # 傳遞給 LLM
        result = self.execute([content])
        return result if result is not None else []
