use std::path::Path;
use std::process::Command;

fn main() {
    uniffi::generate_scaffolding("src/tokenixo.udl").unwrap();

    let assets = Path::new("assets");
    std::fs::create_dir_all(assets).expect("failed to create assets/");

    // ChatGPT / cl100k_base — publicly hosted by OpenAI, no auth required.
    download_if_missing(
        assets.join("cl100k_base.tiktoken").as_path(),
        &["https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"],
        true,
    );

    // Claude tokenizer — public Xenova mirror, no auth required.
    download_if_missing(
        assets.join("claude-tokenizer.json").as_path(),
        &["https://huggingface.co/Xenova/claude-tokenizer/resolve/main/tokenizer.json"],
        true,
    );

    // Gemini / Gemma-2 sentencepiece model.
    // Primary: google/gemma-2-2b (needs HF_TOKEN + accepted Gemma licence).
    // Fallback: google-t5/t5-base spiece.model (public, same sentencepiece
    //           format, good vocabulary approximation for Gemini).
    download_if_missing(
        assets.join("gemini.model").as_path(),
        &[
            "https://huggingface.co/google/gemma-2-2b/resolve/main/tokenizer.model",
            "https://huggingface.co/google-t5/t5-base/resolve/main/spiece.model",
        ],
        true,
    );
}

/// Download `url` to `dest` if `dest` does not already exist.
/// `urls` is tried in order; the first that succeeds wins.
/// Panics if every URL fails and `required` is true.
fn download_if_missing(dest: &Path, urls: &[&str], required: bool) {
    if dest.exists() {
        return;
    }

    // Retrieve the optional HF bearer token once.
    let hf_auth: Option<String> = std::env::var("HF_TOKEN").ok();

    for &url in urls {
        println!("cargo:warning=Downloading {} → {}", url, dest.display());

        let mut cmd = Command::new("curl");
        cmd.args([
            "--fail", "--silent", "--show-error", "--location",
            "--output", dest.to_str().expect("non-UTF-8 path"),
            url,
        ]);
        if let Some(ref token) = hf_auth {
            cmd.args(["-H", &format!("Authorization: Bearer {token}")]);
        }

        let ok = cmd
            .status()
            .map(|s| s.success())
            .unwrap_or(false);

        if ok {
            return; // success — done
        }

        // Clean up any partial file before trying the next URL.
        let _ = std::fs::remove_file(dest);

        if urls.len() > 1 {
            println!("cargo:warning={url} failed, trying next URL …");
        }
    }

    if required {
        panic!(
            "Failed to download asset to {}.\n\
             URLs tried: {}\n\
             Hint: for gated models (e.g. Gemma) set the HF_TOKEN env var to a \
             HuggingFace token that has accepted the model licence, then rerun.",
            dest.display(),
            urls.join(", ")
        );
    }
}
