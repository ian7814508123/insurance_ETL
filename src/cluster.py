"""
1. 用途說明:
   統一的名詞定義與理賠項目之批次語義聚類與去重工具。本腳本遍歷所有商品 JSON 檔案，
   根據指定的 target (definition 或 claim_item) 收集 classification == "NEW_GENERAL" 的候選項目，
   進行字面去重後，調用 Gemini Embedding API 將描述特徵向量化，
   再使用 Complete Linkage 層次聚類演算法（餘弦相似度 >= 0.85）將相似項目聚合為語義群組，
   最後將結果輸出為對應的聚類結果 JSON (definition_clusters.json 或 claim_item_clusters.json)，
   供後續管理端批次晉升審核與商品聯動更新。

2. 如何使用:
   - 對名詞定義進行聚類：
     python src/cluster.py --target definition
   - 對理賠項目進行聚類：
     python src/cluster.py --target claim_item
"""

import json
import os
import sys
import math
import time
import argparse
from pathlib import Path
from typing import Dict, Any, List, Tuple, Set

# 確保在 Windows 環境下輸出 UTF-8 不崩潰
try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# 加入專案根目錄至 sys.path，以便 import config
root_path = Path(__file__).parent.parent
if str(root_path) not in sys.path:
    sys.path.append(str(root_path))

import config
from google import genai
from google.genai import types

def cosine_similarity(v1: List[float], v2: List[float]) -> float:
    dot_product = sum(x * y for x, y in zip(v1, v2))
    norm_v1 = math.sqrt(sum(x * x for x in v1))
    norm_v2 = math.sqrt(sum(x * x for x in v2))
    if norm_v1 == 0.0 or norm_v2 == 0.0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)

