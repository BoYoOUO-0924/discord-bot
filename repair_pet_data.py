import json
import os

DATA_FILE = 'data/pet.json'

def repair():
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        new_data = {}
        fixed_count = 0
        
        for k, v in data.items():
            clean_key = k.strip().replace('\n', '').replace('\r', '').replace('"', '').replace("'", "")
            # Ensure it's digits
            if not clean_key.isdigit():
                 print(f"Skipping non-digit key: {repr(k)}")
                 continue
                 
            if k != clean_key:
                print(f"Repaired key: {repr(k)} -> {repr(clean_key)}")
                fixed_count += 1
            
            new_data[clean_key] = v
            
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(new_data, f, ensure_ascii=False, indent=2)
            
        print(f"Repair complete. Fixed {fixed_count} keys.")
        print("Keys:", list(new_data.keys()))
        
    except Exception as e:
        print(f"Repair failed: {e}")

if __name__ == "__main__":
    repair()
