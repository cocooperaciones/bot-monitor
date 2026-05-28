#!/bin/bash
cd ~/whatsapp-agent
git add results.json
git commit -m "update results $(date '+%Y-%m-%d %H:%M')"
git push origin main
