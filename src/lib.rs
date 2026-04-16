uniffi::include_scaffolding!("tokenixo");

use std::path::PathBuf;
use std::sync::OnceLock;
use std::io::Read;
use base64::{engine::general_purpose, Engine as _};
use rustc_hash::FxHashMap;

mod bpe;
use bpe::{CoreBPE, Rank};
use sentencepiece::SentencePieceProcessor;
use flate2::read::GzDecoder;

// ── Types ────────────────────────────────────────────────────────────────────

pub struct TokenSpan {
    pub start: u64,
    pub end: u64,
}

pub enum TokenizerKind {
    ChatGPT,
    Claude,
    Gemini,
}

// ── FFI health-check ─────────────────────────────────────────────────────────

pub fn ping() -> String {
    eprintln!("[tokenixo] ping() — FFI bridge alive");
    "ok".to_string()
}

// ── Asset resolution ─────────────────────────────────────────────────────────
//
// Priority:
//   1. TOKENIXO_ASSETS_DIR env var (override for testing)
//   2. <exe>/../Resources/assets  (macOS app bundle)
//   3. Compile-time CARGO_MANIFEST_DIR/assets (development)

fn assets_dir() -> PathBuf {
    if let Ok(p) = std::env::var("TOKENIXO_ASSETS_DIR") {
        return PathBuf::from(p);
    }
    if let Ok(exe) = std::env::current_exe() {
        let candidate = exe
            .parent().unwrap_or(&exe)   // MacOS/
            .parent().unwrap_or(&exe)   // Contents/
            .join("Resources/assets");
        eprintln!("[tokenixo] assets_dir: checking bundle candidate: {:?}", candidate);
        if candidate.exists() {
            eprintln!("[tokenixo] assets_dir: using bundle path");
            return candidate;
        }
    }
    let fallback = PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/assets"));
    eprintln!("[tokenixo] assets_dir: using compile-time fallback: {:?}", fallback);
    fallback
}

// ── Asset loader ─────────────────────────────────────────────────────────────
//
// Pass the EXACT filename as it appears in the bundle (e.g.
// "claude-tokenizer.json.gz").  This function:
//   1. Tries assets_dir()/name  (exact name — the release bundle has .gz files)
//   2. If name ends with ".gz" and the .gz wasn't found, strips the suffix and
//      tries the plain file (development builds where assets/ has originals).
//
// If the file name ends with ".gz", the bytes are decompressed before return.

fn read_asset(name: &str) -> Option<Vec<u8>> {
    let dir  = assets_dir();
    let path = dir.join(name);

    eprintln!("[tokenixo] read_asset: looking for {:?}", path);

    let raw_bytes: Vec<u8> = if path.exists() {
        match std::fs::read(&path) {
            Ok(b)  => b,
            Err(e) => {
                eprintln!("[tokenixo] read_asset: read failed: {e}");
                return None;
            }
        }
    } else if name.ends_with(".gz") {
        // Bundle file not found — try the plain (non-compressed) fallback used
        // in dev builds where assets/ contains the originals.
        let plain_name = &name[..name.len() - 3];
        let plain = dir.join(plain_name);
        eprintln!("[tokenixo] read_asset: .gz not found, trying plain {:?}", plain);
        match std::fs::read(&plain) {
            Ok(b)  => {
                eprintln!("[tokenixo] read_asset: read plain {} bytes from {:?}", b.len(), plain);
                // Plain file is not compressed — return as-is.
                return Some(b);
            }
            Err(e) => {
                eprintln!("[tokenixo] read_asset: plain read also failed: {e}");
                return None;
            }
        }
    } else {
        eprintln!("[tokenixo] read_asset: NOT FOUND: {name}");
        return None;
    };

    // Decompress if the name indicates gzip.
    if name.ends_with(".gz") {
        eprintln!("[tokenixo] read_asset: decompressing {} ({} compressed bytes) …", name, raw_bytes.len());
        let mut decoder = GzDecoder::new(raw_bytes.as_slice());
        let mut out = Vec::new();
        match decoder.read_to_end(&mut out) {
            Ok(_)  => {
                eprintln!("[tokenixo] read_asset: decompressed {} → {} bytes", name, out.len());
                Some(out)
            }
            Err(e) => {
                eprintln!("[tokenixo] read_asset: decompress failed: {e}");
                None
            }
        }
    } else {
        eprintln!("[tokenixo] read_asset: read {} bytes from {:?}", raw_bytes.len(), path);
        Some(raw_bytes)
    }
}

