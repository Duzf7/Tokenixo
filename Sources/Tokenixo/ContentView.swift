import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Tokenizer engine (ObservableObject, always on MainActor)
//
// Keeping tokenization state in a @MainActor ObservableObject guarantees that
// @Published updates always happen on the main thread and always trigger SwiftUI
// re-renders.  The plain @State + Task approach used previously was broken because
// Task{} inside a View struct method does NOT inherit @MainActor, so writes to
// @State from the continuation ran off-thread and were silently ignored.

@MainActor
final class TokenizerEngine: ObservableObject {
    @Published var spans:        [TokenSpan]   = []
    @Published var isTokenizing: Bool          = false

    private var currentTask: Task<Void, Never>?

    /// Re-tokenize `text` with `kind`.  Cancels any in-flight task first so
    /// rapid keystrokes never stack up stale results.
    func retokenize(text: String, kind: TokenizerKind) {
        currentTask?.cancel()
        guard !text.isEmpty else {
            spans        = []
            isTokenizing = false
            return
        }

        isTokenizing = true
        // Snapshot values so the detached closure captures plain Sendable types.
        let t = text, k = kind
        currentTask = Task { [weak self] in
            // Task inherits @MainActor from the enclosing class method.
            // Task.detached moves the blocking Rust FFI call off the main thread.
            let result = await Task.detached(priority: .userInitiated) {
                tokenize(text: t, kind: k)      // blocking Rust FFI
            }.value
            guard !Task.isCancelled else { return }
            // We are back on MainActor — safe to write @Published properties.
            self?.spans        = result
            self?.isTokenizing = false
        }
    }
}

// MARK: - Context-window model catalogue

private struct ContextModel: Identifiable {
    let id = UUID()
    let name: String
    let limit: Int
}

private let contextModels: [ContextModel] = [
    ContextModel(name: "Claude Haiku 4.5",  limit: 200_000),
    ContextModel(name: "Claude Sonnet 4.6", limit: 1_000_000),
    ContextModel(name: "Claude Opus 4.6",   limit: 1_000_000),
    ContextModel(name: "Gemini 3 Flash",    limit: 1_048_576),
    ContextModel(name: "Gemini 3.1 Pro",    limit: 1_048_576),
    ContextModel(name: "GPT-5.4",           limit: 1_050_000),
]

// MARK: - Token highlight palettes (6 colours, light + dark)

// UniFFI generates Swift enum cases via heck::to_lower_camel_case:
//   "ChatGPT" → .chatGpt   "Claude" → .claude   "Gemini" → .gemini

private let lightPalette: [NSColor] = [
    NSColor(red: 1.00, green: 0.91, blue: 0.71, alpha: 1),  // amber
    NSColor(red: 0.78, green: 0.95, blue: 0.78, alpha: 1),  // sage
    NSColor(red: 0.76, green: 0.89, blue: 1.00, alpha: 1),  // sky
    NSColor(red: 0.94, green: 0.79, blue: 0.97, alpha: 1),  // lavender
    NSColor(red: 1.00, green: 0.80, blue: 0.80, alpha: 1),  // rose
    NSColor(red: 0.78, green: 0.97, blue: 0.94, alpha: 1),  // mint
]

private let darkPalette: [NSColor] = [
    NSColor(red: 0.42, green: 0.30, blue: 0.08, alpha: 1),  // amber
    NSColor(red: 0.14, green: 0.35, blue: 0.14, alpha: 1),  // sage
    NSColor(red: 0.10, green: 0.24, blue: 0.44, alpha: 1),  // sky
    NSColor(red: 0.30, green: 0.14, blue: 0.36, alpha: 1),  // lavender
    NSColor(red: 0.42, green: 0.14, blue: 0.14, alpha: 1),  // rose
    NSColor(red: 0.10, green: 0.32, blue: 0.30, alpha: 1),  // mint
]

// MARK: - Highlighted NSTextView wrapper

private final class HighlightCoordinator: NSObject, NSTextViewDelegate {
    var onTextChange: (String) -> Void = { _ in }

