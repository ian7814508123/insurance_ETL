import json
import os
import sys

# 將當前工作目錄加入路徑，以便導入根目錄的 config
sys.path.append(os.getcwd())

from google import genai
from google.genai import types
import config

class SeedGenerator:
    """基於保險法與法律常識產生核心名詞定義。"""

    def __init__(self, api_key: str = config.GEMINI_API_KEY, model_name: str = config.DEFAULT_MODEL):
        self.client = genai.Client(api_key=api_key)
        self.model_name = model_name

    def generate_core_definitions(self) -> list:
        prompt = """
        作為保險法專家，請根據《中華民國保險法》及相關法規，整理出最核心的名詞定義。
        
        目標名詞清單（包含但不限於）：
        - 保險契約
        - 保險人
        - 要保人
        - 被保險人
        - 受益人
        - 保險費
        - 保險金額
        - 保險事故
        - 寬限期間
        - 復效
        - 解約金
        
        輸出格式：JSON 陣列，每個項目需符合以下 Schema：
        {
            "type": "名詞定義",
            "description": "...",
            "code": "...",
            "display_name": "...",
            "base_definition": "引用保險法原文或最權威的法律描述",
            "level": "BASE",
            "classification": "EXISTING_MATCH",
            "parameter": {},
            "synonym_map": []
        }
        """

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
            ),
        )
        return json.loads(response.text)

    def merge_to_base(self, new_defs: list, base_path: str):
        if os.path.exists(base_path):
            with open(base_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
        else:
            existing = []

        existing_codes = {d["code"] for d in existing}
        merged_count = 0
        
        for d in new_defs:
            if d["code"] not in existing_codes:
                existing.append(d)
                merged_count += 1
        
        with open(base_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, ensure_ascii=False, indent=2)
        
        print(f"成功將 {merged_count} 條核心法律定義併入 base.json。")

if __name__ == "__main__":
    generator = SeedGenerator()
    base_file = r"c:\Users\User\Downloads\保費試算\data\definitions\base.json"
    
    print("正在透過 LLM 產生保險法核心名詞定義...")
    seeds = generator.generate_core_definitions()
    generator.merge_to_base(seeds, base_file)
