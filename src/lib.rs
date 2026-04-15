uniffi::include_scaffolding!("tokenixo");

use std::path::PathBuf;
use std::sync::OnceLock;
use tiktoken_rs::cl100k_base;
use sentencepiece::SentencePieceProcessor;

// ── Types ────────────────────────────────────────────────────────────────────

pub struct TokenSpan {
    pub start: u64,
    pub end: u64,
    pub index: u64,
}

pub enum TokenizerKind {
    ChatGPT,
    Claude,
    Gemini,
}

// ── FFI health-check ─────────────────────────────────────────────────────────

pub fn ping() -> String {
    eprintln!("[tokenixo] ping() called — FFI bridge is alive");
    "ok".to_string()
}

// ── Asset resolution ─────────────────────────────────────────────────────────
//
// Priority:
//   1. TOKENIXO_ASSETS_DIR env var (override for testing)
//   2. <exe>/../Resources/assets  (inside a macOS app bundle)
//   3. Compile-time CARGO_MANIFEST_DIR/assets (development builds)

fn assets_dir() -> PathBuf {
    if let Ok(p) = std::env::var("TOKENIXO_ASSETS_DIR") {
        let pb = PathBuf::from(&p);
        eprintln!("[tokenixo] assets_dir: env override → {}", pb.display());
        return pb;
    }
    if let Ok(exe) = std::env::current_exe() {
        // exe is at  .../Tokenixo.app/Contents/MacOS/Tokenixo
        // Resources is at .../Tokenixo.app/Contents/Resources/
        let candidate = exe
            .parent().unwrap_or(&exe)   // MacOS/
            .parent().unwrap_or(&exe)   // Contents/
            .join("Resources/assets");
        if candidate.exists() {
            eprintln!("[tokenixo] assets_dir: bundle path → {}", candidate.display());
            return candidate;
        }
        eprintln!(
            "[tokenixo] assets_dir: bundle candidate does not exist: {}",
            candidate.display()
        );
    }
    // Development fallback — baked in at compile time.
    let fallback = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/assets"));
    eprintln!("[tokenixo] assets_dir: compile-time fallback → {}", fallback.display());
    fallback
}

// ── Tiktoken cache → Application Support ────────────────────────────────────
//
// tiktoken-rs respects TIKTOKEN_CACHE_DIR.  Point it at a stable, writable
// directory so vocab files survive between launches without cluttering $HOME.

fn ensure_tiktoken_cache() {
    static ONCE: OnceLock<()> = OnceLock::new();
    ONCE.get_or_init(|| {
        if let Ok(existing) = std::env::var("TIKTOKEN_CACHE_DIR") {
            eprintln!("[tokenixo] tiktoken cache already set: {existing}");
            return;
        }
        let cache = dirs::data_local_dir()
            .unwrap_or_else(|| PathBuf::from("/tmp"))
            .join("Tokenixo/tiktoken-cache");
        if let Err(e) = std::fs::create_dir_all(&cache) {
            eprintln!("[tokenixo] tiktoken cache dir creation failed: {e}");
        } else {
            eprintln!("[tokenixo] tiktoken cache dir: {}", cache.display());
        }
        // Safety: OnceLock guarantees this closure executes exactly once.
        unsafe { std::env::set_var("TIKTOKEN_CACHE_DIR", &cache) };
    });
}

// ── ChatGPT — tiktoken-rs / cl100k_base ─────────────────────────────────────

fn chatgpt_spans(text: &str) -> Vec<TokenSpan> {
    ensure_tiktoken_cache();

    // Store Option<CoreBPE>: None means initialisation failed.
    // OnceLock ensures we only attempt loading once (and log the outcome).
    static BPE: OnceLock<Option<tiktoken_rs::CoreBPE>> = OnceLock::new();

    let slot = BPE.get_or_init(|| {
        eprintln!("[tokenixo] chatgpt: loading cl100k_base …");
        match cl100k_base() {
            Ok(bpe) => {
                eprintln!("[tokenixo] chatgpt: cl100k_base loaded OK");
                Some(bpe)
            }
            Err(e) => {
                eprintln!("[tokenixo] chatgpt: cl100k_base FAILED: {e}");
                None
            }
        }
    });

    let Some(bpe) = slot else {
        eprintln!("[tokenixo] chatgpt: BPE not available, returning []");
        return vec![];
    };

    let token_ids = bpe.encode_ordinary(text);
    eprintln!("[tokenixo] chatgpt: {:?} → {} tokens", text, token_ids.len());

    let mut spans = Vec::with_capacity(token_ids.len());
    let mut cursor: usize = 0;

    for (index, &id) in token_ids.iter().enumerate() {
        match bpe.decode_bytes(&[id]) {
            Ok(bytes) => {
                let len = bytes.len();
                spans.push(TokenSpan {
                    start: cursor as u64,
                    end: (cursor + len) as u64,
                    index: index as u64,
                });
                cursor += len;
            }
            Err(e) => {
                eprintln!("[tokenixo] chatgpt: decode_bytes failed for id {id}: {e}");
            }
        }
    }
    spans
}

// ── Claude — tokenizers / Xenova/claude-tokenizer ───────────────────────────
//
// tokenizer.json is downloaded into assets/ at build time by build.rs.

