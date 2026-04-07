#!/bin/bash
pip install pyinstaller tiktoken tokenizers
pyinstaller --onefile --windowed --name Tokenixo Tokenixo.py
echo "Done! Your app is in dist/Tokenixo.app"