import json
import os

path = r'c:\Users\User\Downloads\保費試算\data\definitions\base.json'
with open(path, 'r', encoding='utf-8') as f:
    data = json.load(f)

mapping = {
    'INS_ACT_ART_3': 'POLICY_HOLDER',
    'INS_ACT_ART_4': 'INSURED',
    'INS_ACT_ART_5': 'BENEFICIARY',
    'INS_ACT_ART_1_3': 'PREMIUM',
    'INS_ACT_ART_1_4_5': 'INSURANCE_ACCIDENT',
    'INS_ACT_ART_116': 'GRACE_PERIOD',
    'INS_ACT_ART_116_3': 'REINSTATEMENT',
    'INS_ACT_ART_119': 'SURRENDER_VALUE',
    'INS_ACT_ART_72': 'SUM_INSURED',
    'POLICYHOLDER': 'POLICY_HOLDER',
    'INSURED_EVENT': 'INSURANCE_ACCIDENT'
}

new_data = []
seen_codes = set()

for d in data:
    old_code = d['code']
    new_code = mapping.get(old_code, old_code)
    
    # 如果代碼重複，保留較豐富的內容 (通常是後來加入的有 description)
    if new_code in seen_codes:
        # 尋找已存在的項並更新 (簡單起見，這裡直接跳過重複的，但優先保留法律定義)
        continue
    
    d['code'] = new_code
    new_data.append(d)
    seen_codes.add(new_code)

with open(path, 'w', encoding='utf-8') as f:
    json.dump(new_data, f, ensure_ascii=False, indent=2)

print("base.json 整理完成。")
