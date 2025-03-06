#!/bin/bash
cd interfaces/web && gunicorn app:app &  
cd /opt/render/project/src/interfaces/telegram && python bot.py
