import XCTest
@testable import Core

final class PlayitTests: XCTestCase {
    private let s = Strings(Shared.strings)

    private func state(_ text: String) throws -> PlayitState {
        try JSONDecoder().decode(PlayitState.self, from: Data(text.utf8))
    }

    func testNotInstalledTellsTheOwnerToInstallItOnThePc() throws {
        let step = Playit.step(s, try state(#"{"on": false, "phase": "not_installed"}"#), owner: true)
        XCTAssertNil(step.button)
        XCTAssertEqual(step.lines.last?.text, s.t("playit.not_here"))
    }

    func testAMissingTunnelSaysHowToMakeItAndAFoundOneShowsItsAddress() throws {
        let missing = try state(#"{"on": true, "phase": "running", "proxy": "v2", "ways": [{"kind": "java", "local_port": 25565, "tunnel": null}]}"#)
        let step = Playit.step(s, missing, owner: true)
        XCTAssertEqual(step.button, "make")
        XCTAssertTrue(step.checkAgain)
        XCTAssertTrue(step.lines.last?.text.contains("25565") ?? false)
        let found = try state(#"{"on": true, "phase": "running", "ways": [{"kind": "java", "local_port": 25565, "tunnel": {"address": "cool.gl.joinmc.link"}}]}"#)
        let shown = Playit.step(s, found, owner: true)
        XCTAssertNil(shown.button)
        XCTAssertEqual(shown.lines.first?.text, s.t("playit.join_java", ["address": "cool.gl.joinmc.link", "port": "25565"]))
        XCTAssertEqual(Playit.javaAddress(found), "cool.gl.joinmc.link")
    }

    func testAStoppedProgramOffersReconnect() throws {
        let step = Playit.step(s, try state(#"{"on": true, "phase": "error", "problem": "down"}"#), owner: true)
        XCTAssertEqual(step.button, "reconnect")
        XCTAssertEqual(step.lines.first?.tone, .danger)
    }
}
