import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Tokenizer engine

@MainActor
final class TokenizerEngine: ObservableObject {
    /// Flat span buffer: even indices are UTF-8 start offsets, odd indices are
    /// end offsets (both UInt32).  Token count = spanBuffer.count / 2.
    /// Storing interleaved UInt32 pairs cuts per-span overhead vs a struct array
    /// and halves allocator metadata by using one contiguous block.
    @Published var spanBuffer:   [UInt32] = []
    @Published var isTokenizing: Bool     = false

    private var currentTask: Task<Void, Never>?

    func retokenize(text: String, kind: TokenizerKind) {
        currentTask?.cancel()
        guard !text.isEmpty else {
            spanBuffer   = []
            isTokenizing = false
            return
        }

        isTokenizing = true
        let t = text, k = kind
        currentTask = Task { [weak self] in
            do    { try await Task.sleep(for: .milliseconds(150)) }
            catch { return }

            // Convert [TokenSpan] → flat [UInt32] on the background thread so
            // the FFI allocation is freed before we touch the main actor.
            let buf = await Task.detached(priority: .userInitiated) {
                let raw = tokenize(text: t, kind: k)
                var b = [UInt32]()
                b.reserveCapacity(raw.count * 2)
                for s in raw { b.append(UInt32(s.start)); b.append(UInt32(s.end)) }
                return b   // raw is released here
            }.value

            guard !Task.isCancelled else { return }
            self?.spanBuffer   = buf
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

// MARK: - Token highlight palettes
//
// Six NSColor objects allocated ONCE per process (static let at module scope).
// All 50,000 span attribute applications share these six objects — never
// allocate a new NSColor per span.

private let lightPalette: [NSColor] = [
    NSColor(red: 1.00, green: 0.91, blue: 0.71, alpha: 1),  // amber
    NSColor(red: 0.78, green: 0.95, blue: 0.78, alpha: 1),  // sage
    NSColor(red: 0.76, green: 0.89, blue: 1.00, alpha: 1),  // sky
    NSColor(red: 0.94, green: 0.79, blue: 0.97, alpha: 1),  // lavender
    NSColor(red: 1.00, green: 0.80, blue: 0.80, alpha: 1),  // rose
    NSColor(red: 0.78, green: 0.97, blue: 0.94, alpha: 1),  // mint
]

private let darkPalette: [NSColor] = [
    NSColor(red: 0.42, green: 0.30, blue: 0.08, alpha: 1),
    NSColor(red: 0.14, green: 0.35, blue: 0.14, alpha: 1),
    NSColor(red: 0.10, green: 0.24, blue: 0.44, alpha: 1),
    NSColor(red: 0.30, green: 0.14, blue: 0.36, alpha: 1),
    NSColor(red: 0.42, green: 0.14, blue: 0.14, alpha: 1),
    NSColor(red: 0.10, green: 0.32, blue: 0.30, alpha: 1),
]

// MARK: - Highlight coordinator
//
// Full-document highlight pass, correctness-first:
//
//  • On every retokenise, ALL spans are coloured in one beginEditing/endEditing
//    block — no viewport window, no scroll callbacks, no visible-range maths.
//  • The heavy work (building the (range, color) list) runs on a background
//    queue; only the storage mutations touch the main thread.
//  • A generation counter cancels any in-flight background pass that is
//    superseded by a newer retokenise before it finishes.
//  • Memory stays low via the compact UInt32 spanBuffer and the six static
//    NSColor objects — not via attribute culling.

private final class HighlightCoordinator: NSObject, NSTextViewDelegate {

    var onTextChange: (String) -> Void = { _ in }

    // Flat span buffer. span i: start = buf[i*2], end = buf[i*2+1].
    var spanBuffer: [UInt32] = []
    var palette:    [NSColor] = []

    weak var textView: NSTextView?

    // Incremented on every invalidation; background passes check this before
    // committing so stale results from a previous tokenise are discarded.
    private var generation: Int = 0

    // MARK: NSTextViewDelegate

    func textDidChange(_ notification: Notification) {
        guard let tv = notification.object as? NSTextView else { return }
        onTextChange(tv.string)
    }

    // MARK: Highlight

    /// Colour every span in spanBuffer across the full document.
    ///
    /// Steps:
    ///  1. Reset NSTextStorage to plain text (synchronous, main thread).
    ///  2. Build a [(NSRange, NSColor)] list on a background thread — O(n) but
    ///     no UIKit/AppKit calls, so it's safe off-main.
    ///  3. Apply all attributes on the main thread inside one
    ///     beginEditing/endEditing block so NSLayoutManager invalidates layout
    ///     exactly once regardless of token count.
    func invalidateAllHighlights() {
        guard let tv = textView, let storage = tv.textStorage else { return }

        // ── Step 1: reset to plain monospace text ────────────────────────────
        let font  = NSFont.monospacedSystemFont(ofSize: 13, weight: .regular)
        let fresh = NSAttributedString(string: tv.string, attributes: [.font: font])
        storage.setAttributedString(fresh)

        guard !spanBuffer.isEmpty else { return }

        // Snapshot everything the background task needs; capture by value so
        // the main-thread state can change freely while the task runs.
        generation &+= 1
        let gen   = generation
        let buf   = spanBuffer          // value copy — O(n) but cheap for UInt32
        let pal   = palette
        let total = storage.length      // read on main thread while storage is stable

        // ── Step 2: build attribute list off-main ────────────────────────────
        DispatchQueue.global(qos: .userInitiated).async { [weak self] in
            let count = buf.count / 2
            var attrs = [(NSRange, NSColor)]()
            attrs.reserveCapacity(count)
            for i in 0 ..< count {
                let s = Int(buf[i * 2])
                // Extend end to the start of the next span (covers whitespace gap),
                // or to the full document length for the last span.
                let e = i + 1 < count ? Int(buf[(i + 1) * 2]) : total
                guard s >= 0, e > s, e <= total else { continue }
                attrs.append((NSRange(location: s, length: e - s),
                              pal[i % pal.count]))
            }

            // ── Step 3: apply on main inside one editing session ─────────────
            DispatchQueue.main.async { [weak self] in
                guard let self, self.generation == gen else { return }
                storage.beginEditing()
                for (range, color) in attrs {
                    storage.addAttribute(.backgroundColor, value: color, range: range)
                }
                storage.endEditing()
            }
        }
    }
}

// MARK: - Highlighted NSTextView wrapper

private struct TokenizedTextEditor: NSViewRepresentable {
    @Binding var text: String
    let spanBuffer:  [UInt32]
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

        let coord = context.coordinator
        coord.textView = tv

        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }
        let coord = context.coordinator

        coord.onTextChange = { newText in self.text = newText }

        let newPalette     = colorScheme == .dark ? darkPalette : lightPalette
        let paletteChanged = coord.palette.first != newPalette.first
        let spansChanged   = coord.spanBuffer.count != spanBuffer.count
                          || coord.spanBuffer.first != spanBuffer.first
                          || coord.spanBuffer.last  != spanBuffer.last

        coord.palette    = newPalette
        coord.spanBuffer = spanBuffer

        // Text changed externally (file open, Clear button) — push new string
        // and reset all attributes via setAttributedString.
        if tv.string != text {
            let sel = tv.selectedRanges
            tv.string = text
            let safe = sel.filter {
                let r = $0.rangeValue
                return r.location + r.length <= tv.string.utf16.count
            }
            if !safe.isEmpty { tv.selectedRanges = safe }
            coord.invalidateAllHighlights()
            return
        }

        if spansChanged || paletteChanged {
            coord.invalidateAllHighlights()
        }
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

    private var tokenCount: Int { engine.spanBuffer.count / 2 }
    private var charCount:  Int { inputText.count }
    private var wordCount:  Int { inputText.split(whereSeparator: \.isWhitespace).count }
    private var lineCount:  Int {
        guard !inputText.isEmpty else { return 0 }
        return inputText.components(separatedBy: "\n").count
    }

    var body: some View {
        VStack(spacing: 0) {
            toolbar
            Divider()
            TokenizedTextEditor(
                text:        $inputText,
                spanBuffer:  engine.spanBuffer,
                colorScheme: colorScheme
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
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
            let pong = ping()
            print("[Tokenixo] FFI ping → \(pong)")
        }
    }

    private var toolbar: some View {
        HStack(spacing: 10) {
            VStack(alignment: .leading, spacing: 1) {
                Text("Tokenixo").font(.subheadline).fontWeight(.semibold)
                Text("v\(APP_VERSION)").font(.caption2).foregroundStyle(.tertiary)
            }

            Picker("Tokenizer", selection: $selectedKind) {
                Text("ChatGPT").tag(TokenizerKind.chatGpt)
                Text("Claude") .tag(TokenizerKind.claude)
                Text("Gemini") .tag(TokenizerKind.gemini)
            }
            .pickerStyle(.menu)
            .labelsHidden()
            .frame(width: 130)

            Spacer()

            Button("Clear") { inputText = ""; engine.spanBuffer = [] }
                .keyboardShortcut(.delete, modifiers: [.command, .shift])

            Button("Open…") { openFile() }
                .keyboardShortcut("o", modifiers: .command)
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
    }

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
