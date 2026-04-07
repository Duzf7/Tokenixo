# Tokenixo

Offline desktop token counter built with Python and tkinter.

![License](https://img.shields.io/badge/license-MIT-blue)
![Platform](https://img.shields.io/badge/platform-Windows%20%7C%20macOS%20%7C%20Linux-lightgrey)

Tokenixo counts tokens in real time as you type, paste, or edit text. No API calls, no internet required — everything runs locally.

## Features

- **Real-time token counting** — updates as you type, paste, or delete
- **Tokenizer backends** — tiktoken (cl100k_base) or Xenova/claude-tokenizer
- **Text stats** — tokens, characters, words, lines, and characters per token
- **Context window usage** — live percentage bars for Claude Haiku 4.5 (200K), Sonnet 4.6 (1M), and Opus 4.6 (1M)
- **Color-coded token highlighting** — alternating background colors visualize token boundaries
- **Selection stats** — select text to see token count for just that portion, highlighted with high-contrast colors
- **File support** — open `.txt`, `.md`, `.py`, `.js`, `.ts`, `.json`, `.csv`, or any file directly

## Installation

```bash
git clone https://github.com/Duzf7/Tokenixo.git
cd Tokenixo
pip install -r requirements.txt
```

## Usage

```bash
python Tokenixo.py
```

## Dependencies

Python 3.8+ with tkinter (included with most Python installations).

At least one tokenizer backend is required:

| Backend | Install | Notes |
|---|---|---|
| tiktoken | `pip install tiktoken` | Recommended — faster, smaller |
| tokenizers | `pip install tokenizers` | HuggingFace backend, uses Xenova/claude-tokenizer |

Both can be installed simultaneously. Tokenixo will use whichever is available.

## License

MIT — see [LICENSE](LICENSE) for details.

## Author

[Duzf7](https://github.com/Duzf7)
