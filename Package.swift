// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "Tokenixo",
    platforms: [
        .macOS(.v14)
    ],
    targets: [
        // Exposes the UniFFI-generated C header (RustBuffer, RustCallStatus, etc.)
        // and all ffi_tokenixo_* symbols to Swift via the modulemap in generated/.
        // Wrapper dir with module.modulemap (SPM requires this exact filename).
        // The modulemap references the UniFFI-generated C header in generated/.
        .systemLibrary(
            name: "TokenixoFFI",
            path: "TokenixoFFI",
            pkgConfig: nil,
            providers: nil
        ),
        .executableTarget(
            name: "Tokenixo",
            dependencies: ["TokenixoFFI"],
            path: "Sources/Tokenixo",
            linkerSettings: [
                .unsafeFlags(["-L", "target/release"]),
                .linkedLibrary("tokenixo")
            ]
        )
    ]
)