def main():
    parser = argparse.ArgumentParser(description="統一的語意聚類與去重工具")
    parser.add_argument(
        "--target",
        choices=["definition", "claim_item"],
        required=True,
        help="聚類對象：definition (名詞定義) 或 claim_item (理賠項目)",
    )
    parser.add_argument(
        "--data-dir",
        default=str(root_path / "data"),
        help="專案資料目錄 (預設為專案的 data 目錄)",
    )
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    products_dir = data_dir / "claim_items" / "products"
    
    if not products_dir.exists():
        print(f"錯誤：找不到商品 JSON 目錄，路徑為: {products_dir}")
        sys.exit(1)
        
    print(f"開始掃描商品 JSON 檔案並收集 NEW_GENERAL 的 {args.target} 項目...")
    
    # 決定對應的 json 屬性鍵值
    if args.target == "definition":
        data_key = "global_definitions"
    else:
        data_key = "claim_items"

    # 收集所有的 NEW_GENERAL 項目，以 (display_name, description) 作為 key 進行字面去重
    candidates: Dict[Tuple[str, str], Dict[str, Any]] = {}
    total_found = 0
    
    # 遍歷 products/ 目錄下的所有 json 檔案
    for json_path in products_dir.glob("*.json"):
        if json_path.name == "test_product.json":
            continue
            
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception as e:
            print(f"讀取 {json_path.name} 失敗: {e}")
            continue
            
        if not isinstance(data, dict):
            print(f"警告：{json_path.name} 頂層非 dict 結構，跳過。")
            continue
            
        items = data.get(data_key, [])
        product_code = json_path.stem
        
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("classification") == "NEW_GENERAL":
                total_found += 1
                display_name = item.get("display_name", "").strip()
                description = item.get("description", "").strip()
                
                # 如果沒有 description，用 base_definition 代替
                if not description:
                    description = item.get("base_definition", "").strip()
                    
                key = (display_name, description)
                
                # 確保 item 中寫入來源商品代碼以供後續成員對齊與 manager 更新使用
                item["origin_product"] = product_code
                
                if key not in candidates:
                    candidates[key] = {
                        "display_name": display_name,
                        "description": description,
                        "base_definition": item.get("base_definition", "").strip(),
                        "origin_products": {product_code},
                        "original_items": [item]
                    }
                else:
                    candidates[key]["origin_products"].add(product_code)
                    candidates[key]["original_items"].append(item)
                    
    candidate_list = list(candidates.values())
    print(f"掃描完畢。共找到 {total_found} 筆 NEW_GENERAL 的 {args.target} 項目。")
    print(f"進行字面去重後，共有 {len(candidate_list)} 筆唯一候選特徵。")
    
    if not candidate_list:
        print(f"沒有找到任何 NEW_GENERAL 的 {args.target} 項目，結束執行。")
        sys.exit(0)
        
    # 2. 實作 Embedding 向量計算
    api_key = config.GEMINI_API_KEY
    if not api_key:
        print("錯誤：config.GEMINI_API_KEY 未設定，試圖讀取環境變數 GEMINI_API_KEY...")
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            print("錯誤：無法取得 Gemini API Key，請檢查環境變數或 config.py")
            sys.exit(1)
            
    print("初始化 Gemini Client...")
    client = genai.Client(api_key=api_key)
    
    # 自動偵測可用的 embedding 模型
    embedding_model = "text-embedding-004"
    try:
        models = list(client.models.list())
        available_models = [m.name for m in models]
        t_model = next((m for m in available_models if "text-embedding-004" in m), None)
        if t_model:
            embedding_model = t_model
        else:
            e_model = next((m for m in available_models if "embedding-001" in m), None)
            if e_model:
                embedding_model = e_model
        print(f"自動偵測 Embedding 模型為: {embedding_model}")
    except Exception as e:
        print(f"自動偵測模型列表失敗（可能無權限），將使用預設模型: {e}")
        
    print("開始計算 Embedding 向量...")
    features_texts = [f"{c['display_name']} {c['description']}" for c in candidate_list]
    
    # 批次呼叫 API（每批最多 50 筆）
    batch_size = 50
    embeddings: List[List[float]] = []
    
    for i in range(0, len(features_texts), batch_size):
        batch = features_texts[i:i+batch_size]
        print(f"正在計算第 {i+1} 至 {min(i+batch_size, len(features_texts))} 筆的向量...")
        
        max_retries = 3
        retry_delay = 2.0
        success = False
        
        for retry in range(max_retries):
            try:
                response = client.models.embed_content(
                    model=embedding_model,
                    contents=batch
                )
                batch_embeddings = [e.values for e in response.embeddings]
                embeddings.extend(batch_embeddings)
                success = True
                break
            except Exception as e:
                err_str = str(e)
                print(f"計算向量失敗 (嘗試 {retry+1}/{max_retries}): {err_str}")
                
                # 自動自癒：如果 404 NOT_FOUND 且當前模型為 text-embedding-004，自動切換至 embedding-001
                if "404" in err_str or "NOT_FOUND" in err_str:
                    if "text-embedding-004" in embedding_model:
                        print("檢測到 404/NOT_FOUND，自動切換至備份模型 embedding-001 重新嘗試...")
                        embedding_model = "embedding-001"
                        try:
                            response = client.models.embed_content(
                                model=embedding_model,
                                contents=batch
                            )
                            batch_embeddings = [e.values for e in response.embeddings]
                            embeddings.extend(batch_embeddings)
                            success = True
                            break
                        except Exception as e2:
                            print(f"以備用模型 embedding-001 重試也失敗: {e2}")
                            if "models/" not in embedding_model:
                                print("嘗試加上 models/ 前綴：models/embedding-001...")
                                embedding_model = "models/embedding-001"
                                try:
                                    response = client.models.embed_content(
                                        model=embedding_model,
                                        contents=batch
                                    )
                                    batch_embeddings = [e.values for e in response.embeddings]
                                    embeddings.extend(batch_embeddings)
                                    success = True
                                    break
                                except Exception as e3:
                                    print(f"以 models/embedding-001 重試失敗: {e3}")
                                    
                if retry < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    
        if not success:
            print("無法從 Gemini Embedding API 取得向量，程序終止。")
            sys.exit(1)
            
        if i + batch_size < len(features_texts):
            time.sleep(1.0)
            
    print("向量特徵計算完成。")
    
    # 3. Complete Linkage 層次聚類演算法
    print("開始執行 Complete Linkage 層次聚類...")
    N = len(candidate_list)
    
    # 預先計算兩兩相似度
    sim_matrix = {}
    for i in range(N):
        for j in range(i + 1, N):
            sim = cosine_similarity(embeddings[i], embeddings[j])
            sim_matrix[(i, j)] = sim
            sim_matrix[(j, i)] = sim
            
    # 聚類列表，每個聚類是一組候選項目的 index 列表
    clusters: List[List[int]] = [[i] for i in range(N)]
    
    # 計算兩個聚類之間的 Complete Linkage 相似度
    def get_cluster_similarity(c1: List[int], c2: List[int]) -> float:
        min_sim = 1.0
        for idx1 in c1:
            for idx2 in c2:
                if idx1 == idx2:
                    continue
                sim = sim_matrix.get((idx1, idx2), 0.0)
                if sim < min_sim:
                    min_sim = sim
        return min_sim
        
    threshold = 0.85
    
    while len(clusters) > 1:
        max_sim = -1.0
        best_pair = (-1, -1)
        
        # 尋找 Complete Linkage 相似度最高的一對聚類
        for i in range(len(clusters)):
            for j in range(i + 1, len(clusters)):
                sim = get_cluster_similarity(clusters[i], clusters[j])
                if sim > max_sim:
                    max_sim = sim
                    best_pair = (i, j)
                    
        # 如果最高相似度小於閾值 0.85，終止合併
        if max_sim < threshold:
            print(f"相似度最高的一對聚類相似度為 {max_sim:.4f} < {threshold}，停止合併。")
            break
            
        # 合併 best_pair[0] 與 best_pair[1]
        c_i, c_j = best_pair
        print(f"合併聚類 (相似度 {max_sim:.4f})：")
        print(f"  群組 1 的代表: {candidate_list[clusters[c_i][0]]['display_name']}")
        print(f"  群組 2 的代表: {candidate_list[clusters[c_j][0]]['display_name']}")
        
        clusters[c_i].extend(clusters[c_j])
        clusters.pop(c_j)
        
    print(f"聚類完成！共有 {len(clusters)} 個群組。")
    
    # 4. 輸出聚類結果
    output_clusters = []
    
    for c_idx, cluster_members in enumerate(clusters):
        cluster_id = f"{args.target.upper()}_CLUSTER_{c_idx + 1:03d}"
        
        # 找出代表項目：以 description 最長或 params 最多者為代表
        best_member_idx = -1
        best_score = -1
        
        for idx in cluster_members:
            cand = candidate_list[idx]
            first_raw = cand["original_items"][0]
            desc_len = len(cand["description"])
            
            # definition 用 parameter，claim_item 用 parameters
            if args.target == "definition":
                params_count = len(first_raw.get("parameter", {}))
            else:
                params_count = len(first_raw.get("parameters", []))
                
            score = desc_len + params_count * 10
            
            if score > best_score:
                best_score = score
                best_member_idx = idx
                
        rep_candidate = candidate_list[best_member_idx]
        rep_raw_item = rep_candidate["original_items"][0]
        rep_code = rep_raw_item.get("code")
        
        # 整理群組內所有成員資訊
        members_info = []
        for idx in cluster_members:
            cand = candidate_list[idx]
            for raw_item in cand["original_items"]:
                members_info.append({
                    "code": raw_item.get("code"),
                    "display_name": raw_item.get("display_name"),
                    "origin_product": raw_item.get("origin_product"),
                    "description": cand["description"]
                })
                
        # 收集群組內所有成員的 display_name 與既存同義詞，用以擴充代表項目的 synonym_map
        all_synonyms = set()
        for syn in rep_raw_item.get("synonym_map", []):
            all_synonyms.add(syn)
        for syn in rep_raw_item.get("aliases", []):
            all_synonyms.add(syn)
            
        for idx in cluster_members:
            cand = candidate_list[idx]
            all_synonyms.add(cand["display_name"])
            for raw_item in cand["original_items"]:
                all_synonyms.add(raw_item.get("display_name", ""))
                for syn in raw_item.get("synonym_map", []):
                    all_synonyms.add(syn)
                for syn in raw_item.get("aliases", []):
                    all_synonyms.add(syn)
                    
        # 排除與代表項目 display_name 相同的詞，以及空字串
        rep_display_name = rep_candidate["display_name"]
        all_synonyms.discard(rep_display_name)
        all_synonyms.discard("")
        
        # 整理代表項目的屬性（根據 target 不同區分）
        if args.target == "definition":
            representative_info = {
                "code": rep_code,
                "display_name": rep_display_name,
                "description": rep_candidate["description"],
                "base_definition": rep_candidate["base_definition"],
                "parameter": rep_raw_item.get("parameter", {}),
                "synonym_map": sorted(list(all_synonyms)),
                "level": "BASE"
            }
        else:
            representative_info = {
                "code": rep_code,
                "display_name": rep_display_name,
                "description": rep_candidate["description"],
                "base_definition": rep_candidate["base_definition"],
                "logic_structure": rep_raw_item.get("logic_structure"),
                "parameters": rep_raw_item.get("parameters"),
                "payment_type": rep_raw_item.get("payment_type"),
                "synonym_map": sorted(list(all_synonyms))
            }
            
        output_clusters.append({
            "cluster_id": cluster_id,
            "representative": representative_info,
            "members": members_info
        })
        
    # 決定輸出檔路徑
    if args.target == "definition":
        output_path = data_dir / "definitions" / "definition_clusters.json"
    else:
        output_path = data_dir / "claim_items" / "claim_item_clusters.json"
        
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_clusters, f, ensure_ascii=False, indent=2)
        
    print(f"聚類結果已成功寫入：{output_path}")
    print(f"{args.target} 語意聚類執行完成！")

if __name__ == "__main__":
    main()
