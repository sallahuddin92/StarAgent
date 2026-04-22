#!/usr/bin/env python3
"""
SQLite Root Cause Investigation
Systematically tests each potential failure cause
"""

import os
import sys
import subprocess
import time
import json
import sqlite3
import requests
import threading
from pathlib import Path

# Configuration
REPO_ROOT = Path(__file__).resolve().parent
PROXY_URL = os.environ.get("STARAGENT_HTTP_URL") or os.environ.get("MACAGENT_HTTP_URL") or "http://127.0.0.1:8095"
_API_KEY = os.environ.get("STARAGENT_API_KEY") or os.environ.get("MACAGENT_API_KEY") or os.environ.get("PROXY_API_KEY") or "local-dev-key"
AUTH_HEADER = {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}
DB_PATH = REPO_ROOT / "data" / "memory.db"

class Investigation:
    def __init__(self):
        self.findings = []
        self.server_log = Path("/tmp/sqlite_investigation.log")
        
    def log_finding(self, category, test, result, evidence):
        """Log a finding with evidence"""
        self.findings.append({
            "category": category,
            "test": test,
            "result": result,
            "evidence": evidence
        })
        print(f"\n[{category}] {test}")
        print(f"  Result: {result}")
        print(f"  Evidence: {evidence[:200]}...")
        
    def start_server(self, config_name="default"):
        """Start server with given configuration"""
        print(f"\n{'='*70}")
        print(f"STARTING SERVER: {config_name}")
        print(f"{'='*70}")
        
        # Kill existing server
        subprocess.run(["pkill", "-9", "-f", "uvicorn"], stderr=subprocess.DEVNULL)
        time.sleep(2)
        
        # Clean database
        subprocess.run(["rm", "-rf", str(DB_PATH.parent / "memory.db*")], stderr=subprocess.DEVNULL)
        time.sleep(1)
        
        # Start new server
        os.chdir(str(REPO_ROOT))
        with open(self.server_log, "w") as log_file:
            subprocess.Popen(
                ["python3", "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(int(os.environ.get("PORT", "8095")))],
                stdout=log_file,
                stderr=log_file
            )
        
        time.sleep(8)
        
        # Verify health
        try:
            resp = requests.get(f"{PROXY_URL}/health", timeout=5)
            if resp.status_code == 200:
                print("✅ Server started successfully")
                return True
        except:
            pass
        
        print("❌ Server failed to start")
        print(f"Logs: {self.server_log.read_text()[-500:]}")
        return False
    
    def test_sequential_requests(self, count=3):
        """Test N sequential requests"""
        print(f"\n[TEST] Sequential Requests ({count})")
        
        results = []
        for i in range(count):
            payload = {
                "model": "gemma4:e2b",
                "messages": [{"role": "user", "content": f"Sequential request {i+1}"}]
            }
            
            try:
                resp = requests.post(f"{PROXY_URL}/v1/chat/completions", json=payload, headers=AUTH_HEADER, timeout=10)
                results.append((i+1, resp.status_code, "OK" if resp.status_code == 200 else resp.text[:100]))
                print(f"  Request {i+1}: HTTP {resp.status_code}")
            except Exception as e:
                results.append((i+1, None, str(e)[:100]))
                print(f"  Request {i+1}: EXCEPTION {str(e)[:50]}")
            
            time.sleep(0.5)
        
        succeeded = sum(1 for _, status, _ in results if status == 200)
        self.log_finding(
            "Access Pattern",
            "Sequential Requests",
            f"{succeeded}/{count} succeeded",
            json.dumps(results)
        )
        return succeeded == count
    
    def test_concurrent_requests(self, count=3):
        """Test N concurrent requests"""
        print(f"\n[TEST] Concurrent Requests ({count})")
        
        results = []
        lock = threading.Lock()
        
        def make_request(req_id):
            payload = {
                "model": "gemma4:e2b",
                "messages": [{"role": "user", "content": f"Concurrent {req_id}"}]
            }
            try:
                resp = requests.post(f"{PROXY_URL}/v1/chat/completions", json=payload, headers=AUTH_HEADER, timeout=10)
                status = resp.status_code
                data = "OK" if status == 200 else resp.text[:100]
            except Exception as e:
                status = None
                data = str(e)[:100]
            
            with lock:
                results.append((req_id, status, data))
                print(f"  Request {req_id}: HTTP {status}")
        
        threads = [threading.Thread(target=make_request, args=(i+1,)) for i in range(count)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        
        succeeded = sum(1 for _, status, _ in results if status == 200)
        self.log_finding(
            "Access Pattern",
            "Concurrent Requests",
            f"{succeeded}/{count} succeeded",
            json.dumps(results)
        )
        return succeeded >= count - 1  # Allow 1 failure for concurrency stress
    
    def check_server_logs_for_error(self):
        """Extract exact error from server logs"""
        print(f"\n[CHECK] Server Logs for Error Details")
        
        logs = self.server_log.read_text()
        
        # Find OperationalError
        if "OperationalError" in logs:
            # Extract context
            lines = logs.split('\n')
            for i, line in enumerate(lines):
                if "OperationalError" in line:
                    context = '\n'.join(lines[max(0, i-5):min(len(lines), i+10)])
                    print(f"Found OperationalError:\n{context}")
                    self.log_finding(
                        "Error Diagnosis",
                        "Server OperationalError",
                        "FOUND",
                        context
                    )
                    return True
        
        # Find disk I/O error
        if "disk I/O" in logs:
            lines = logs.split('\n')
            for i, line in enumerate(lines):
                if "disk I/O" in line:
                    context = '\n'.join(lines[max(0, i-3):min(len(lines), i+5)])
                    print(f"Found disk I/O error:\n{context}")
                    self.log_finding(
                        "Error Diagnosis",
                        "Disk I/O Error",
                        "FOUND",
                        context
                    )
                    return True
        
        print("No OperationalError or disk I/O error in logs")
        self.log_finding(
            "Error Diagnosis",
            "Server Errors",
            "NOT FOUND",
            "No OperationalError detected in logs"
        )
        return False
    
    def check_database_state(self):
        """Check database file state"""
        print(f"\n[CHECK] Database File State")
        
        if not DB_PATH.exists():
            self.log_finding("File State", "Database Exists", "NO", "No database file found")
            return False
        
        size = DB_PATH.stat().st_size
        print(f"Database file size: {size} bytes")
        
        # Check WAL files
        wal_files = list(DB_PATH.parent.glob("memory.db*"))
        print(f"Related files: {[f.name for f in wal_files]}")
        
        # Try to open database
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check")
            result = cursor.fetchone()[0]
            conn.close()
            
            self.log_finding(
                "File State",
                "Database Integrity",
                result,
                f"Size: {size} bytes, WAL files: {len(wal_files)}"
            )
            return result == "ok"
        except Exception as e:
            self.log_finding(
                "File State",
                "Database Integrity",
                "ERROR",
                str(e)
            )
            return False
    
    def check_database_schema(self):
        """Verify database schema"""
        print(f"\n[CHECK] Database Schema")
        
        try:
            conn = sqlite3.connect(str(DB_PATH))
            cursor = conn.cursor()
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = [row[0] for row in cursor.fetchall()]
            print(f"Tables: {tables}")
            
            cursor.execute("SELECT name FROM sqlite_master WHERE type='index'")
            indexes = [row[0] for row in cursor.fetchall()]
            print(f"Indexes: {indexes}")
            
            conn.close()
            
            self.log_finding(
                "Schema",
                "Database Tables",
                f"{len(tables)} tables, {len(indexes)} indexes",
                f"Tables: {tables}, Indexes: {indexes}"
            )
            return len(tables) >= 3
        except Exception as e:
            self.log_finding("Schema", "Database Tables", "ERROR", str(e))
            return False
    
    def save_report(self):
        """Save findings to JSON"""
        report_file = REPO_ROOT / "sqlite_investigation.json"
        with open(report_file, "w") as f:
            json.dump(self.findings, f, indent=2)
        print(f"\n✅ Investigation saved to: {report_file}")
        return report_file

def main():
    inv = Investigation()
    
    print("\n" + "="*70)
    print("SQLite ROOT CAUSE INVESTIGATION")
    print("="*70)
    
    # Phase 1: Reproduce with default config
    print("\n" + "="*70)
    print("PHASE 1: REPRODUCE WITH DEFAULT CONFIGURATION")
    print("="*70)
    
    if not inv.start_server("default"):
        print("❌ Server failed to start. Aborting.")
        return
    
    inv.check_database_state()
    inv.check_database_schema()
    
    # Test 1: Sequential requests
    print("\n" + "-"*70)
    seq_ok = inv.test_sequential_requests(3)
    
    # Test 2: Concurrent requests
    print("\n" + "-"*70)
    conc_ok = inv.test_concurrent_requests(3)
    
    # Check for errors
    inv.check_server_logs_for_error()
    
    # Phase 2: Summary
    print("\n" + "="*70)
    print("PHASE 1 SUMMARY")
    print("="*70)
    print(f"Sequential requests (3): {'✅ PASS' if seq_ok else '❌ FAIL'}")
    print(f"Concurrent requests (3): {'✅ PASS' if conc_ok else '❌ FAIL'}")
    
    # Save findings
    report_file = inv.save_report()
    
    print(f"\nFull investigation data: {report_file}")
    print("\nNext steps: Analyze report and decide on remediation")

if __name__ == "__main__":
    main()
