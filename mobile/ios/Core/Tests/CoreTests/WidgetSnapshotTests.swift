import XCTest
@testable import Core

final class WidgetSnapshotTests: XCTestCase {
    private let list = ServerList(servers: [
        ServerRow(id: "survival", name: "Survival", color: "#1967D4", state: "ONLINE", playersOnline: 3, maxPlayers: 20, label: "not_answering"),
    ])

    func testAFreshReadIsShownAsItIs() {
        let snap = WidgetSnapshot.read(list, now: 1000)
        XCTAssertEqual(snap.shown(1100).servers.first?.state, "ONLINE")
        // A stuck server reads "Not answering" on the home screen too.
        XCTAssertEqual(snap.shown(1100).servers.first?.label, "not_answering")
        XCTAssertEqual(snap.shown(1100).servers.first?.playersOnline, 3)
        XCTAssertTrue(snap.isFresh(1100))
    }

    func testAnOldReadTurnsUnknownInsteadOfLookingCurrent() {
        let snap = WidgetSnapshot.read(list, now: 1000)
        let later = snap.shown(1000 + WidgetSnapshot.FRESH_FOR + 1)
        XCTAssertEqual(later.servers.first?.state, "UNKNOWN")
        XCTAssertEqual(later.servers.first?.label, "unknown")
        XCTAssertNil(later.servers.first?.playersOnline)
        XCTAssertEqual(later.checkedAt, 1000)
    }

    func testAnUnreachablePCKeepsTheNamesNotWhatTheyWereDoing() {
        let before = WidgetSnapshot.read(list, now: 1000)
        let snap = WidgetSnapshot.failed(before, problem: .unreachable, now: 2000)
        XCTAssertFalse(snap.reached)
        XCTAssertEqual(snap.problemKey, "mobile.error.unreachable")
        XCTAssertEqual(snap.servers.first?.name, "Survival")
        XCTAssertEqual(snap.shown(2000).servers.first?.state, "UNKNOWN")
        XCTAssertNil(snap.servers.first?.playersOnline)
        XCTAssertEqual(WidgetSnapshot.failed(nil, problem: .unreachable, now: 1).servers, [])
    }

    func testTheSnapshotIsSavedAndReadBack() throws {
        let snap = WidgetSnapshot.read(list, now: 1000)
        let data = try JSONEncoder().encode(snap)
        XCTAssertEqual(try JSONDecoder().decode(WidgetSnapshot.self, from: data), snap)
    }
}
