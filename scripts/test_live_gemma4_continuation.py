import requests
import json
import time

import os

PORT = os.environ.get("PORT") or "8095"
PROXY_URL = os.environ.get("STARAGENT_HTTP_URL") or os.environ.get("MACAGENT_HTTP_URL") or f"http://127.0.0.1:{PORT}"
AUTH_HEADER = {"Authorization": "Bearer local-dev-key", "Content-Type": "application/json"}

def test_continuation_fidelity():
    print("\n[TEST] Continuation Fidelity (V5)")
    print("-" * 60)
    
    # turn 1: trigger agent to inspect folder
    payload1 = {
        "messages": [{"role": "user", "content": "Inspect the app folder and identify the main entry point."}],
        "conversation_id": "test-cont-fidelity",
        "stream": False
    }
    
    print("1. Sending initial inspection task...")
    resp1 = requests.post(f"{PROXY_URL}/v1/chat/completions", json=payload1, headers=AUTH_HEADER)
    print(f"   Status: {resp1.status_code}")
    data1 = resp1.json()
    
    # check for partial_complete or anchored response
    print(f"   Response: {data1.get('choices', [{}])[0].get('message', {}).get('content', '')[:100]}...")
    
    # turn 2: send 'continue'
    payload2 = {
        "messages": [{"role": "user", "content": "continue"}],
        "conversation_id": "test-cont-fidelity",
        "stream": False
    }
    
    print("\n2. Sending 'continue' to verify focus persistence...")
    resp2 = requests.post(f"{PROXY_URL}/v1/chat/completions", json=payload2, headers=AUTH_HEADER)
    print(f"   Status: {resp2.status_code}")
    data2 = resp2.json()
    print(f"   Continuation outcome: {data2.get('choices', [{}])[0].get('message', {}).get('content', '')[:200]}...")

if __name__ == "__main__":
    test_continuation_fidelity()
