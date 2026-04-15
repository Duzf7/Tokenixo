# Tokenixo

## Description

Tokenixo is a native macOS SwiftUI application that tokenizes text in real time using three production tokenizers:

- **ChatGPT** — OpenAI cl100k_base BPE via [tiktoken-rs](https://github.com/zurawiki/tiktoken-rs)
- **Claude** — Xenova/claude-tokenizer via HuggingFace [tokenizers](https://github.com/huggingface/tokenizers)
- **Gemini** — SentencePiece approximation via [sentencepiece-rs](https://codeberg.org/danieldk/sentencepiece)

The tokenizer logic is written in Rust and exposed to Swift through a UniFFI-generated C FFI bridge. Each token is highlighted with an alternating colour palette directly in the text editor. A stats bar shows live token, character, word, and line counts. A collapsible panel shows context-window usage against six model limits.

## Requirements

- **macOS 14 Sonoma or later**
- **Rust** (stable toolchain) — install via [rustup](https://rustup.rs)
- **Swift** — included with Xcode Command Line Tools (`xcode-select --install`)
- **cmake** — required by the sentencepiece-rs build:
  ```
  brew install cmake
  ```
- **Homebrew** — https://brew.sh (used to install cmake)

Optional:
- `swiftformat` — auto-formats generated Swift bindings (`brew install swiftformat`)
- A HuggingFace account with the Gemma licence accepted, plus `HF_TOKEN` set in the environment, to use the exact Gemma-2 tokenizer instead of the T5 fallback.

## Build

```bash
make app
```

This runs four steps in order:

1. `cargo build --release` — compiles the Rust library (`libtokenixo.a` / `libtokenixo.dylib`) and the `uniffi-bindgen` helper binary. On first build, `build.rs` downloads the Claude and Gemini tokenizer vocab files into `assets/`.
2. `cargo run --bin uniffi-bindgen generate` — generates `generated/tokenixo.swift`, `generated/tokenixoFFI.h`, and `generated/tokenixoFFI.modulemap` from `src/tokenixo.udl`.
3. `swift build --configuration release` — compiles the SwiftUI app, linking against `libtokenixo`.
4. Assembles `Tokenixo.app` bundle with the executable, `Info.plist`, and the `assets/` directory under `Contents/Resources/assets/`.

To open the finished bundle:

```bash
open Tokenixo.app
# or
make run
```

To clean all build artefacts:

```bash
make clean
```

## Test

Run the Rust unit tests (covers all three tokenizers with "Hello, Claude." as a smoke-test input):

```bash
cargo test -- --nocapture
```

Expected output includes:

```
[tokenixo] chatgpt: "Hello, Claude." → 4 tokens
[tokenixo] claude: "Hello, Claude." → 4 tokens
[tokenixo] gemini: "Hello, Claude." → 5 pieces
test result: ok. 3 passed; 0 failed; 0 ignored
```

The Swift layer has no separate test target; exercise it by launching the app and typing text.

## Package

To build a distributable disk image:

```bash
make dmg
```

This creates `Tokenixo.dmg` containing the app bundle. (Requires the `make app` step to have succeeded first.)

## Project Structure

```
Tokenixo/
├── src/
│   ├── lib.rs              # Rust tokenizer logic (ChatGPT, Claude, Gemini) + UniFFI public API
│   ├── tokenixo.udl        # UniFFI interface definition — source of truth for the FFI contract
│   └── bin/
│       └── uniffi-bindgen.rs  # Thin binary that drives the UniFFI code-generator
├── Sources/
│   └── Tokenixo/
│       ├── main.swift      # App entry point (calls TokenixoApp.main())
│       ├── ContentView.swift  # SwiftUI UI: text editor, token highlighting, stats, context panel
│       └── tokenixo.swift  # Auto-generated UniFFI Swift bindings (do not edit)
├── generated/              # UniFFI output: tokenixo.swift, tokenixoFFI.h, tokenixoFFI.modulemap
│                           #   (regenerated on every build — excluded from git)
├── TokenixoFFI/
│   └── module.modulemap    # SPM systemLibrary wrapper exposing the C header to Swift
├── assets/                 # Tokenizer vocab files downloaded at build time by build.rs
│   ├── claude-tokenizer.json  # Xenova/claude-tokenizer (HuggingFace, ~1.7 MB)
│   └── gemini.model        # SentencePiece model for Gemini approximation (~773 KB)
├── build.rs                # Cargo build script: runs UniFFI scaffolding + downloads assets
├── cargo.toml              # Rust package manifest and dependencies
├── Cargo.lock              # Locked dependency versions
├── Package.swift           # Swift Package Manager manifest (TokenixoFFI + Tokenixo targets)
├── Makefile                # Top-level build orchestration (app, run, clean, dmg)
├── Info.plist              # macOS app bundle metadata
├── LICENSE                 # Project licence
├── TESTS.md                # Extended test notes
└── README.md               # This file
```

### Key design decisions

| Decision | Rationale |
|---|---|
| Rust tokenizers exposed via UniFFI | Keeps the heavy vocab-loading and BPE logic in Rust; Swift only handles UI |
| `@MainActor ObservableObject` for tokenizer state | Guarantees `@Published` updates fire on the main thread; plain `@State + Task` does not |
| Build-time vocab download in `build.rs` | Bundles assets into the app so no network access is needed at runtime |
| `systemLibrary` SPM target for C header | Required so Swift can resolve `RustBuffer`, `RustCallStatus`, and all FFI symbols |
| tiktoken cache → Application Support | Keeps OpenAI vocab files in a persistent, writable location across launches |
