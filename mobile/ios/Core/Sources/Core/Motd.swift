import Foundation

/// The MOTD builder's colour codes. Minecraft writes colours into the text
/// itself: a § and one letter or digit (§6 gold, §l bold, §r back to plain).
/// Same table as the desktop app (desktop/motd.py) and Motd.kt.
public enum Motd {
    /// Minecraft's 16 text colours, by code, as 0xRRGGBB, in order.
    public static let colors: [(code: Character, rgb: Int)] = [
        ("0", 0x000000), ("1", 0x0000AA), ("2", 0x00AA00), ("3", 0x00AAAA),
        ("4", 0xAA0000), ("5", 0xAA00AA), ("6", 0xFFAA00), ("7", 0xAAAAAA),
        ("8", 0x555555), ("9", 0x5555FF), ("a", 0x55FF55), ("b", 0x55FFFF),
        ("c", 0xFF5555), ("d", 0xFF55FF), ("e", 0xFFFF55), ("f", 0xFFFFFF),
    ]

    /// The style buttons: code and its words' key.
    public static let styles: [(code: Character, key: String)] = [
        ("l", "motd.bold"), ("o", "motd.italic"), ("n", "motd.underline"), ("m", "motd.strike"), ("r", "motd.plain"),
    ]

    /// What the server list shows text in before any colour.
    public static let plain = 0xAAAAAA

    /// The keys that get the builder: Java's MOTD, a Bedrock server's name.
    public static let keys: Set<String> = ["motd", "server-name"]

    public struct Piece: Equatable, Sendable {
        public var text: String
        public var color: Int = Motd.plain
        public var bold = false
        public var italic = false
        public var underline = false
        public var strike = false
    }

    /// The text cut where its style changes. A colour code ends bold,
    /// italic and the rest, the way Java Edition draws it.
    public static func pieces(_ motd: String) -> [Piece] {
        var out: [Piece] = []
        var style = Piece(text: "")
        let chars = Array(motd)
        var i = 0
        while i < chars.count {
            if chars[i] == "§" && i + 1 < chars.count {
                let code = chars[i + 1].lowercased().first ?? chars[i + 1]
                if let found = colors.first(where: { $0.code == code }) {
                    style = Piece(text: "", color: found.rgb)
                } else if code == "l" {
                    style.bold = true
                } else if code == "o" {
                    style.italic = true
                } else if code == "n" {
                    style.underline = true
                } else if code == "m" {
                    style.strike = true
                } else if code == "r" {
                    style = Piece(text: "")
                }  // unknown codes (and §k, scrambled) show nothing
                i += 2
                continue
            }
            var end = i
            while end < chars.count && chars[end] != "§" { end += 1 }
            var piece = style
            piece.text = String(chars[i..<end])
            out.append(piece)
            i = end
        }
        return out
    }
}

/// Streamer mode: addresses, folder paths and keys hidden on screen. The
/// rules are shared/streamer.json, the same file the desktop app reads.
public struct Streamer {
    public let hidden: String
    public let cases: [(String, String)]
    private let pattern: NSRegularExpression?

    public init(_ data: Data) {
        let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
        hidden = root["hidden"] as? String ?? "••••"
        let patterns = root["patterns"] as? [String] ?? []
        pattern = try? NSRegularExpression(pattern: patterns.map { "(?:\($0))" }.joined(separator: "|"))
        cases = (root["cases"] as? [[String]] ?? []).compactMap { $0.count == 2 ? ($0[0], $0[1]) : nil }
    }

    /// `text` with every address, path and key swapped for dots.
    public func hide(_ text: String) -> String {
        guard let pattern else { return text }
        let range = NSRange(text.startIndex..., in: text)
        return pattern.stringByReplacingMatches(
            in: text, range: range, withTemplate: NSRegularExpression.escapedTemplate(for: hidden)
        )
    }
}