// ── ChatGPT — cl100k_base read from the app bundle ───────────────────────────

const CL100K_PAT: &str =
    "'(?i:[sdmt]|ll|ve|re)|[^\\r\\n\\p{L}\\p{N}]?+\\p{L}++|\\p{N}{1,3}+| ?[^\\s\\p{L}\\p{N}]++[\\r\\n]*+|\\s++$|\\s*[\\r\\n]|\\s+(?!\\S)|\\s";

fn chatgpt_bpe_from_bytes(bytes: &[u8]) -> Result<CoreBPE, Box<dyn std::error::Error>> {
    let text = std::str::from_utf8(bytes)?;
    let mut encoder: FxHashMap<Vec<u8>, Rank> = FxHashMap::default();
    for line in text.lines() {
        let mut parts = line.split(' ');
        let raw  = parts.next().ok_or("missing token")?;
        let rank: Rank = parts.next().ok_or("missing rank")?.trim().parse()?;
        encoder.insert(general_purpose::STANDARD.decode(raw)?, rank);
    }
    let mut special: FxHashMap<String, Rank> = FxHashMap::default();
    special.insert("<|endoftext|>".into(), 100257);
    special.insert("<|fim_prefix|>".into(), 100258);
    special.insert("<|fim_middle|>".into(), 100259);
    special.insert("<|fim_suffix|>".into(), 100260);
    special.insert("<|endofprompt|>".into(), 100276);
    Ok(CoreBPE::new(encoder, special, CL100K_PAT)?)
}

fn chatgpt_spans(text: &str) -> Vec<TokenSpan> {
    static BPE: OnceLock<Option<CoreBPE>> = OnceLock::new();
    let slot = BPE.get_or_init(|| {
        eprintln!("[tokenixo] chatgpt: loading cl100k_base …");
        let bytes = read_asset("cl100k_base.tiktoken")?;
        match chatgpt_bpe_from_bytes(&bytes) {
            Ok(bpe) => { eprintln!("[tokenixo] chatgpt: OK"); Some(bpe) }
            Err(e)  => { eprintln!("[tokenixo] chatgpt: FAILED: {e}"); None }
        }
    });
    let Some(bpe) = slot else { return vec![]; };

    let token_ids = bpe.encode_ordinary(text);
    let mut spans = Vec::with_capacity(token_ids.len());
    let mut cursor: usize = 0;
    for &id in &token_ids {
        if let Ok(bytes) = bpe.decode_bytes(&[id]) {
            let len = bytes.len();
            spans.push(TokenSpan { start: cursor as u64, end: (cursor + len) as u64 });
            cursor += len;
        }
    }
    eprintln!("[tokenixo] chatgpt: {} spans", spans.len());
    spans
}

// ── Claude — tokenizer JSON read from the app bundle ─────────────────────────

fn claude_spans(text: &str) -> Vec<TokenSpan> {
    static TOK: OnceLock<Option<tokenizers::Tokenizer>> = OnceLock::new();

    let slot = TOK.get_or_init(|| {
        eprintln!("[tokenixo] claude: loading tokenizer …");
        let bytes = read_asset("claude-tokenizer.json")?;
        match tokenizers::Tokenizer::from_bytes(&bytes) {
            Ok(tok) => { eprintln!("[tokenixo] claude: OK"); Some(tok) }
            Err(e)  => { eprintln!("[tokenixo] claude: from_bytes FAILED: {e}"); None }
        }
    });
    let Some(tokenizer) = slot else { return vec![]; };

    match tokenizer.encode(text, false) {
        Ok(enc) => {
            let spans: Vec<TokenSpan> = enc.get_offsets().iter()
                .map(|&(s, e)| TokenSpan { start: s as u64, end: e as u64 })
                .collect();
            eprintln!("[tokenixo] claude: {} spans", spans.len());
            spans
        }
        Err(e) => { eprintln!("[tokenixo] claude: encode FAILED: {e}"); vec![] }
    }
}

