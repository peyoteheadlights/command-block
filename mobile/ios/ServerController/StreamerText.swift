import Core
import Foundation
import SwiftUI

/// Streamer mode: addresses, folder paths and keys hidden on screen
/// (shared/streamer.json, mirrors StreamerText.kt). Every Text the app's
/// screens make from a String passes through here, the same idea as the
/// desktop app's one filter for the whole window. Text boxes keep their
/// real words, so saving never saves the dots.
enum StreamerMode {
    static let rules: Streamer? = SharedFiles.data("streamer", "json", bundle: .main).map { Streamer($0) }

    /// Kept on this phone, like the theme.
    static var on: Bool {
        get { UserDefaults.standard.bool(forKey: "streamer") }
        set { UserDefaults.standard.set(newValue, forKey: "streamer") }
    }

    static func shown(_ text: String) -> String {
        guard on, let rules else { return text }
        return rules.hide(text)
    }
}

extension Text {
    /// Text from a String: shown as typed, through streamer mode. Chosen
    /// over SwiftUI's own init for any String, so the screens need no change.
    init(_ content: String) {
        self.init(verbatim: StreamerMode.shown(content))
    }

    /// The same, for the places that used Text(verbatim:).
    init(hiding content: String) {
        self.init(verbatim: StreamerMode.shown(content))
    }
}
