import Core
import CoreText
import SwiftUI
import UIKit

// Colors and wording both the app and the widget use. The colors come from
// shared/theme.json and a server's own color goes through ServerColors, so
// the phone matches the desktop app.

extension Color {
    /// "#RRGGBB" as a color, or nil for anything else.
    static func fromHex(_ hex: String?) -> Color? {
        guard let c = ServerColors.rgb(hex) else { return nil }
        return Color(red: Double(c[0]) / 255.0, green: Double(c[1]) / 255.0, blue: Double(c[2]) / 255.0)
    }
}

/// The app's look: the shared theme names.
enum ThemeChoice: String, CaseIterable, Identifiable {
    case system
    case light
    case dark
    case graphite
    case contrast

    var id: String { rawValue }

    var labelKey: String {
        switch self {
        case .system: return "theme.system"
        case .light: return "theme.light"
        case .dark: return "theme.dark"
        case .graphite: return "theme.graphite"
        case .contrast: return "theme.contrast"
        }
    }

    /// Which theme.json entry to use: "Match my device" follows light or dark.
    func resolvedName(_ scheme: ColorScheme) -> String {
        if self == .system {
            return scheme == .dark ? "dark" : "light"
        }
        return rawValue
    }

    /// The light or dark system look under the theme; nil follows the device.
    var colorScheme: ColorScheme? {
        switch self {
        case .system: return nil
        case .light: return .light
        case .dark, .graphite, .contrast: return .dark
        }
    }
}

/// One theme's colors, by the shared token names ("--sheet", ...).
struct Palette {
    let tokens: [String: String]

    func color(_ name: String, _ fallback: Color) -> Color {
        Color.fromHex(tokens[name]) ?? fallback
    }

    var sheet: Color { color("--sheet", Color(uiColor: .systemGroupedBackground)) }
    var surface: Color { color("--surface", Color(uiColor: .secondarySystemGroupedBackground)) }
    var sunken: Color { color("--surface-sunken", Color(uiColor: .tertiarySystemGroupedBackground)) }
    var textPrimary: Color { color("--text-primary", .primary) }
    var textSecondary: Color { color("--text-secondary", .secondary) }
    var accent: Color { color("--accent", .accentColor) }
    var accentText: Color { color("--accent-text", .white) }
    var accentInk: Color { color("--accent-ink", .accentColor) }
    var accentEdge: Color { color("--accent-edge", .gray) }
    var danger: Color { color("--danger", .red) }
    var success: Color { color("--success", .green) }
    var desk: Color { color("--desk", Color(uiColor: .systemGroupedBackground)) }
    var neutral: Color { color("--neutral", .gray) }
    /// The line around a box: the server's color, half see-through.
    var edge: Color { color("--accent-ring", .gray).opacity(0.5) }
    /// The band behind the sheet (the top bar), and the words on it.
    var band: Color { color("--band", sheet) }
    var bandText: Color { color("--band-text", textPrimary) }
    /// A divider's tab in the top view, and its words.
    var tabFill: Color { color("--tab-fill", sunken) }
    var tabText: Color { color("--tab-text", textPrimary) }

    func tone(_ tone: Tone) -> Color {
        switch tone {
        case .success: return color("--success", .green)
        case .warning: return color("--warning", .orange)
        case .danger: return color("--danger", .red)
        case .neutral: return textSecondary
        }
    }

    /// The theme in the binder look, as the desktop app draws it: plain
    /// paper, with the copper of the app icon on the buttons.
    static func base(_ themes: [String: [String: String]], _ name: String) -> Palette {
        binder(nil, themes, name)
    }

    /// The theme with a server's color on the band behind the sheet, the
    /// buttons, the edges and its tab; the page stays plain paper, as in
    /// the desktop app. A missing or broken color is the look of no server.
    static func server(_ color: String?, _ themes: [String: [String: String]], _ name: String) -> Palette {
        binder(color, themes, name)
    }

    private static func binder(_ color: String?, _ themes: [String: [String: String]], _ name: String) -> Palette {
        var tokens: [String: String] = [:]
        var plain: [String: String] = [:]
        for (key, value) in themes[name] ?? [:] {
            let bare = key.hasPrefix("--") ? String(key.dropFirst(2)) : key
            plain[bare] = value
            tokens["--" + bare] = value
        }
        let look = ServerColors.binder(color, plain, name)
        for (key, value) in look {
            tokens[key] = value
        }
        // The page is the paper.
        if let paper = look["--paper"] {
            tokens["--sheet"] = paper
        }
        return Palette(tokens: tokens)
    }
}

