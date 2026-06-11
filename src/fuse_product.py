"""
用途說明:
   負責讀取第一階段產出的條款理賠 JSON 與第二階段產出的費率解析 JSON 結果，
   依據對齊標準將兩者合併成 Unified Actuarial Package (UAP) 結構，並進行查表代碼的交叉校驗。
"""

import copy
import re
from typing import Dict, Any, List


class ProductFuser:
    """統一商品精算封裝融合器 (Product Fuser for UAP)"""

    def __init__(self):
        pass

    def fuse_claim_and_rates(
        self, claim_data: Dict[str, Any], rate_results: Dict[str, Any]
    ) -> Dict[str, Any]:
        """將條款理賠 JSON 與費率解析結果進行整合。

        Args:
            claim_data: 條款解析出來的 JSON 結構 (含 claim_items, global_definitions)
            rate_results: premium_extractor 輸出的解析報告 (含 extracted_data)

        Returns:
            Dict[str, Any]: 融合後符合 UAP 規範的統一商品 JSON
        """
        uap_data = copy.deepcopy(claim_data)
        extracted = rate_results.get("extracted_data", {})
        rate_blocks = extracted.get("rate_blocks", [])
        rate_metadata = extracted.get("metadata", {})

        # 1. 決定費率表代碼，預設為 PREMIUM_RATE_TABLE
        table_code = "PREMIUM_RATE_TABLE"

        # 2. 寫入 rate_tables 與費率 metadata 區塊
        uap_data["rate_tables"] = {table_code: rate_blocks}

        if "metadata" not in uap_data:
            uap_data["metadata"] = {}

        # 融入費率端之精算 metadata 與信心評分
        uap_data["metadata"]["premium_metadata"] = rate_metadata
        uap_data["metadata"]["premium_confidence_score"] = rate_results.get(
            "global_confidence_score", "N/A"
        )

        # 3. 進行交叉校驗 (Cross-validation)
        self._validate_lookup_references(uap_data)

        return uap_data

    def _validate_lookup_references(self, uap_data: Dict[str, Any]) -> None:
        """靜態檢測條款公式中的 LOOKUP 引用與實際載入的費率表是否匹配。"""
        rate_tables = uap_data.get("rate_tables", {})
        claim_items = uap_data.get("claim_items", [])

        # 尋找公式中 LOOKUP(Table_Name, ...) 模式的正則表達式
        lookup_pattern = re.compile(r"LOOKUP\(\s*([A-Za-z0-9_]+)\s*,")

        for item in claim_items:
            logic_struct = item.get("logic_structure", {})
            formula_tpl = logic_struct.get("formula_template", {})
            expression = formula_tpl.get("expression", "")
            python_eval = logic_struct.get("python_logic_eval", "")

            # 掃描 DSL 與 Python eval 中是否有查表動作
            found_tables = []
            if expression:
                found_tables.extend(lookup_pattern.findall(expression))
            if python_eval:
                found_tables.extend(lookup_pattern.findall(python_eval))

            # 去重
            found_tables = list(set(found_tables))

            for table_name in found_tables:
                if table_name not in rate_tables:
                    print(
                        f"  -> [融合警告] 給付項目 {item.get('code')} 引用了未知的費率表: {table_name}"
                    )
                else:
                    print(
                        f"  -> [融合成功] 成功勾稽給付項目 {item.get('code')} 對 {table_name} 的引用！"
                    )


if __name__ == "__main__":
    pass
