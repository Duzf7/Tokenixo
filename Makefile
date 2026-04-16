.PHONY: app run clean dmg

# ── Version ───────────────────────────────────────────────────────────────────

# Derive version from the nearest git tag (e.g. v0.2.0 → 0.2.0).
# Falls back to 0.1.0 when no tag exists (clean checkout, CI without tags, etc.).
_GIT_TAG        := $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')
VERSION         := $(or $(_GIT_TAG),0.1.0)

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

DMG_NAME        := Tokenixo-$(VERSION)-macos.dmg

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
	#    Remove any stale dylib so the linker is forced to use libtokenixo.a
	#    (static), producing a fully self-contained binary with no dylib dependency.
	@test -f $(GENERATED_DIR)/tokenixoFFI.h || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.h not found — run step 2 first"; exit 1; }
	@test -f $(GENERATED_DIR)/tokenixoFFI.modulemap || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.modulemap not found — run step 2 first"; exit 1; }
	rm -f target/release/libtokenixo.dylib target/release/deps/libtokenixo.dylib
	swift build --configuration release

	# 4. Assemble the app bundle skeleton (always start with a clean assets dir).
	mkdir -p $(APP_MACOS)
	rm -rf $(APP_RESOURCES)/Resources/assets
	mkdir -p $(APP_RESOURCES)/Resources/assets

	# 5. Copy the Swift executable into the bundle.
	cp $(SWIFT_BINARY) $(APP_MACOS)/Tokenixo

	# 6. Copy the Info.plist into the bundle.
	cp Info.plist $(APP_RESOURCES)/Info.plist

	# 7. Bundle all tokenizer vocab assets uncompressed.
	#    All three are read at runtime from Contents/Resources/assets/.
	@test -d assets || { echo "ERROR: assets/ not found — did cargo build succeed?"; exit 1; }
	cp assets/cl100k_base.tiktoken  $(APP_RESOURCES)/Resources/assets/
	cp assets/claude-tokenizer.json $(APP_RESOURCES)/Resources/assets/
	cp assets/gemini.model          $(APP_RESOURCES)/Resources/assets/

	# 8. Strip local symbols from the Swift binary.
	strip -x $(APP_MACOS)/Tokenixo

	# 9. Done.
	@echo "✓ Built $(APP_BUNDLE) v$(VERSION) — run with: open $(APP_BUNDLE)"

# ── dmg ──────────────────────────────────────────────────────────────────────

dmg: app
	@echo "Creating DMG with Applications symlink..."
	rm -rf dmg_staging
	mkdir -p dmg_staging
	cp -R $(APP_BUNDLE) dmg_staging/
	ln -s /Applications dmg_staging/Applications
	hdiutil create \
		-volname Tokenixo \
		-srcfolder dmg_staging \
		-ov -format UDZO \
		$(DMG_NAME)
	rm -rf dmg_staging
	@echo "✓ Packaged $(DMG_NAME)"

# ── run ──────────────────────────────────────────────────────────────────────

run: app
	open $(APP_BUNDLE)

# ── clean ────────────────────────────────────────────────────────────────────

clean:
	rm -rf .build $(GENERATED_DIR) $(APP_BUNDLE) $(SOURCES_SWIFT) *.dmg