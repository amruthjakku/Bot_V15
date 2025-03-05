#!/bin/bash
cd interfaces/web && gunicorn app:app &
cd ../telegram && python bot.py

