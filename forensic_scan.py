import os
import json
import time

HISTORY_BASE = os.path.expanduser("~/Library/Application Support/Code/User/History")

def scan_history():
    inventory = {}
    
    # List all subdirectories in history
    try:
        dirs = os.listdir(HISTORY_BASE)
    except Exception as e:
        print(f"Error listing history base: {e}")
        return {}

    for d in dirs:
        dir_path = os.path.join(HISTORY_BASE, d)
        if not os.path.isdir(dir_path):
            continue
            
        entries_file = os.path.join(dir_path, "entries.json")
        if not os.path.exists(entries_file):
            continue
            
        try:
            with open(entries_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                resource = data.get("resource", "")
                
                # Check if it belongs to our project
                if "macagent_proxy_starter" in resource:
                    entries = data.get("entries", [])
                    if not entries:
                        continue
                        
                    # Get latest entry
                    latest_entry = sorted(entries, key=lambda x: x.get("timestamp", 0), reverse=True)[0]
                    
                    # Store in inventory
                    # resource is usually file:///...
                    original_path = resource.replace("file://", "")
                    
                    history_file_path = os.path.join(dir_path, latest_entry["id"])
                    timestamp = latest_entry["timestamp"]
                    
                    # If multiple hashes for same file, keep latest
                    if original_path not in inventory or timestamp > inventory[original_path]["timestamp"]:
                        inventory[original_path] = {
                            "history_path": history_file_path,
                            "timestamp": timestamp,
                            "date": time.ctime(timestamp / 1000.0)
                        }
        except Exception as e:
            # print(f"Error processing {entries_file}: {e}")
            continue
            
    return inventory

if __name__ == "__main__":
    results = scan_history()
    print(json.dumps(results, indent=2))
    
    with open("forensic_inventory.json", "w") as f:
        json.dump(results, f, indent=2)