    func textDidChange(_ notification: Notification) {
        guard let tv = notification.object as? NSTextView else { return }
        onTextChange(tv.string)
    }
}

private struct TokenizedTextEditor: NSViewRepresentable {
    @Binding var text: String
    let spans: [TokenSpan]
    let colorScheme: ColorScheme

    func makeCoordinator() -> HighlightCoordinator { HighlightCoordinator() }

    func makeNSView(context: Context) -> NSScrollView {
        let scroll = NSTextView.scrollableTextView()
        guard let tv = scroll.documentView as? NSTextView else { return scroll }

        tv.isEditable  = true
        tv.isRichText  = true
        tv.allowsUndo  = true
        tv.isAutomaticQuoteSubstitutionEnabled = false
        tv.isAutomaticDashSubstitutionEnabled  = false
        tv.font = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        tv.textContainerInset = NSSize(width: 8, height: 8)
        tv.delegate = context.coordinator
        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }

        // Refresh the callback on every update cycle so the @Binding reference
        // never goes stale if SwiftUI recreates the struct value.
        //
        // NOTE: no guard against tv.string == newText here.  By the time
        // textDidChange fires, NSTextView has already committed the user's edit
        // so tv.string always equals newText — a guard would silently swallow
        // every keystroke.  Programmatic tv.string = text changes in this same
        // updateNSView do NOT fire textDidChange, so there is no loop risk.
        context.coordinator.onTextChange = { newText in
            self.text = newText     // propagates to @State in ContentView
        }

        // Update text content only when an external change happened (file open,
        // Clear button).  Never overwrite on user-driven edits to avoid cursor
        // position resets.
        if tv.string != text {
            let sel = tv.selectedRanges
            tv.string = text
            let safe = sel.filter {
                let r = $0.rangeValue
                return r.location + r.length <= tv.string.utf16.count
            }
            if !safe.isEmpty { tv.selectedRanges = safe }
        }

        applyHighlights(to: tv)
    }

    private func applyHighlights(to tv: NSTextView) {
        guard let storage = tv.textStorage else { return }
        let palette   = colorScheme == .dark ? darkPalette : lightPalette
        let fullRange = NSRange(location: 0, length: storage.length)

        storage.beginEditing()
        storage.removeAttribute(.backgroundColor, range: fullRange)
        // Re-apply the monospaced font so that attribute edits above don't
        // inadvertently revert the text to the system font.
        storage.addAttribute(.font,
                             value: NSFont.monospacedSystemFont(ofSize: 13, weight: .regular),
                             range: fullRange)

        let utf16Count = tv.string.utf16.count
        for span in spans {
            let start = Int(span.start), end = Int(span.end)
            guard start >= 0, end > start, end <= utf16Count else { continue }
            storage.addAttribute(.backgroundColor,
                                 value: palette[Int(span.index) % palette.count],
                                 range: NSRange(location: start, length: end - start))
        }
        storage.endEditing()
    }
}

// MARK: - Stats bar

private struct StatsBar: View {
    let tokenCount: Int
    let charCount:  Int
    let wordCount:  Int
    let lineCount:  Int

    var body: some View {
        HStack(spacing: 20) {
            stat(label: "Tokens", value: tokenCount)
            Divider().frame(height: 14)
            stat(label: "Chars",  value: charCount)
            Divider().frame(height: 14)
            stat(label: "Words",  value: wordCount)
            Divider().frame(height: 14)
            stat(label: "Lines",  value: lineCount)
            Spacer()
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 6)
        .background(.bar)
    }

    private func stat(label: String, value: Int) -> some View {
        HStack(spacing: 4) {
            Text(label).font(.caption).foregroundStyle(.secondary)
            Text(value.formatted()).font(.caption.monospacedDigit()).fontWeight(.medium)
        }
    }
}

// MARK: - Context window usage panel

private struct ContextWindowRow: View {
    let model: ContextModel
    let tokenCount: Int

    private var fraction: Double {
        guard model.limit > 0 else { return 0 }
        return min(Double(tokenCount) / Double(model.limit), 1.0)
    }

