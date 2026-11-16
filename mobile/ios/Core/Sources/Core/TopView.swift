import Foundation

/// The top view's rules that don't need a screen: the binder tips back and
/// every server's tab stands behind it, one step apart. On a phone it opens
/// by pulling the band (under the top bar) down. The Android app has the
/// same rules (core/TopView.kt).
public enum TopView {
    /// Degrees the page tips back.
    public static let tip: Double = 14

    /// The design's time for a binder move, in seconds, with its curve
    /// cubic-bezier(0.22, 1, 0.36, 1).
    public static let seconds: Double = 0.62

    /// How far of the way a pull must go before letting go finishes it.
    public static let enough: Double = 0.25

    /// The tabs from the front one back: the open one, then the ones after
    /// it on the strip, then around to the ones before it.
    public static func order<T: Equatable>(_ strip: [T], open: T) -> [T] {
        let at = strip.firstIndex(of: open) ?? 0
        return Array(strip[at...] + strip[..<at])
    }

    /// Whether the top view is open after a pull is let go. `pulled` is how
    /// far the finger went (down is positive), `distance` the whole way.
    public static func opens(wasOpen: Bool, pulled: Double, distance: Double) -> Bool {
        wasOpen ? pulled > -distance * enough : pulled > distance * enough
    }
}