extension Font {
    /// Archivo, the heading font of all three apps (shared/fonts), growing
    /// with the phone's text size like the system's own styles.
    static func heading(_ style: Font.TextStyle = .headline) -> Font {
        var size: CGFloat = 17
        var name = "Archivo-SemiBold"
        switch style {
        case .largeTitle:
            size = 34
            name = "Archivo-Bold"
        case .title:
            size = 28
            name = "Archivo-Bold"
        case .title2:
            size = 22
            name = "Archivo-Bold"
        case .title3:
            size = 20
        default:
            break
        }
        return .custom(name, size: size, relativeTo: style)
    }
}

/// The bundled files, read once.
enum Bundled {
    /// Archivo, from the app's own files: nothing is downloaded.
    static let fonts: Bool = {
        let urls = Bundle.main.urls(forResourcesWithExtension: "ttf", subdirectory: "shared/fonts") ?? []
        for url in urls {
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, nil)
        }
        return !urls.isEmpty
    }()

    static let english: [String: [String]] = {
        guard let data = SharedFiles.data("strings", "json", bundle: .main) else { return [:] }
        return Strings.parse(data)
    }()

    static let themes: [String: [String: String]] = {
        guard let data = SharedFiles.data("theme", "json", bundle: .main) else { return [:] }
        return ServerColors.parseThemes(data)
    }()

    static let feed: [String: String] = {
        guard let data = SharedFiles.data("feed", "json", bundle: .main) else { return [:] }
        return SharedFiles.parseFeed(data)
    }()

    static let apiRange: ApiRange = {
        guard let data = SharedFiles.data("version", "json", bundle: .main), let range = ApiRange.parse(data) else {
            return ApiRange(min: 1, max: 1)
        }
        return range
    }()

    static var appVersion: String {
        (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "0"
    }
}

/// A server's color as a small round badge, or a plain ring when it has none.
struct ColorBadge: View {
    let hex: String?
    var size: CGFloat = 14

    var body: some View {
        if let badge = ServerColors.badge(hex), let fill = Color.fromHex(badge.fill) {
            Circle()
                .fill(fill)
                .frame(width: size, height: size)
                .accessibilityHidden(true)
        } else {
            Circle()
                .strokeBorder(Color.secondary, lineWidth: 1.5)
                .frame(width: size, height: size)
                .accessibilityHidden(true)
        }
    }
}

/// A small (i) next to a heading or a label. A tap shows the sentence that
/// explains it; VoiceOver reads it as the dot's hint. (The desktop app
/// shows the same sentence on hover.)
struct InfoDot: View {
    let words: String
    let palette: Palette
    /// What VoiceOver calls the dot ("What is this?", from shared/strings.json).
    let label: String

    @State private var open = false

    var body: some View {
        Button {
            open.toggle()
        } label: {
            Image(systemName: "info.circle")
                .foregroundStyle(open ? palette.accentInk : palette.textSecondary)
                .frame(minWidth: 44, minHeight: 44)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(label)
        .accessibilityHint(words)
        .popover(isPresented: $open) {
            Text(words)
                .font(.callout)
                .foregroundStyle(palette.textPrimary)
                .fixedSize(horizontal: false, vertical: true)
                .padding(14)
                .frame(maxWidth: 300)
                .presentationCompactAdaptation(.popover)
        }
    }
}

/// A player's head. A plain grey head for everyone until the app can get
/// real skins, as in the desktop app.
struct Face: View {
    let palette: Palette
    var side: CGFloat = 28

    var body: some View {
        ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6)
                .fill(palette.neutral)
                .overlay(RoundedRectangle(cornerRadius: 6).strokeBorder(palette.edge, lineWidth: 1.5))
            // Two eyes, on the 8 by 8 grid a Minecraft face is drawn on.
            let cell = side / 8
            ForEach([1.5, 5.0], id: \.self) { column in
                Rectangle()
                    .fill(palette.sheet)
                    .frame(width: 1.5 * cell, height: cell)
                    .offset(x: column * cell, y: 4 * cell)
            }
        }
        .frame(width: side, height: side)
        .accessibilityHidden(true)
    }
}
