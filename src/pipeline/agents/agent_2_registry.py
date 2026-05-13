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
        ## base_definition 1:1 絕對複製規範（最高優先級）
        1. 你在填寫 `base_definition` 時，必須表現得像一台「精密影印機」。
        2. **嚴禁改寫**：絕對禁止潤飾文字、更換同義詞、修正錯字或調整標點符號。
        3. **完整提取**：必須完整保留該給付項目的原始條文描述，不得進行摘要或縮減。
           - **區分條文類型**：
            - **給付項目總覽/保險範圍**：(通常在「第X條：保險範圍」或「保險總覽」) 這些是宣告式的總覽，列出這張保單「保什麼」，但不一定有細節。請務必標註其正確的條號與名稱。
            - **具體給付細則**：(通常在後續條款) 這些是詳細規定「怎麼賠」的實體條款。
            - **實體條款優先原則**：若具體給付條款中出現「因第 X 條之約定而住院接受...者，本公司按...給付」等文字時，base_definition 與 source_reference 的 section_title 必須鎖定在該項目專屬的給付細則條款，嚴禁誤將第 X 條（總則）誤認為給付實體來源。
        4. **禁止解析**：不得將條文中的邏輯轉化為你自己的語言，必須保留法律條文的原始語氣。
        5. **附表處理**：若條款中提及「依附表一...所列倍數給付」，請直接「原封不動」保留此引用語句。絕對不可以將附表中的具體項目內容直接填入此處。
        6. **負面案例警告**：
           - 錯誤行為：在 `base_definition` 中寫出「1.心臟手術 100倍、2.肺部手術 50倍...」
           - 正確行為：在 `base_definition` 中寫出「...按其投保保險金額乘以『附表一』所列之倍數給付。」
        7. **目標**：你的輸出必須能與原始文字達成字元級別（Character-level）的對齊，任何微小的改動都會被視為錯誤。
        8. 附表的具體細節留給後續 Agent 5 處理。
        """
        super().__init__(
            schema=AGENT_2_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    async def build_registry(
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
        result = await self.async_execute([content])
        return result if result is not None else []
