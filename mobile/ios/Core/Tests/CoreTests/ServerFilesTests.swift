import XCTest
@testable import Core

/// Server files, the graphs as CSV, and the Technical settings group
/// (mirrors ServerFilesTest.kt).
final class ServerFilesTests: XCTestCase {
    private let token = "secret-session-token-1234567890"

    override func setUp() {
        super.setUp()
        StubAgent.reset()
    }

    private func client() -> AgentClient {
        let value = token
        return AgentClient(
            pairing: Pairing(host: "localhost", port: 8765, fingerprint: String(repeating: "ab", count: 32), apiVersion: 1),
            token: { value },
            configuration: StubAgent.configuration()
        )
    }

    func testAFolderListsFoldersAndFilesAndPathsUseSlashes() async throws {
        StubAgent.json(#"{"path": "plugins", "parent": "", "entries": [{"name": "Thing", "folder": true, "size": null, "changed": 1.0}, {"name": "a.yml", "folder": false, "size": 12, "changed": 2.0}], "truncated": false}"#)
        let found = try await client().serverFiles(serverId: "a", folder: "plugins")
        XCTAssertEqual(found.parent, "")
        XCTAssertNil(found.entries[0].size)
        XCTAssertEqual(found.inside("a.yml"), "plugins/a.yml")
        XCTAssertEqual(ServerFiles(path: "").inside("x.txt"), "x.txt")
        StubAgent.json(#"{"filename": "A-performance.csv", "text": "time\n"}"#)
        let csv = try await client().performanceCsv(serverId: "a", hours: 24)
        XCTAssertEqual(csv.filename, "A-performance.csv")
        XCTAssertEqual(StubAgent.requests.map { $0.request.url?.path }, [
            "/api/servers/a/files", "/api/servers/a/performance/csv",
        ])
        XCTAssertEqual(StubAgent.requests[0].request.url?.query, "path=plugins")
    }

    func testExitCodesAndPriorityAreATechnicalGroup() {
        let group = ServerForm.GROUPS.filter(\.technical)
        XCTAssertEqual(group.first?.fields.map(\.key), ["monitor.ignored_exit_codes", "server.low_priority"])
        XCTAssertEqual(group.count, 1)
    }
}
