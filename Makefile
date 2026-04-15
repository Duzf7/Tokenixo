.PHONY: app clean run

# ── Paths ────────────────────────────────────────────────────────────────────

CARGO_RELEASE   := target/release
RUST_STATICLIB  := $(CARGO_RELEASE)/libtokenixo.a
# SPM places the Swift binary in its own build tree
SWIFT_BINARY    := .build/release/Tokenixo

UDL             := src/tokenixo.udl
GENERATED_DIR   := generated
# uniffi-bindgen emits tokenixo.swift; SPM only scans Sources/Tokenixo/,
# so the generated Swift wrapper is copied there after bindgen runs.
GENERATED_SWIFT := $(GENERATED_DIR)/tokenixo.swift
SOURCES_SWIFT   := Sources/Tokenixo/tokenixo.swift

APP_BUNDLE      := Tokenixo.app
APP_MACOS       := $(APP_BUNDLE)/Contents/MacOS
APP_RESOURCES   := $(APP_BUNDLE)/Contents

# ── app ──────────────────────────────────────────────────────────────────────

app:
	# 1. Compile the Rust library and the uniffi-bindgen helper binary.
	cargo build --release

	# 2. Generate UniFFI Swift bindings from the UDL.
	#    Produces: generated/tokenixo.swift  (Swift wrapper — must be compiled)
	#              generated/tokenixoFFI.h   (C header  — found via -I generated)
	#              generated/tokenixoFFI.modulemap
	cargo run --bin uniffi-bindgen generate $(UDL) --language swift --out-dir $(GENERATED_DIR)/
	#    Copy the Swift wrapper into the SPM source tree so the target picks it up.
	#    Package.swift uses path: "Sources/Tokenixo", so only files there are compiled.
	cp $(GENERATED_SWIFT) $(SOURCES_SWIFT)

	# 3. Build the SwiftUI executable.
	#    Package.swift links libtokenixo from target/release (cargo output dir).
	@test -f $(GENERATED_DIR)/tokenixoFFI.h || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.h not found — run step 2 first"; exit 1; }
	@test -f $(GENERATED_DIR)/tokenixoFFI.modulemap || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.modulemap not found — run step 2 first"; exit 1; }
	swift build --configuration release

	# 4. Assemble the app bundle skeleton.
	mkdir -p $(APP_MACOS)
	mkdir -p $(APP_RESOURCES)/Resources/assets

	# 5. Copy the Swift executable into the bundle.
	cp $(SWIFT_BINARY) $(APP_MACOS)/Tokenixo

	# 6. Copy the Info.plist into the bundle.
	cp Info.plist $(APP_RESOURCES)/Info.plist

	# 7. Bundle the tokenizer vocab assets downloaded by build.rs.
	#    These are read at runtime via assets_dir() in lib.rs which resolves
	#    <exe>/../Resources/assets when running from the app bundle.
	@test -d assets || { echo "ERROR: assets/ directory not found — did cargo build succeed?"; exit 1; }
	cp -r assets/. $(APP_RESOURCES)/Resources/assets/

	# 8. Done.
	@echo "✓ Built Tokenixo.app — run with: open Tokenixo.app"

# ── run ──────────────────────────────────────────────────────────────────────

run: app
	open $(APP_BUNDLE)

# ── clean ────────────────────────────────────────────────────────────────────

clean:
	rm -rf .build $(GENERATED_DIR) $(APP_BUNDLE) $(SOURCES_SWIFT)