    private var fillColor: Color {
        switch fraction {
        case ..<0.60: return .green
        case ..<0.85: return .yellow
        default:      return .red
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 3) {
            HStack {
                Text(model.name).font(.caption).lineLimit(1)
                Spacer()
                Text("\(tokenCount.formatted()) / \(model.limit.formatted())")
                    .font(.caption.monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            GeometryReader { geo in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 3).fill(.quaternary)
                    RoundedRectangle(cornerRadius: 3)
                        .fill(fillColor)
                        .frame(width: max(geo.size.width * fraction, fraction > 0 ? 3 : 0))
                }
            }
            .frame(height: 6)
        }
    }
}

private struct ContextWindowPanel: View {
    let tokenCount: Int
    @State private var isExpanded = true

    var body: some View {
        DisclosureGroup(isExpanded: $isExpanded) {
            VStack(spacing: 8) {
                ForEach(contextModels) { ContextWindowRow(model: $0, tokenCount: tokenCount) }
            }
            .padding(.top, 4)
        } label: {
            Text("Context Window Usage")
                .font(.caption).fontWeight(.semibold).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(.bar)
    }
}

// MARK: - Main content view

struct ContentView: View {
    @StateObject private var engine = TokenizerEngine()
    @State private var inputText    = ""
    @State private var selectedKind: TokenizerKind = .chatGpt
    @Environment(\.colorScheme) private var colorScheme

    // MARK: Derived statistics (read from engine + inputText)

    private var tokenCount: Int { engine.spans.count }
    private var charCount:  Int { inputText.count }
    private var wordCount:  Int { inputText.split(whereSeparator: \.isWhitespace).count }
    private var lineCount:  Int {
        guard !inputText.isEmpty else { return 0 }
        return inputText.components(separatedBy: "\n").count
    }

    // MARK: Body

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            TokenizedTextEditor(
                text:        $inputText,
                spans:       engine.spans,
                colorScheme: colorScheme
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            // Spinner visible while the tokenizer is initialising on first use
            // (tiktoken downloads its vocab file in the background on first call).
            .overlay(alignment: .topTrailing) {
                if engine.isTokenizing {
                    ProgressView().scaleEffect(0.6).padding(8)
                }
            }
            Divider()
            StatsBar(
                tokenCount: tokenCount,
                charCount:  charCount,
                wordCount:  wordCount,
                lineCount:  lineCount
            )
            Divider()
            ContextWindowPanel(tokenCount: tokenCount)
        }
        .onChange(of: inputText)    { _, _ in engine.retokenize(text: inputText, kind: selectedKind) }
        .onChange(of: selectedKind) { _, _ in engine.retokenize(text: inputText, kind: selectedKind) }
        .onAppear {
            // Verify the FFI bridge is alive; result appears in Console.app.
            let pong = ping()
            print("[Tokenixo] FFI ping → \(pong)")
        }
    }

    // MARK: Toolbar

    private var toolbar: some View {
        HStack(spacing: 10) {
            Text("Tokenizer").font(.subheadline).foregroundStyle(.secondary)

            Picker("Tokenizer", selection: $selectedKind) {
                Text("ChatGPT").tag(TokenizerKind.chatGpt)
                Text("Claude") .tag(TokenizerKind.claude)
                Text("Gemini") .tag(TokenizerKind.gemini)
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .frame(width: 130)

            Spacer()

            Button("Clear") { inputText = ""; engine.spans = [] }
                .keyboardShortcut(.delete, modifiers: [.command, .shift])

            Button("Open…") { openFile() }
                .keyboardShortcut("o", modifiers: .command)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

    // MARK: File open

    private func openFile() {
        let panel = NSOpenPanel()
        panel.title               = "Open Text File"
        panel.allowedContentTypes = [.plainText, .sourceCode, .text]
        panel.allowsMultipleSelection = false
        panel.canChooseDirectories    = false
        guard panel.runModal() == .OK, let url = panel.url else { return }
        guard let contents = try? String(contentsOf: url, encoding: .utf8) else { return }
        inputText = contents
    }
}
