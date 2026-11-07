import XCTest
@testable import Core

/// Diagnose server: the screen's rules and the agent's answers (mirrors DiagnoseTest.kt).
final class DiagnoseTests: XCTestCase {
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

    private func body(_ index: Int) -> String {
        String(data: StubAgent.requests[index].body, encoding: .utf8) ?? ""
    }

    func testLinesGoUnderANewHeadingEachTimeTheGroupChanges() {
        let lines = [
            DiagnoseLine(group: "Join from this PC", what: "Log in", ok: true),
            DiagnoseLine(group: "Join from this PC", what: "Arrive in the world", ok: false, detail: "Kicked"),
            DiagnoseLine(group: "Home network", what: "From this PC", ok: nil, detail: "Not checked"),
        ]
        let groups = Diagnose.groups(lines)
        XCTAssertEqual(groups.map(\.group), ["Join from this PC", "Home network"])
        XCTAssertEqual(groups[0].lines.count, 2)
        XCTAssertEqual(lines.map { Diagnose.mark($0.ok) }, ["✓", "✗", "?"])
    }

    func testChangesWaitForAPickOnlyOnceTheRunIsDone() {
        let change = DiagnoseChange(key: "restart", title: "Restart the server", safe: true)
        XCTAssertEqual(Diagnose.statusKey(nil), "diagnose.starting")
        XCTAssertEqual(Diagnose.statusKey(Diagnosis(running: true, changes: [change])), "diagnose.running")
        XCTAssertTrue(Diagnose.waiting(Diagnosis(running: true, changes: [change])).isEmpty)
        XCTAssertEqual(Diagnose.statusKey(Diagnosis(changes: [change])), "diagnose.offer")
        XCTAssertEqual(Diagnose.statusKey(Diagnosis()), "diagnose.done")
    }

    func testTheRunIsStartedReadAndPickedThroughTheAgent() async throws {
        let c = client()
        StubAgent.json("null")
        let none = try await c.diagnosis(serverId: "s")
        XCTAssertNil(none)
        XCTAssertEqual(StubAgent.requests[0].request.url?.path, "/api/servers/s/server/diagnose")

        StubAgent.json("{\"job_id\": null, \"ask_mods\": \"Join with all mods too?\"}")
        let asked = try await c.startDiagnose(serverId: "s", goAhead: false, mods: nil)
        XCTAssertEqual(asked.askMods, "Join with all mods too?")
        XCTAssertEqual(body(1), "{\"go_ahead\":false}")

        StubAgent.json("{\"job_id\": \"j\"}")
        let started = try await c.startDiagnose(serverId: "s", goAhead: true, mods: false)
        XCTAssertEqual(started.jobId, "j")
        XCTAssertEqual(body(2), "{\"go_ahead\":true,\"mods\":false}")

        StubAgent.json("{\"running\": false, \"lines\": [{\"group\": \"Home network\", \"what\": \"From this PC\", \"ok\": false, \"detail\": \"No answer\", \"port\": 25565}], \"changes\": [{\"key\": \"firewall\", \"title\": \"Let Minecraft through the firewall\", \"why\": \"Nothing answered\", \"safe\": false}], \"done\": [{\"title\": \"Restart\", \"safe\": true, \"outcome\": \"no_help\", \"detail\": \"Put back\"}], \"steps\": [], \"known\": [{\"id\": \"oom\", \"say\": \"The server ran out of memory.\", \"link\": null, \"change\": \"Give it 1 GB more memory\"}], \"sign_in\": true}")
        let result = try await c.diagnosis(serverId: "s")
        XCTAssertEqual(result.map { Diagnose.waiting($0).map(\.title) }, ["Let Minecraft through the firewall"])
        XCTAssertEqual(result?.known.first?.change, "Give it 1 GB more memory")
        XCTAssertEqual(result?.done.first.map(Diagnose.doneMark), "–")
        XCTAssertEqual(result?.signIn, true)

        StubAgent.json("{\"job_id\": \"k\"}")
        _ = try await c.diagnoseChanges(serverId: "s", pick: "safe")
        XCTAssertEqual(StubAgent.requests[4].request.url?.path, "/api/servers/s/server/diagnose/changes")
        XCTAssertEqual(body(4), "{\"pick\":\"safe\"}")
    }
}