fn claude_spans(text: &str) -> Vec<TokenSpan> {
    static TOK: OnceLock<Option<tokenizers::Tokenizer>> = OnceLock::new();

    let slot = TOK.get_or_init(|| {
        let path = assets_dir().join("claude-tokenizer.json");
        eprintln!("[tokenixo] claude: loading tokenizer from {}", path.display());
        if !path.exists() {
            eprintln!("[tokenixo] claude: FILE NOT FOUND: {}", path.display());
            return None;
        }
        match tokenizers::Tokenizer::from_file(&path) {
            Ok(tok) => {
                eprintln!("[tokenixo] claude: tokenizer loaded OK");
                Some(tok)
            }
            Err(e) => {
                eprintln!("[tokenixo] claude: from_file FAILED: {e}");
                None
            }
        }
    });

    let Some(tokenizer) = slot else {
        eprintln!("[tokenixo] claude: tokenizer not available, returning []");
        return vec![];
    };

    match tokenizer.encode(text, false) {
        Ok(encoding) => {
            let offsets = encoding.get_offsets();
            eprintln!("[tokenixo] claude: {:?} → {} tokens", text, offsets.len());
            offsets
                .iter()
                .enumerate()
                .map(|(index, &(start, end))| TokenSpan {
                    start: start as u64,
                    end: end as u64,
                    index: index as u64,
                })
                .collect()
        }
        Err(e) => {
            eprintln!("[tokenixo] claude: encode FAILED: {e}");
            vec![]
        }
    }
}

// ── Gemini — sentencepiece / Gemma-2 ─────────────────────────────────────────
//
// tokenizer.model is downloaded into assets/ at build time by build.rs.

fn gemini_spans(text: &str) -> Vec<TokenSpan> {
    static SPP: OnceLock<Option<SentencePieceProcessor>> = OnceLock::new();

    let slot = SPP.get_or_init(|| {
        let path = assets_dir().join("gemini.model");
        eprintln!("[tokenixo] gemini: loading SPM from {}", path.display());
        if !path.exists() {
            eprintln!("[tokenixo] gemini: FILE NOT FOUND: {}", path.display());
            return None;
        }
        match SentencePieceProcessor::open(&path) {
            Ok(spp) => {
                eprintln!("[tokenixo] gemini: SPM loaded OK");
                Some(spp)
            }
            Err(e) => {
                eprintln!("[tokenixo] gemini: open FAILED: {e}");
                None
            }
        }
    });

    let Some(spp) = slot else {
        eprintln!("[tokenixo] gemini: SPM not available, returning []");
        return vec![];
    };

    match spp.encode(text) {
        Ok(pieces) => {
            eprintln!("[tokenixo] gemini: {:?} → {} pieces", text, pieces.len());
            let mut spans = Vec::with_capacity(pieces.len());
            let mut cursor: usize = 0;

            for (index, piece) in pieces.iter().enumerate() {
                // ▁ (U+2581) is the SentencePiece word-boundary marker; replace
                // with a literal space to recover surface bytes.
                let surface: String = piece.piece.replace('\u{2581}', " ");
                let sbytes = surface.as_bytes();

                let slen = if cursor == 0 && sbytes.first() == Some(&b' ') {
                    sbytes.len().saturating_sub(1)
                } else {
                    sbytes.len()
                };

                spans.push(TokenSpan {
                    start: cursor as u64,
                    end: (cursor + slen) as u64,
                    index: index as u64,
                });
                cursor += slen;
            }
            spans
        }
        Err(e) => {
            eprintln!("[tokenixo] gemini: encode FAILED: {e}");
            vec![]
        }
    }
}

// ── Public API (matches tokenixo.udl) ───────────────────────────────────────

pub fn tokenize(text: String, kind: TokenizerKind) -> Vec<TokenSpan> {
    eprintln!("[tokenixo] tokenize() called, text len={}", text.len());
    let result = match kind {
        TokenizerKind::ChatGPT => chatgpt_spans(&text),
        TokenizerKind::Claude  => claude_spans(&text),
        TokenizerKind::Gemini  => gemini_spans(&text),
    };
    eprintln!("[tokenixo] tokenize() returning {} spans", result.len());
    result
}

pub fn count_tokens(text: String, kind: TokenizerKind) -> u64 {
    tokenize(text, kind).len() as u64
}

pub fn available_tokenizers() -> Vec<TokenizerKind> {
    vec![
        TokenizerKind::ChatGPT,
        TokenizerKind::Claude,
        TokenizerKind::Gemini,
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chatgpt_hello_claude() {
        let spans = chatgpt_spans("Hello, Claude.");
        eprintln!("ChatGPT spans: {}", spans.len());
        for s in &spans { eprintln!("  [{},{}]", s.start, s.end); }
        assert_eq!(spans.len(), 4, "expected 4 tokens for 'Hello, Claude.'");
    }

    #[test]
    fn claude_hello() {
        let spans = claude_spans("Hello, Claude.");
        eprintln!("Claude spans: {}", spans.len());
        for s in &spans { eprintln!("  [{},{}]", s.start, s.end); }
        assert!(spans.len() > 0, "Claude tokenizer returned empty");
    }

    #[test]
    fn gemini_hello() {
        let spans = gemini_spans("Hello, Claude.");
        eprintln!("Gemini spans: {}", spans.len());
        for s in &spans { eprintln!("  [{},{}]", s.start, s.end); }
        assert!(spans.len() > 0, "Gemini tokenizer returned empty");
    }
}
