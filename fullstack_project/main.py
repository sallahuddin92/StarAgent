from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import create_engine, Column, Integer, String
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.exc import SQLAlchemyError
import json
from typing import List
app = FastAPI()
# SQLite Setup
DATABASE_URL = "sqlite:///./data.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine)
# Database Model
class Item(Base):
__tablename__ = "items"
id = Column(Integer, primary_key=True, index=True)
name = Column(String, index=True)
description = Column(String)
# Table Initialization
Base = SessionLocal
try:
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()
Base.metadata.create_all(bind=engine)
except ImportError:
pass
# CRUD Operations
def get_db():
db = SessionLocal()
try:
yield db
finally:
db.close()
@app.on_event("startup")
def startup_event():
# Ensure tables exist (already handled by declarative_base, but good practice)
pass
# CORS Middleware
app.add_middleware(
CORSMiddleware,
allow_origins=["*"],
allow_credentials=True,
allow_methods=["*"],
allow_headers=["*"],