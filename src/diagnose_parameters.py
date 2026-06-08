"""
1. 用途說明:
   參數來源自動診斷與標記引擎 (Parameter Source Diagnoser & Aligner)。
   本腳本負責對基底層與商品層理賠項目的 `parameters` 進行靜態分析：
   - 對每個參數判定並標記 `source_type` 欄位為 DEFINITION_ALIGN, SYSTEM_DERIVED 或 USER_INPUT。
   - 命名一致性檢查：檢查參數是否與名詞定義中的中文概念相同但英文 Code 不同（如 INSURANCE_AMOUNT vs SUM_INSURED），自動推薦標準 Code。
   - 自動修復 (--fix)：當啟用修復時，連鎖更新 parameters 的 param_name、source_type，以及 logic_structure 中所有引用的公式與表示式，防止命名空間斷鍵。

2. 如何使用:
   - 唯讀掃描：
     python src/diagnose_parameters.py
   - 執行自動修復並更新 JSON 檔案：
     python src/diagnose_parameters.py --fix
   - 指定資料夾：
     python src/diagnose_parameters.py --data-dir ./data
"""

import json
import os
import re
import argparse
import sys
from pathlib import Path
from typing import Dict, List, Any, Set, Tuple

# 確保在 Windows 環境下輸出 UTF-8 不崩潰
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 常見的系統衍生或計算型變數 (不區分大小寫)
SYSTEM_DERIVED_VARIABLES = {
    "POLICY_YEAR",
    "ELAPSED_YEARS",
    "POLICY_YEAR_COUNT",
    "CURRENT_DATE",
    "EVENT_DATE",
    "SURVIVAL_STATUS",
    "DAYS_SINCE_ACCIDENT",
    "CONTRACT_EFFECTIVE_DATE",
    "INSURANCE_AGE",
    "AGE",
    "POLICY_MONTHS",
    "ELAPSED_MONTHS",
    "TODAY",
}


