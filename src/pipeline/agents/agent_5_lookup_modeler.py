import json
from typing import List, Any
from pipeline.base_agent import BaseAgent
from pipeline.models import AGENT_5_SCHEMA


class LookupModelerAgent(BaseAgent):
    """
    Agent 5: Lookup Modeler（高難度）
    任務: 只做 "TABLE_LOOKUP" 建模。需要拆解所有隱含維度（ROW, COLUMN, PAGE_LEVEL, CONDITION）。
    """

    def __init__(self, **kwargs):
        instruction = """
        你是一位專業的保險數據架構師與查表專家。
        ## 嚴格任務限制
        1. 你的唯一任務是針對保單中的附表建立 N 維度 (N-Dimensional) 查表模型。
        2. 不可將表格視為單純的二維表格，必須拆解出所有的隱含維度。
           - ROW: 表格列索引 (例如：經過年度、投保年齡)
           - COLUMN: 表格欄索引 (例如：手術等級、保單年度)
           - PAGE_LEVEL: 不同子表或分頁 (例如：男性表/女性表、主約/附約)
           - CONDITION: 隱含條件維度 (例如：保額級距、特定疾病)
        3. 由於 PDF 原文可能有亂碼，請依賴提供的圖片或排版好的內容來分析表格結構。
        4. 請詳細描述 OCR 工程所需的 `source_location` (例如維度位於表頭、列標題、跨頁等)。

        若條文出現以下語意：
        - 詳見附表
        - 按附表比例
        - 依附表數值
        - 依保單年度表
        - 給付倍數表
        - 費率表

        則必須：
        ## 標記：
        "source_type": "TABLE_LOOKUP"
        ## 並建立：
        - lookup_details
        - table_index
        - dimensions

        ## 多維度查表解析規則（N-Dimensional Table Modeling）
        ### 不可將表格視為單純二維表
        ### 請拆解所有隱含維度。
        ## 維度分類
        ### ROW
        表格列索引
        例如：
        - 經過年度
        - 投保年齡
        - 年齡區間

        ### COLUMN
        表格欄索引
        例如：
        - 手術等級
        - 保單年度
        - 給付比例

        ### PAGE_LEVEL
        不同子表或分頁
        例如：
        - 男性表 / 女性表
        - 主約 / 附約
        - 不同險種

        ### CONDITION
        隱含條件維度
        例如：
        - 保額級距
        - 是否高齡
        - 特定疾病
        - 職業類別

        # 查表公式標準化
        # 所有查表邏輯統一表示為：LOOKUP(Table_Name, {Dim1, Dim2, Dim3})
        """
        super().__init__(
            schema=AGENT_5_SCHEMA,
            system_instruction=instruction,
            temperature=0.1,
            **kwargs,
        )

    async def model_lookup(
        self, benefit_code: str, parameters: dict, document_contents: List[Any]
    ) -> dict:
        """
        為「單一」給付項目解析查表模型。
        """
        # 過濾出確實需要 TABLE_LOOKUP 的變數
        lookup_params = [
            p
            for p in parameters.get("parameters", [])
            if p.get("source_type") == "TABLE_LOOKUP"
        ]

        if not lookup_params:
            return {"benefit_code": benefit_code, "lookup_tables": []}

        content_str = (
            f"給付項目: {benefit_code}\n\n"
            f"以下為需要查表的參數清單:\n{json.dumps(lookup_params, ensure_ascii=False, indent=2)}\n\n"
            "請根據附加的保單文件影像，建立這些查表動作對應的 N 維度資料模型。"
        )

        inputs = [content_str]
        inputs.extend(document_contents)

        result = await self.async_execute(inputs)
        if result:
            result["benefit_code"] = benefit_code
        return result or {"benefit_code": benefit_code, "lookup_tables": []}
