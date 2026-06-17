#!/bin/bash
# Regression test: Simple coding task streaming without web research
export AUTO_APPROVE=true
echo "Running simple coding test..."
./scripts/staragent --project demo --conversation stream-test-$(date +%s) agent "Write a python script that prints hello" --stream
