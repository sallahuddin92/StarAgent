#!/usr/bin/env python3
"""
SQLite Failure Point Investigation
Minimal test to isolate exact failure mechanism
"""

import sqlite3
import json
import time
from pathlib import Path
from sqlalchemy import create_engine, Column, String, Integer, Text, event
from sqlalchemy.orm import declarative_base, sessionmaker

# Clean start
db_path = Path("/tmp/test_sqlite.db")
if db_path.exists():
    db_path.unlink()

print("\n" + "="*70)
print("SQLITE FAILURE POINT INVESTIGATION")
print("="*70)

# Test 1: Basic SQLite functionality
print("\n[TEST 1] Raw SQLite - Sequential Operations")
print("-" * 70)

try:
    conn = sqlite3.connect(str(db_path))
    cursor = conn.cursor()
    
    # Create table
    cursor.execute("""
        CREATE TABLE test_conv (
            id TEXT PRIMARY KEY,
            project_id TEXT,
            data TEXT
        )
    """)
    conn.commit()
    
    # Insert record 1
    cursor.execute("INSERT INTO test_conv VALUES (?, ?, ?)", ("conv1", "proj1", '{"test": 1}'))
    conn.commit()
    print("✓ Insert 1: OK")
    
    # Insert record 2
    cursor.execute("INSERT INTO test_conv VALUES (?, ?, ?)", ("conv2", "proj1", '{"test": 2}'))
    conn.commit()
    print("✓ Insert 2: OK")
    
    # Query both
    cursor.execute("SELECT COUNT(*) FROM test_conv")
    count = cursor.fetchone()[0]
    print(f"✓ Query: {count} records")
    
    conn.close()
    print("✅ RAW SQLITE: PASS")
    
except Exception as e:
    print(f"❌ RAW SQLITE: FAIL - {e}")

# Test 2: SQLAlchemy with StaticPool (original config)
print("\n[TEST 2] SQLAlchemy with StaticPool - Sequential Operations")
print("-" * 70)

db_path.unlink(missing_ok=True)

try:
    from sqlalchemy.pool import StaticPool
    
    Base = declarative_base()
    
    class TestConv(Base):
        __tablename__ = "test_conv"
        id = Column(String, primary_key=True)
        project_id = Column(String)
        data = Column(Text)
    
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        echo=False
    )
    
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    # Operation 1
    session = Session()
    session.add(TestConv(id="conv1", project_id="proj1", data='{"test": 1}'))
    session.commit()
    session.close()
    print("✓ Insert 1: OK")
    
    # Operation 2
    session = Session()
    session.add(TestConv(id="conv2", project_id="proj1", data='{"test": 2}'))
    session.commit()
    session.close()
    print("✓ Insert 2: OK")
    
    # Query
    session = Session()
    count = session.query(TestConv).count()
    session.close()
    print(f"✓ Query: {count} records")
    
    print("✅ STATICPOOL: PASS")
    
except Exception as e:
    print(f"❌ STATICPOOL: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 3: SQLAlchemy with QueuePool (current config)
print("\n[TEST 3] SQLAlchemy with QueuePool - Sequential Operations")
print("-" * 70)

db_path.unlink(missing_ok=True)

try:
    from sqlalchemy.pool import QueuePool
    
    Base = declarative_base()
    
    class TestConv(Base):
        __tablename__ = "test_conv"
        id = Column(String, primary_key=True)
        project_id = Column(String)
        data = Column(Text)
    
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 15.0
        },
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=5,
        echo=False
    )
    
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    # Operation 1
    session = Session()
    session.add(TestConv(id="conv1", project_id="proj1", data='{"test": 1}'))
    session.commit()
    session.close()
    print("✓ Insert 1: OK")
    
    # Operation 2
    session = Session()
    session.add(TestConv(id="conv2", project_id="proj1", data='{"test": 2}'))
    session.commit()
    session.close()
    print("✓ Insert 2: OK")
    
    # Query
    session = Session()
    count = session.query(TestConv).count()
    session.close()
    print(f"✓ Query: {count} records")
    
    print("✅ QUEUEPOOL: PASS")
    
except Exception as e:
    print(f"❌ QUEUEPOOL: FAIL - {e}")
    import traceback
    traceback.print_exc()

# Test 4: WAL Mode
print("\n[TEST 4] SQLAlchemy with WAL Mode - Sequential Operations")
print("-" * 70)

db_path.unlink(missing_ok=True)

try:
    from sqlalchemy.pool import QueuePool
    
    Base = declarative_base()
    
    class TestConv(Base):
        __tablename__ = "test_conv"
        id = Column(String, primary_key=True)
        project_id = Column(String)
        data = Column(Text)
    
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={
            "check_same_thread": False,
            "timeout": 15.0
        },
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=5,
        echo=False
    )
    
    # Enable WAL
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_conn, connection_record):
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute("PRAGMA busy_timeout=15000")
        cursor.close()
    
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    
    # Operation 1
    session = Session()
    session.add(TestConv(id="conv1", project_id="proj1", data='{"test": 1}'))
    session.commit()
    session.close()
    print("✓ Insert 1: OK")
    
    # Operation 2
    session = Session()
    session.add(TestConv(id="conv2", project_id="proj1", data='{"test": 2}'))
    session.commit()
    session.close()
    print("✓ Insert 2: OK")
    
    # Query
    session = Session()
    count = session.query(TestConv).count()
    session.close()
    print(f"✓ Query: {count} records")
    
    print("✅ WAL MODE: PASS")
    
except Exception as e:
    print(f"❌ WAL MODE: FAIL - {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*70)
print("SUMMARY")
print("="*70)
print("All isolated tests should PASS if components work individually")
print("If any FAIL, the issue is in that specific component")
