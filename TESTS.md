# Order

```
edit Rust  → cargo test
edit FFI   → swift run
ship       → make app
package    → make dmg
```

---

## Rust test

```bash
cargo test                    # unit tests
cargo run --example tokenize  # quick manual test
```

---

## Swift logic — without .app bundle

```bash
swift run                     # runs Sources/Tokenixo/main.swift directly
```

No bundling, no `make app`. Rebuilds only changed files. This is the main development loop.

---

## Shipping

```bash
make app
```

---

## Packaging

```bash
make dmg
```