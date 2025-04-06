#!/bin/bash

cd "$(dirname "$0")"
source class-monitor/bin/activate
python main.py
deactivate