// ── Gemini — sentencepiece ────────────────────────────────────────────────────
//
// The bundle stores "gemini.model.gz".  SentencePieceProcessor::open() needs a
// file path, so we decompress once into ~/Library/Caches/Tokenixo/gemini.model
// and load from there.  Subsequent launches reuse the cached file.

fn gemini_model_path() -> Option<PathBuf> {
    let cache_dir = dirs::cache_dir()?.join("Tokenixo");
    if let Err(e) = std::fs::create_dir_all(&cache_dir) {
        eprintln!("[tokenixo] gemini: cache dir failed: {e}");
    }
    let cached = cache_dir.join("gemini.model");
    if cached.exists() {
        eprintln!("[tokenixo] gemini: cache hit {:?}", cached);
        return Some(cached);
    }

    // Decompress from bundle (exact .gz filename) or plain dev asset.
    let bytes = read_asset("gemini.model.gz")?;
    match std::fs::write(&cached, &bytes) {
        Ok(_)  => { eprintln!("[tokenixo] gemini: wrote {} bytes to cache", bytes.len()); Some(cached) }
        Err(e) => {
            eprintln!("[tokenixo] gemini: cache write failed: {e}");
            // Fallback: temp file so this launch still works.
            let tmp = std::env::temp_dir().join("tokenixo-gemini.model");
            std::fs::write(&tmp, &bytes).ok()?;
            eprintln!("[tokenixo] gemini: using temp {:?}", tmp);
            Some(tmp)
        }
    }
}

fn gemini_spans(text: &str) -> Vec<TokenSpan> {
    static SPP: OnceLock<Option<SentencePieceProcessor>> = OnceLock::new();

    let slot = SPP.get_or_init(|| {
        eprintln!("[tokenixo] gemini: loading SPM …");
        let path = gemini_model_path()?;
        match SentencePieceProcessor::open(&path) {
            Ok(spp) => { eprintln!("[tokenixo] gemini: loaded OK"); Some(spp) }
            Err(e)  => { eprintln!("[tokenixo] gemini: open FAILED: {e}"); None }
        }
    });
    let Some(spp) = slot else { return vec![]; };

    match spp.encode(text) {
        Ok(pieces) => {
            let mut spans = Vec::with_capacity(pieces.len());
            let mut cursor: usize = 0;
            for piece in &pieces {
                let surface: String = piece.piece.replace('\u{2581}', " ");
                let sbytes = surface.as_bytes();
                let slen = if cursor == 0 && sbytes.first() == Some(&b' ') {
                    sbytes.len().saturating_sub(1)
                } else {
                    sbytes.len()
                };
                spans.push(TokenSpan { start: cursor as u64, end: (cursor + slen) as u64 });
                cursor += slen;
            }
            eprintln!("[tokenixo] gemini: {} spans", spans.len());
            spans
        }
        Err(e) => { eprintln!("[tokenixo] gemini: encode FAILED: {e}"); vec![] }
    }
}

// ── Public API ───────────────────────────────────────────────────────────────

pub fn tokenize(text: String, kind: TokenizerKind) -> Vec<TokenSpan> {
    eprintln!("[tokenixo] tokenize() text len={}", text.len());
    match kind {
        TokenizerKind::ChatGPT => chatgpt_spans(&text),
        TokenizerKind::Claude  => claude_spans(&text),
        TokenizerKind::Gemini  => gemini_spans(&text),
    }
}

pub fn count_tokens(text: String, kind: TokenizerKind) -> u64 {
    tokenize(text, kind).len() as u64
}

pub fn available_tokenizers() -> Vec<TokenizerKind> {
    vec![TokenizerKind::ChatGPT, TokenizerKind::Claude, TokenizerKind::Gemini]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chatgpt_hello_claude() {
        let spans = chatgpt_spans("Hello, Claude.");
        assert_eq!(spans.len(), 4, "expected 4 tokens for 'Hello, Claude.'");
    }

    #[test]
    fn claude_hello() {
        let spans = claude_spans("Hello, Claude.");
        assert!(!spans.is_empty(), "Claude tokenizer returned empty");
    }

    #[test]
    fn gemini_hello() {
        let spans = gemini_spans("Hello, Claude.");
        assert!(!spans.is_empty(), "Gemini tokenizer returned empty");
    }
}
