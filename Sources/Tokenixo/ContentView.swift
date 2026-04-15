import SwiftUI
import AppKit
import UniformTypeIdentifiers

// MARK: - Tokenizer engine (ObservableObject, always on MainActor)

@MainActor
final class TokenizerEngine: ObservableObject {
    @Published var spans:        [TokenSpan] = []
    @Published var isTokenizing: Bool        = false

    private var currentTask: Task<Void, Never>?

    /// Re-tokenize `text` with `kind`.
    ///
    /// Debounced 150 ms: if the user types another character before the timer
    /// fires the previous task is cancelled and a new one starts.  This keeps
    /// the UI responsive during fast typing and avoids saturating the Rust FFI
    /// with redundant work.
    func retokenize(text: String, kind: TokenizerKind) {
        currentTask?.cancel()
        guard !text.isEmpty else {
            spans        = []
            isTokenizing = false
            return
        }

        isTokenizing = true
        let t = text, k = kind
        currentTask = Task { [weak self] in
            // ── Debounce ─────────────────────────────────────────────────────
            // Task.sleep throws CancellationError when cancel() is called, so
            // the catch exits cleanly without reaching the Rust FFI at all.
            do    { try await Task.sleep(for: .milliseconds(150)) }
            catch { return }

            // ── Tokenize on a background thread ──────────────────────────────
            let result = await Task.detached(priority: .userInitiated) {
                tokenize(text: t, kind: k)
            }.value

            guard !Task.isCancelled else { return }
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

// MARK: - Highlight coordinator
//
// Owns three optimisations:
//
//  1. Viewport culling — only background-color attributes in the visible
//     scroll region (+1 viewport of padding above/below) exist in
//     NSTextStorage at any moment.  Off-screen text has no colour attribute,
//     so NSLayoutManager tracks far fewer attribute-run boundaries.
//
//  2. Incremental clear — on scroll we clear exactly the previous highlighted
//     NSRange and stamp the new one.  On full spans replacement we clear the
//     entire document once (one cheap removeAttribute call) then stamp the
//     new viewport.
//
//  3. Single beginEditing/endEditing session — all attribute mutations happen
//     inside one editing batch so NSLayoutManager invalidates layout only once
//     per highlight pass.

private final class HighlightCoordinator: NSObject, NSTextViewDelegate {

    // ── Set by updateNSView every cycle ──────────────────────────────────────
    var onTextChange: (String) -> Void = { _ in }
    var spans:   [TokenSpan] = []
    var palette: [NSColor]   = []

    // ── Weak refs to avoid retain cycles ─────────────────────────────────────
    weak var scrollView: NSScrollView?
    weak var textView:   NSTextView?

    // The NSRange inside NSTextStorage that currently has backgroundColor set.
    // We clear exactly this range before stamping the next viewport.
    // Empty range means "nothing is highlighted" (start state, or after a
    // full clear that left the storage clean).
    var highlightedRange = NSRange(location: 0, length: 0)

    // MARK: NSTextViewDelegate

    func textDidChange(_ notification: Notification) {
        guard let tv = notification.object as? NSTextView else { return }
        onTextChange(tv.string)
    }

    // MARK: Scroll notification

    @objc func boundsDidChange(_: Notification) {
        // User scrolled — update which spans are coloured without a full reset.
        applyVisibleHighlights()
    }

    // MARK: Highlight passes

    /// Call when `spans` or `palette` changes.  Clears the *entire* document's
    /// background colour (one call, O(1) in NSAttributedString) so stale
    /// colours from a previous tokenisation that might be outside the current
    /// viewport are removed, then stamps the new visible set.
    func invalidateAllHighlights() {
        guard let tv = textView, let storage = tv.textStorage else { return }
        storage.beginEditing()
        storage.removeAttribute(.backgroundColor,
                                range: NSRange(location: 0, length: storage.length))
        storage.endEditing()
        highlightedRange = NSRange(location: 0, length: 0)
        applyVisibleHighlights()
    }

    /// Apply background colours to the visible viewport + one screen of
    /// padding.  Clears the previously highlighted range first so colours
    /// never accumulate outside the active window.
    func applyVisibleHighlights() {
        guard
            let sv      = scrollView,
            let tv      = textView,
            let storage = tv.textStorage,
            let lm      = tv.layoutManager,
            let tc      = tv.textContainer,
            storage.length > 0,
            !spans.isEmpty
        else {
            // No text / no spans — just wipe whatever was left.
            if let storage = textView?.textStorage, highlightedRange.length > 0 {
                storage.beginEditing()
                storage.removeAttribute(.backgroundColor, range: highlightedRange)
                storage.endEditing()
                highlightedRange = NSRange(location: 0, length: 0)
            }
            return
        }

        // ── 1. Compute visible character range ────────────────────────────────
        let visRect = sv.contentView.bounds
        let origin  = tv.textContainerOrigin
        let localRect = NSRect(x: visRect.minX - origin.x,
                               y: visRect.minY - origin.y,
                               width: visRect.width,
                               height: visRect.height)

        let glyphRange = lm.glyphRange(forBoundingRect: localRect, in: tc)
        let charRange  = lm.characterRange(forGlyphRange: glyphRange,
                                           actualGlyphRange: nil)

        // Pad by one full viewport above and below for smooth scroll.
        let pad   = max(charRange.length, 1)
        let wStart = max(0, charRange.location - pad)
        let wEnd   = min(storage.length, charRange.location + charRange.length + pad)
        let workRange = NSRange(location: wStart, length: wEnd - wStart)

        // ── 2. Binary-search for spans that intersect workRange ───────────────
        let visible = spansInRange(workRange)

        // ── 3. Single editing session: clear old, stamp new ───────────────────
        storage.beginEditing()

        if highlightedRange.length > 0 {
            storage.removeAttribute(.backgroundColor, range: highlightedRange)
        }

        let total = storage.length
        for span in visible {
            let s = Int(span.start), e = Int(span.end)
            guard s >= 0, e > s, e <= total else { continue }
            storage.addAttribute(.backgroundColor,
                                 value: palette[Int(span.index) % palette.count],
                                 range: NSRange(location: s, length: e - s))
        }

        storage.endEditing()
        highlightedRange = workRange
    }

    // MARK: Binary search helper

    /// Returns the slice of `spans` whose byte ranges intersect `range`.
    /// Runs in O(log n + k) where k is the number of matching spans.
    private func spansInRange(_ range: NSRange) -> ArraySlice<TokenSpan> {
        guard !spans.isEmpty else { return spans[0..<0] }
        let lo = range.location
        let hi = range.location + range.length

        // First span with end > lo  (i.e. might overlap the left edge)
        var left = 0, right = spans.count
        while left < right {
            let mid = (left + right) / 2
            Int(spans[mid].end) <= lo ? (left = mid + 1) : (right = mid)
        }
        let firstIdx = left

        // Scan forward until start >= hi
        var lastIdx = firstIdx
        while lastIdx < spans.count, Int(spans[lastIdx].start) < hi {
            lastIdx += 1
        }

        return spans[firstIdx..<lastIdx]
    }
}

// MARK: - Highlighted NSTextView wrapper

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

        // Give the coordinator weak refs so it can reach the views from its
        // scroll-notification callback.
        let coord = context.coordinator
        coord.scrollView = scroll
        coord.textView   = tv

        // Observe scroll-position changes so we repaint only the new viewport.
        scroll.contentView.postsBoundsChangedNotifications = true
        NotificationCenter.default.addObserver(
            coord,
            selector: #selector(HighlightCoordinator.boundsDidChange(_:)),
            name: NSView.boundsDidChangeNotification,
            object: scroll.contentView
        )

        return scroll
    }

    func updateNSView(_ scroll: NSScrollView, context: Context) {
        guard let tv = scroll.documentView as? NSTextView else { return }
        let coord = context.coordinator

        // Always refresh the binding closure so it never captures a stale self.
        coord.onTextChange = { newText in self.text = newText }

        let newPalette  = colorScheme == .dark ? darkPalette : lightPalette
        let paletteChanged = coord.palette.first != newPalette.first
        let spansChanged   = coord.spans.count != spans.count
                          || coord.spans.first?.start != spans.first?.start
                          || coord.spans.last?.end    != spans.last?.end

        coord.palette = newPalette
        coord.spans   = spans

        // If the model text differs from the view (file open, Clear button):
        // push the new string and schedule a full highlight reset.
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
            // Spans or palette changed — must clear the entire document once to
            // remove stale colours that may be outside the current viewport.
            coord.invalidateAllHighlights()
        }
        // If neither spans nor text nor palette changed (e.g. a pure SwiftUI
        // re-render for an unrelated reason) there is nothing to do; the scroll
        // observer will handle viewport updates when the user scrolls.
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

    private var tokenCount: Int { engine.spans.count }
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
                spans:       engine.spans,
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
