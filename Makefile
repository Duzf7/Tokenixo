.PHONY: app run clean dmg

# ── Version ───────────────────────────────────────────────────────────────────

# Derive version from the nearest git tag (e.g. v0.2.0 → 0.2.0).
# Falls back to 0.1.0 when no tag exists (clean checkout, CI without tags, etc.).
_GIT_TAG        := $(shell git describe --tags --abbrev=0 2>/dev/null | sed 's/^v//')
VERSION         := $(or $(_GIT_TAG),0.1.0)

# ── Paths ────────────────────────────────────────────────────────────────────

CARGO_RELEASE   := target/release
RUST_STATICLIB  := $(CARGO_RELEASE)/libtokenixo.a
RUST_DYLIB      := $(CARGO_RELEASE)/libtokenixo.dylib
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
	cargo run --bin uniffi-bindgen generate $(UDL) --language swift --out-dir $(GENERATED_DIR)/
	#    Copy the Swift wrapper into the SPM source tree so the target picks it up.
	cp $(GENERATED_SWIFT) $(SOURCES_SWIFT)

	# 3. Build the SwiftUI executable.
	@test -f $(GENERATED_DIR)/tokenixoFFI.h || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.h not found — run step 2 first"; exit 1; }
	@test -f $(GENERATED_DIR)/tokenixoFFI.modulemap || { echo "ERROR: $(GENERATED_DIR)/tokenixoFFI.modulemap not found — run step 2 first"; exit 1; }
	swift build --configuration release

	# 4. Assemble the app bundle skeleton.
	mkdir -p $(APP_MACOS)
	mkdir -p $(APP_RESOURCES)/Frameworks          # <-- Frameworks folder for dylib
	mkdir -p $(APP_RESOURCES)/Resources/assets

	# 5. Copy the Swift executable into the bundle.
	cp $(SWIFT_BINARY) $(APP_MACOS)/Tokenixo

	# 6. Copy the Info.plist into the bundle.
	cp Info.plist $(APP_RESOURCES)/Info.plist

	# 7. Bundle the tokenizer vocab assets.
	@test -d assets || { echo "ERROR: assets/ directory not found — did cargo build succeed?"; exit 1; }
	cp -r assets/. $(APP_RESOURCES)/Resources/assets/

	# 8. Gzip-compress assets to reduce app size.
	gzip -9 -f $(APP_RESOURCES)/Resources/assets/claude-tokenizer.json
	gzip -9 -f $(APP_RESOURCES)/Resources/assets/gemini.model

	# 9. Copy the dynamic library into the app bundle.
	cp $(RUST_DYLIB) $(APP_RESOURCES)/Frameworks/

	# 10. Change the dylib's install name to a relative path.
	install_name_tool -id @executable_path/../Frameworks/libtokenixo.dylib \
		$(APP_RESOURCES)/Frameworks/libtokenixo.dylib

	# 11. Update the executable's reference to the dylib.
	install_name_tool -change $(RUST_DYLIB) \
		@executable_path/../Frameworks/libtokenixo.dylib \
		$(APP_MACOS)/Tokenixo

	# 12. Ad‑hoc sign the entire bundle so it runs locally.
	codesign --force --deep --sign - $(APP_BUNDLE)

	# 13. Strip local symbols from the Swift binary (optional, saves space).
	strip -x $(APP_MACOS)/Tokenixo

	# 14. Done.
	@echo "✓ Built $(APP_BUNDLE) v$(VERSION) — run with: open $(APP_BUNDLE)"

# ── dmg ──────────────────────────────────────────────────────────────────────

dmg: app
	@echo "Creating DMG with Applications symlink..."
	rm -rf dmg_staging
	mkdir -p dmg_staging
	cp -Rp $(APP_BUNDLE) dmg_staging/
	ln -s /Applications dmg_staging/Applications
	# Ad‑hoc sign the staged app to avoid Gatekeeper quirks during packaging.
	codesign --force --deep --sign - dmg_staging/$(APP_BUNDLE)
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
	rm -rf .build $(GENERATED_DIR) $(APP_BUNDLE) $(SOURCES_SWIFT) *.dmg dmg_staging