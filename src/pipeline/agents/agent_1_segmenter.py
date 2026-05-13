from typing import Any, List
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_1_SCHEMA


class ClaimLocatorAgent(BaseAgent):
    """
    Agent 1: 理賠項目定位器
    任務：「找出理賠條文」
    - 過濾掉無關條文，只保留理賠相關段落。
    - 不要解析公式。
    - 不要抽參數。
    - 不要做 schema 融合。
    只負責將原始文本中涉及理賠的條文切分為帶有 ID 的片段。
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位保險文件解析專家，專門負責在保險條款中尋找並定位所有的「理賠/給付項目」。
        ## 嚴格任務限制
        1. 你的唯一任務是找出文件中所有涉及「理賠與給付」的條文。
        - **區分條文類型**：
            - **給付項目總覽/保險範圍**：(通常在「第X條：保險範圍」或「保險總覽」) 這些是宣告式的總覽，列出這張保單「保什麼」，但不一定有細節。請務必標註其正確的條號與名稱。
            - **具體給付細則**：(通常在後續條款) 這些是詳細規定「怎麼賠」的實體條款。
        2. 請直接忽略與理賠無關的條款（如：名詞定義、契約撤銷權、保費繳納、聲明事項等），絕對不要輸出它們。
        3. 嚴禁在此階段解析任何計算公式或抽取變數。
        4. **嚴禁包含附表/附件內容**：若條款中提到「詳如附表一」或「參閱附件二」，請僅保留該句文字。**絕對不要**將附表一或附件二中的具體表格、清單內容提取出來。
        5. 只需要紀錄這些理賠條文所在的章節、頁碼以及完整的原始文字。對於章節 (`section`)，必須完整保留「條號與條文名稱」(例如: "第十五條：加護病房保險金的給付")，這對於後續 Agent 判斷是否為「實體條款」至關重要。
        6. 請為每一個理賠條文片段賦予一個唯一的 `segment_id` (如 SEG_001, SEG_002)。
        """
        super().__init__(
            schema=AGENT_1_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    async def extract_segments(self, document_contents: List[Any]) -> List[dict]:
        """
        :param document_contents: 文件內容 (可以是字串或 PDF 轉換後的 image parts)
        :return: 依照 AGENT_1_SCHEMA 輸出的 segment 列表
        """
        result = await self.async_execute(document_contents)
        return result if result is not None else []
