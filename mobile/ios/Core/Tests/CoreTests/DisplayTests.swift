import XCTest
@testable import Core

final class DisplayTests: XCTestCase {
    private let s = Strings(Shared.strings)

    func testTheAgentsLabelReadsTheSameInEveryApp() {
        XCTAssertEqual(s.t(Display.state("online").key), "Online")
        XCTAssertEqual(s.t(Display.state("not_answering").key), "Not answering")
        XCTAssertEqual(Display.state("not_answering").tone, .danger)
        XCTAssertEqual(s.t(Display.state("crashed").key), "Crashed")
        XCTAssertEqual(s.t(Display.state("something_new").key), "Unknown")
        XCTAssertEqual(s.t(Display.state(nil).key), "Unknown")
        XCTAssertTrue(Display.state("starting").busy)
    }

    func testARentedServersPanelStateReadsTheSameWords() throws {
        XCTAssertEqual(s.t(Display.state("ONLINE").key), "Online")
        XCTAssertEqual(s.t(Display.state("OFFLINE").key), "Offline")
        let json = #"{"id": "s", "name": "S", "state": "ONLINE", "answering": {"ok": false, "checked_at": 1.0}}"#
        let row = try JSONDecoder().decode(ServerRow.self, from: Data(json.utf8))
        XCTAssertEqual(row.answering?.ok, false)
    }

    func testDurationsAndPlayers() {
        XCTAssertEqual(Display.duration(s, 40.9), "40s")
        XCTAssertEqual(Display.duration(s, 300.0), "5m")
        XCTAssertEqual(Display.duration(s, 7800.0), "2h 10m")
        XCTAssertEqual(Display.duration(s, 3 * 86400.0 + 4 * 3600), "3d 4h")
        XCTAssertEqual(Display.ago(s, 1000.0, now: 1300.0), "5m ago")
        XCTAssertEqual(Display.ago(s, nil, now: 1300.0), "Never")
        XCTAssertEqual(Display.players(s, 3, 20), "3 of 20")
        XCTAssertEqual(Display.players(s, nil, 20), "Unknown")
        XCTAssertEqual(Display.uptime(s, "ONLINE", 300.0), "Up for 5m")
        XCTAssertNil(Display.uptime(s, "ONLINE", nil))
        XCTAssertNil(Display.uptime(s, "OFFLINE", 300.0))
    }

    func testTheButtonsAreTheAgentsListStartingAsksNothingTheRestAskFirst() {
        XCTAssertEqual(Display.actions(["start", "change"], canControl: true).map { $0.action }, ["start"])
        XCTAssertEqual(Display.actions(["start"], canControl: true).first?.confirm, false)
        let online = Display.actions(["stop", "restart"], canControl: true)
        XCTAssertEqual(online.map { $0.action }, ["restart", "stop"])
        XCTAssertTrue(online.allSatisfy { $0.confirm })
        let left = Display.actions(["force_stop"], canControl: true)
        XCTAssertEqual(left.map { $0.labelKey }, ["action.force_stop"])
        XCTAssertTrue(left.allSatisfy { $0.confirm && $0.danger })
        XCTAssertEqual(Display.actions([], canControl: true), [])
        XCTAssertEqual(Display.actions(["start"], canControl: false), [])
    }

    func testValuesTheAgentDidntGiveReadUnknown() {
        XCTAssertEqual(Display.onlineNow(s, nil, 20), "Players not known")
        XCTAssertEqual(Display.onlineNow(s, 3, 20), "Online now (3 of 20)")
        XCTAssertEqual(Display.onlineNow(s, 3, nil), "Online now (3)")
        XCTAssertEqual(Display.exitCode(s, nil), "Unknown")
        XCTAssertEqual(Display.exitCode(s, 1), "1")
        XCTAssertEqual(Display.playingFor(s, OnlinePlayer(username: "Alex", sessionSeconds: 0)), "Unknown")
        XCTAssertEqual(Display.playingFor(s, OnlinePlayer(username: "Alex", sessionSeconds: 300, sessionStarted: 1)), "5m")
        XCTAssertEqual(s.t(Display.actionState("something_new").key), "Unknown")
        XCTAssertEqual(s.t(Display.confidence(nil).key), "Not sure")
    }

    func testCrashCausesAndConfidenceReadTheSameInEveryApp() {
        XCTAssertEqual(Display.cause(s, nil), "Not known")
        XCTAssertEqual(Display.cause(s, "Unknown"), "Not known")
        XCTAssertEqual(Display.cause(s, "OutOfMemoryError"), "The server ran out of memory")
        XCTAssertEqual(Display.cause(s, "SomethingNew"), "SomethingNew")
        XCTAssertEqual(Display.confidence("confirmed").tone, .danger)
        XCTAssertEqual(s.t(Display.confidence("likely").key), "Likely")
        let technical = Strings(Shared.strings)
        technical.technical = true
        XCTAssertEqual(Display.cause(technical, "OutOfMemoryError"), "OutOfMemoryError")
    }

    func testActivityAndClock() {
        XCTAssertEqual(Display.eventTone("error"), .danger)
        XCTAssertEqual(Display.eventTone(nil), .neutral)
        XCTAssertTrue(Display.worthShowing("server_started", technical: false))
        XCTAssertFalse(Display.worthShowing("state", technical: false))
        XCTAssertTrue(Display.worthShowing("state", technical: true))
        let utc = TimeZone(identifier: "UTC")!
        XCTAssertEqual(Display.clock(1_700_000_000, zone: utc), "22:13:20")
        XCTAssertEqual(Display.dateTime(1_700_000_000, zone: utc), "2023-11-14 22:13:20")
    }

    func testChangesWaitingForARestartOfferRestartWhenNobodysOn() throws {
        let json = #"{"state": "ONLINE", "restart_waiting": ["mods", "settings"], "restart_when_empty": false}"#
        var status = try JSONDecoder().decode(ServerStatus.self, from: Data(json.utf8))
        let waiting = try XCTUnwrap(Display.restartWaiting(s, status, canControl: true))
        XCTAssertEqual(waiting.words, "Changes are waiting for a restart: mods, game settings.")
        XCTAssertEqual(waiting.action?.action, "restart-when-empty")
        XCTAssertNil(Display.restartWaiting(s, status, canControl: false)?.action)
        status.restartWhenEmpty = true
        XCTAssertEqual(Display.restartWaiting(s, status, canControl: true)?.action?.action, "cancel-restart-when-empty")
        // An older agent sends neither field: nothing shows.
        let old = try JSONDecoder().decode(ServerStatus.self, from: Data(#"{"state": "ONLINE"}"#.utf8))
        XCTAssertNil(Display.restartWaiting(s, old, canControl: true))
    }
}