class ParameterDiagnoser:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.definitions_dir = data_dir / "definitions"
        self.products_dir = data_dir / "claim_items" / "products"

        # 儲存基底層名詞定義
        self.base_def_by_code = {}  # code -> item
        self.base_def_by_name = {}  # name -> code
        self.base_def_codes = set()

        # 載入基底名詞定義
        self._load_base_definitions()

    def _load_base_definitions(self):
        def_files = [
            "base_definitions_general.json",
            "base_definitions_health.json",
            "base_definitions_injury.json",
            "base_definitions_life_annuity.json",
            "base_definition_investment.json",
        ]
        for fname in def_files:
            path = self.definitions_dir / fname
            if path.exists():
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for item in data:
                                code = item.get("code")
                                name = item.get("display_name")
                                if code:
                                    self.base_def_codes.add(code)
                                    self.base_def_by_code[code] = item
                                    if name:
                                        self.base_def_by_name[name.strip()] = code
                                    # 處理 synonym_map 中的同義詞
                                    synonyms = item.get("synonym_map", [])
                                    if isinstance(synonyms, list):
                                        for syn in synonyms:
                                            if syn:
                                                self.base_def_by_name[syn.strip()] = (
                                                    code
                                                )
                except Exception as e:
                    print(f"[警告] 無法讀取名詞定義檔案 {fname}: {e}")
        print(f"共載入 {len(self.base_def_codes)} 個基底名詞定義")

    def build_product_definitions(
        self, global_defs: List[Dict[str, Any]]
    ) -> Tuple[Set[str], Dict[str, str]]:
        """
        結合商品層的 global_definitions 與基底名詞定義，建立該商品專屬的定義查找表。
        """
        prod_codes = set(self.base_def_codes)
        prod_by_name = dict(self.base_def_by_name)

        for item in global_defs:
            code = item.get("code")
            name = item.get("display_name")
            if code:
                prod_codes.add(code)
                if name:
                    prod_by_name[name.strip()] = code
                synonyms = item.get("synonym_map", [])
                if isinstance(synonyms, list):
                    for syn in synonyms:
                        if syn:
                            prod_by_name[syn.strip()] = code
        return prod_codes, prod_by_name

    def diagnose_parameter(
        self, param: Dict[str, Any], def_codes: Set[str], def_by_name: Dict[str, str]
    ) -> str:
        """
        判定單一參數的來源類型: DEFINITION_ALIGN, SYSTEM_DERIVED, USER_INPUT
        """
        param_name = param.get("param_name", "")
        display_name = param.get("display_name", "")
        description = param.get("param_description", "")
        value_binding = param.get("value_binding", {})
        binding_type = (
            value_binding.get("binding_type")
            if isinstance(value_binding, dict)
            else None
        )

        # 1. 檢查是否在名詞定義中 (優先用名稱, 再用 code)
        # 用 param_name 直接命中
        if param_name in def_codes:
            return "DEFINITION_ALIGN"

        # 用 display_name 或是描述中完全等於名詞名稱
        clean_display = display_name.strip() if display_name else ""
        clean_desc = description.strip() if description else ""

        if clean_display in def_by_name or clean_desc in def_by_name:
            return "DEFINITION_ALIGN"

        # 2. 檢查是否為系統衍生或計算
        if param_name.upper() in SYSTEM_DERIVED_VARIABLES:
            return "SYSTEM_DERIVED"

        if binding_type == "SYSTEM_CALCULATED":
            return "SYSTEM_DERIVED"

        if param.get("source_type") in ["SYSTEM_DERIVED", "FORMULA"]:
            return "SYSTEM_DERIVED"

        # POLICY_FIXED 由於是條款規定值由系統代入，在此亦歸類為系統衍生或常數處理
        if binding_type == "POLICY_FIXED":
            return "SYSTEM_DERIVED"

        # 3. 預設為使用者輸入 (USER_INPUT)
        if binding_type == "USER_INPUT":
            return "USER_INPUT"

        return "USER_INPUT"

    def check_naming_mismatch(
        self, param: Dict[str, Any], def_by_name: Dict[str, str]
    ) -> str:
        """
        檢查參數是否與名詞定義 display_name 概念相同但 code 不同。
        如果相同，回傳推薦的標準 code，否則回傳 None。
        """
        param_name = param.get("param_name", "")
        display_name = param.get("display_name", "")
        description = param.get("param_description", "")

        # 查找目標名稱
        target_name = None
        if display_name and display_name.strip() in def_by_name:
            target_name = display_name.strip()
        elif description and description.strip() in def_by_name:
            target_name = description.strip()

        if target_name:
            standard_code = def_by_name[target_name]
            if param_name != standard_code:
                # 排除一些特殊情況，除非明確要求
                return standard_code
        return None

    def replace_code_in_logic(self, item: Dict[str, Any], old_code: str, new_code: str):
        """
        當參數被更名時，連鎖更換理賠項目邏輯中所有引用舊 Code 的地方。
        """

        def replace_text(text: Any) -> Any:
            if not isinstance(text, str):
                return text
            # 全字匹配替換，避免替換到部分字元 (例如 EXPR_ABC 中替換 ABC)
            return re.sub(rf"\b{old_code}\b", new_code, text)

        logic = item.get("logic_structure", {})
        if not logic or not isinstance(logic, dict):
            return

        # 替換 trigger_condition
        if "trigger_condition" in logic:
            logic["trigger_condition"] = replace_text(logic["trigger_condition"])

        # 替換 effective_condition
        if "effective_condition" in logic:
            logic["effective_condition"] = replace_text(logic["effective_condition"])

        # 替換 termination_condition
        if "termination_condition" in logic:
            logic["termination_condition"] = replace_text(
                logic["termination_condition"]
            )

        # 替換 formula_template
        formula = logic.get("formula_template", {})
        if isinstance(formula, dict):
            if "expression" in formula:
                formula["expression"] = replace_text(formula["expression"])
        elif isinstance(formula, str):
            logic["formula_template"] = replace_text(formula)

        # 替換 python_logic_eval
        if "python_logic_eval" in logic:
            logic["python_logic_eval"] = replace_text(logic["python_logic_eval"])

        # 替換 conditions
        conditions = logic.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if isinstance(cond, dict) and "formula_ref" in cond:
                    cond["formula_ref"] = replace_text(cond["formula_ref"])

        # 替換 parameters 的 depends_on 與 formula_definition
        params = item.get("parameters", [])
        if isinstance(params, list):
            for p in params:
                if not isinstance(p, dict):
                    continue
                # depends_on
                dep = p.get("depends_on", [])
                if isinstance(dep, list):
                    p["depends_on"] = [new_code if x == old_code else x for x in dep]
                # formula_definition
                if "formula_definition" in p:
                    p["formula_definition"] = replace_text(p["formula_definition"])

    def process_data(
        self, data: Any, fix_mode: bool
    ) -> Tuple[Any, List[Dict[str, Any]], bool]:
        """
        對記憶體中的資料物件直接進行診斷與自動修復。
        """
        if data is None:
            return data, [], False

        claim_items = []
        global_defs = []

        # 區分商品層與基底層 JSON 格式
        if isinstance(data, dict):
            claim_items = data.get("claim_items", [])
            global_defs = data.get("global_definitions", [])
        elif isinstance(data, list):
            claim_items = data

        if not isinstance(claim_items, list):
            return data, [], False

        # 建立該檔案的名詞定義查找表
        def_codes, def_by_name = self.build_product_definitions(global_defs)

        file_modified = False
        warnings = []

        for item in claim_items:
            if not isinstance(item, dict):
                continue

            item_code = item.get("code", "UNKNOWN")
            item_name = item.get("display_name", "未命名給付")
            parameters = item.get("parameters", [])

            if not isinstance(parameters, list):
                continue

            for param in parameters:
                if not isinstance(param, dict):
                    continue

                old_name = param.get("param_name", "")
                if not old_name:
                    continue

                # 1. 診斷 source_type
                predicted_source = self.diagnose_parameter(
                    param, def_codes, def_by_name
                )
                current_source = param.get("source_type")

                if current_source != predicted_source:
                    warnings.append(
                        {
                            "item_code": item_code,
                            "item_name": item_name,
                            "param_name": old_name,
                            "type": "SOURCE_MISMATCH",
                            "msg": f"參數 '{old_name}' 的 source_type 預計為 {predicted_source} (目前為 {current_source})",
                            "predicted_source": predicted_source,
                        }
                    )
                    if fix_mode:
                        param["source_type"] = predicted_source
                        file_modified = True

                # 2. 靜態命名一致性檢查
                recommended_code = self.check_naming_mismatch(param, def_by_name)
                if recommended_code and old_name != recommended_code:
                    warnings.append(
                        {
                            "item_code": item_code,
                            "item_name": item_name,
                            "param_name": old_name,
                            "type": "NAMING_MISMATCH",
                            "msg": f"參數 '{old_name}' 與名詞定義命名不一致。建議改為 standard Code: '{recommended_code}'",
                            "recommended_code": recommended_code,
                        }
                    )
                    if fix_mode:
                        # 執行連鎖修正
                        param["param_name"] = recommended_code
                        self.replace_code_in_logic(item, old_name, recommended_code)
                        file_modified = True

        return data, warnings, file_modified

    def process_claim_items(
        self, file_path: Path, fix_mode: bool
    ) -> Tuple[List[Dict[str, Any]], bool]:
        """
        處理單一 JSON 檔案中的理賠給付項目，進行診斷與自動修復。
        """
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"[錯誤] 無法讀取 {file_path.name}: {e}")
            return [], False

        data, warnings, file_modified = self.process_data(data, fix_mode)

        # 如果有修改且是修復模式，寫回 JSON 檔案
        if fix_mode and file_modified:
            try:
                with open(file_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
            except Exception as e:
                print(f"[錯誤] 無法寫回檔案 {file_path.name}: {e}")
                return warnings, False

        return warnings, file_modified

    def run(self, fix_mode: bool = False):
        """
        執行掃描與診斷。
        """
        print("\n" + "=" * 60)
        print(
            f"正在執行參數來源自動診斷與標記引擎 (修復模式: {'開啟' if fix_mode else '關閉'})"
        )
        print("=" * 60)

        # 1. 收集所有要檢查的理賠項目檔案
        claim_files = [
            "base_claim_items_general.json",
            "base_claim_items_health.json",
            "base_claim_items_injury.json",
            "base_claim_items_life_annuity.json",
            "base_claim_items_investment.json",
        ]

        targets: List[Tuple[str, Path]] = []
        # 基底層理賠項目
        for fname in claim_files:
            path = self.definitions_dir / fname
            if path.exists():
                targets.append(("基底層詞庫", path))

        # 商品層理賠項目
        if self.products_dir.exists():
            for fpath in self.products_dir.glob("*.json"):
                targets.append(("商品層資料", fpath))

        print(f"共尋找到 {len(targets)} 個理賠定義 JSON 檔案待檢查。\n")

        total_warnings = 0
        total_modified_files = 0

        for group, path in targets:
            warnings, modified = self.process_claim_items(path, fix_mode)
            if warnings:
                total_warnings += len(warnings)
                print(f"【{group}】檔案: {path.name} (警示數: {len(warnings)})")
                for w in warnings:
                    print(
                        f"  [{w['type']}] 理賠項目 '{w['item_code']}' ({w['item_name']}):"
                    )
                    print(f"    -> {w['msg']}")
                print("-" * 50)
            if modified:
                total_modified_files += 1

        print("\n" + "=" * 60)
        print("診斷與標記報告摘要：")
        print(f"  總警示 / 修正項目數: {total_warnings}")
        if fix_mode:
            print(f"  已成功更新並修復檔案數: {total_modified_files} 個")
        else:
            print(
                f"  提示: 執行 `python src/diagnose_parameters.py --fix` 以自動更新 source_type 並重命名不一致變數"
            )
        print("=" * 60 + "\n")


def main():
    parser = argparse.ArgumentParser(
        description="理賠給付參數三向診斷與命名一致性標記引擎"
    )
    parser.add_argument(
        "--data-dir",
        default=str(Path(__file__).parent.parent / "data"),
        help="專案的 data 目錄路徑",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="啟用自動標記 source_type 與重新命名不對齊變數，並寫回 JSON 檔案",
    )

    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    if not data_dir.exists():
        print(f"[錯誤] 指定的 data 目錄不存在: {data_dir}")
        sys.exit(1)

    diagnoser = ParameterDiagnoser(data_dir)
    diagnoser.run(fix_mode=args.fix)


if __name__ == "__main__":
    main()
