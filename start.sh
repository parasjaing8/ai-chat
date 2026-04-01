#!/bin/bash
set -e
cd ~/ai-chat
source .venv/bin/activate
exec uvicorn server:app --host 0.0.0.0 --port 8080
