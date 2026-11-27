import XCTest
@testable import Core

/// The app tints a server's screens with exactly the colors the desktop app
/// does: every palette color in every theme, against the recorded cases.
final class ServerColorsTests: XCTestCase {
    private let themes = ServerColors.parseThemes(Shared.data("theme.json"))

    private func tokens(_ theme: String) -> [String: String] {
        var out: [String: String] = [:]
        for (key, value) in themes[theme] ?? [:] {
            out[key.hasPrefix("--") ? String(key.dropFirst(2)) : key] = value
        }
        return out
    }

    func testEveryColorInEveryThemeMatchesTheDesktopApp() throws {
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: Shared.data("color-cases.json")) as? [[String: Any]])
        XCTAssertGreaterThanOrEqual(cases.count, 40)
        for item in cases {
            let theme = try XCTUnwrap(item["theme"] as? String)
            let base = try XCTUnwrap(item["base"] as? String)
            let expected = try XCTUnwrap(item["tokens"] as? [String: String])
            XCTAssertEqual(ServerColors.derive(base, tokens(theme), theme), expected, "\(theme) \(base)")
        }
    }

    func testTheBinderLookMatchesTheRecordedCases() throws {
        let cases = try XCTUnwrap(JSONSerialization.jsonObject(with: Shared.data("look-cases.json")) as? [[String: Any]])
        XCTAssertEqual(cases.count, 52)
        for item in cases {
            let theme = try XCTUnwrap(item["theme"] as? String)
            let base = item["base"] as? String
            let expected = try XCTUnwrap(item["tokens"] as? [String: String])
            XCTAssertEqual(ServerColors.binder(base, tokens(theme), theme), expected, "\(theme) \(base ?? "no server")")
        }
    }

    func testABrokenServerColorGetsTheLookOfNoServer() throws {
        XCTAssertEqual(ServerColors.binder("red", tokens("light"), "light"), ServerColors.binder(nil, tokens("light"), "light"))
        // The words on the band read on it in every theme and color.
        for theme in themes.keys {
            for base in [nil, "#FFFF00", "#000000", "#FFFFFF", "#7F7F7F"] as [String?] {
                let look = ServerColors.binder(base, tokens(theme), theme)
                let text = try XCTUnwrap(ServerColors.rgb(look["--band-text"]))
                let band = try XCTUnwrap(ServerColors.rgb(look["--band"]))
                XCTAssertGreaterThanOrEqual(ServerColors.contrast(text, band), 4.5, "\(theme) \(base ?? "no server") band")
            }
        }
    }

    func testColorsPeopleReadPassWcagAA() throws {
        XCTAssertEqual(Set(themes.keys), ["light", "dark", "graphite", "contrast"])
        for theme in themes.keys {
            for base in ["#FFFF00", "#000000", "#FFFFFF", "#7F7F7F", "#1967D4"] {
                let out = try XCTUnwrap(ServerColors.derive(base, tokens(theme), theme))
                func c(_ name: String) throws -> [Int] {
                    try XCTUnwrap(ServerColors.rgb(out[name]))
                }
                for bg in ["--surface", "--sheet"] {
                    XCTAssertGreaterThanOrEqual(ServerColors.contrast(try c("--accent-ink"), try c(bg)), 4.5, "\(theme) \(base) text on \(bg)")
                }
                XCTAssertGreaterThanOrEqual(ServerColors.contrast(try c("--accent"), try c("--accent-text")), 4.5, "\(theme) \(base) button")
            }
        }
    }

    func testAColorThatIsntOneIsIgnored() {
        XCTAssertNil(ServerColors.derive("red", [:], "light"))
        XCTAssertNil(ServerColors.badge(nil))
        let badge = ServerColors.badge("#ffffff")
        XCTAssertEqual(badge?.fill, "#FFFFFF")
        XCTAssertEqual(badge?.text, "#111111")
    }
}
