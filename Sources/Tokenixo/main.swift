import SwiftUI

// main.swift is the compiler-designated entry point for this target.
// Calling App.main() here is the explicit equivalent of @main, and avoids
// the "multiple entry-point" error that arises when two files in the same
// module both declare @main.

private struct TokenixoApp: App {
    var body: some Scene {
        WindowGroup("Tokenixo") {
            ContentView()
        }
        .defaultSize(width: 900, height: 620)
        .commands {
            CommandGroup(replacing: .newItem) { }
        }
    }
}

TokenixoApp.main()
