#!/bin/bash
cd ~/whatsapp-agent
python3 agent.py >> agent.log 2>&1
bash push_results.sh >> agent.log 2>&1
