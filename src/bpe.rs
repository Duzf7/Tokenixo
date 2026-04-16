// Vendored from tiktoken-rs (MIT licence), trimmed to only the BPE engine.
// Removed: all include_str! vocab embedding, Python bindings, unstable-token
// logic, and helpers we don't use.  No vocab data lives in this file.

use std::num::NonZeroU64;
use std::thread;

use fancy_regex::Regex;
use rustc_hash::FxHashMap as HashMap;

pub type Rank = u32;

// ── BPE merge algorithm ───────────────────────────────────────────────────────

fn _byte_pair_merge(ranks: &HashMap<Vec<u8>, Rank>, piece: &[u8]) -> Vec<(usize, Rank)> {
    let mut parts = Vec::with_capacity(piece.len() + 1);
    let mut min_rank: (Rank, usize) = (Rank::MAX, usize::MAX);
    for i in 0..piece.len() - 1 {
        let rank = *ranks.get(&piece[i..i + 2]).unwrap_or(&Rank::MAX);
        if rank < min_rank.0 {
            min_rank = (rank, i);
        }
        parts.push((i, rank));
    }
    parts.push((piece.len() - 1, Rank::MAX));
    parts.push((piece.len(), Rank::MAX));

    let get_rank = |parts: &Vec<(usize, Rank)>, i: usize| {
        if (i + 3) < parts.len() {
            *ranks
                .get(&piece[parts[i].0..parts[i + 3].0])
                .unwrap_or(&Rank::MAX)
        } else {
            Rank::MAX
        }
    };

    while min_rank.0 != Rank::MAX {
        let i = min_rank.1;
        if i > 0 {
            parts[i - 1].1 = get_rank(&parts, i - 1);
        }
        parts[i].1 = get_rank(&parts, i);
        parts.remove(i + 1);

        min_rank = (Rank::MAX, usize::MAX);
        for (i, &(_, rank)) in parts[..parts.len() - 1].iter().enumerate() {
            if rank < min_rank.0 {
                min_rank = (rank, i);
            }
        }
    }
    parts
}

fn byte_pair_encode(piece: &[u8], ranks: &HashMap<Vec<u8>, Rank>) -> Vec<Rank> {
    if piece.len() == 1 {
        return vec![ranks[piece]];
    }
    _byte_pair_merge(ranks, piece)
        .windows(2)
        .map(|part| ranks[&piece[part[0].0..part[1].0]])
        .collect()
}

// ── Thread-local regex slots ──────────────────────────────────────────────────

struct FakeThreadId(NonZeroU64);

fn hash_current_thread() -> usize {
    const _: [u8; 8] = [0; std::mem::size_of::<std::thread::ThreadId>()];
    const _: [u8; 8] = [0; std::mem::size_of::<FakeThreadId>()];
    let x = unsafe {
        std::mem::transmute::<std::thread::ThreadId, FakeThreadId>(thread::current().id()).0
    };
    u64::from(x) as usize
}

const MAX_NUM_THREADS: usize = 128;

// ── CoreBPE ───────────────────────────────────────────────────────────────────

#[derive(Clone)]
pub struct CoreBPE {
    encoder:                HashMap<Vec<u8>, Rank>,
    #[allow(dead_code)]
    special_tokens_encoder: HashMap<String, Rank>,
    decoder:                HashMap<Rank, Vec<u8>>,
    special_tokens_decoder: HashMap<Rank, Vec<u8>>,
    regex_tls:              Vec<Regex>,
    #[allow(dead_code)]
    special_regex_tls:      Vec<Regex>,
    #[allow(dead_code)]
    sorted_token_bytes:     Vec<Vec<u8>>,
}

#[derive(Debug)]
pub struct DecodeKeyError(pub Rank);
impl std::fmt::Display for DecodeKeyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "invalid token for decoding: {}", self.0)
    }
}
impl std::error::Error for DecodeKeyError {}

impl CoreBPE {
    pub fn new(
        encoder: HashMap<Vec<u8>, Rank>,
        special_tokens_encoder: HashMap<String, Rank>,
        pattern: &str,
    ) -> Result<Self, Box<dyn std::error::Error>> {
        let regex = Regex::new(pattern)?;
        let special_regex = {
            let parts: Vec<_> = special_tokens_encoder
                .keys()
                .map(|s| fancy_regex::escape(s))
                .collect();
            Regex::new(&parts.join("|"))?
        };
        let decoder: HashMap<Rank, Vec<u8>> =
            encoder.iter().map(|(k, v)| (*v, k.clone())).collect();
        assert_eq!(
            encoder.len(),
            decoder.len(),
            "duplicate token indices in encoder"
        );
        let special_tokens_decoder: HashMap<Rank, Vec<u8>> = special_tokens_encoder
            .iter()
            .map(|(k, v)| (*v, k.as_bytes().to_vec()))
            .collect();
        let mut sorted_token_bytes: Vec<Vec<u8>> = encoder.keys().cloned().collect();
        sorted_token_bytes.sort();

        Ok(Self {
            encoder,
            special_tokens_encoder,
            decoder,
            special_tokens_decoder,
            regex_tls: (0..MAX_NUM_THREADS).map(|_| regex.clone()).collect(),
            special_regex_tls: (0..MAX_NUM_THREADS).map(|_| special_regex.clone()).collect(),
            sorted_token_bytes,
        })
    }

    fn _get_tl_regex(&self) -> &Regex {
        &self.regex_tls[hash_current_thread() % MAX_NUM_THREADS]
    }

    pub fn encode_ordinary(&self, text: &str) -> Vec<Rank> {
        let regex = self._get_tl_regex();
        let mut ret = vec![];
        for mat in regex.find_iter(text) {
            let piece = mat.unwrap().as_str().as_bytes();
            match self.encoder.get(piece) {
                Some(token) => ret.push(*token),
                None => ret.extend(&byte_pair_encode(piece, &self.encoder)),
            }
        }
        ret
    }

    pub fn decode_bytes(&self, tokens: &[Rank]) -> Result<Vec<u8>, DecodeKeyError> {
        let mut ret = Vec::with_capacity(tokens.len() * 2);
        for &token in tokens {
            let bytes = self
                .decoder
                .get(&token)
                .or_else(|| self.special_tokens_decoder.get(&token))
                .ok_or(DecodeKeyError(token))?;
            ret.extend(bytes);
        }
        Ok(ret)
    }
}
