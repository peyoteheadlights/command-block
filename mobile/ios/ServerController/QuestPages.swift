import Core
import Foundation
import Observation

/// One server's Quests and Community events (mirrors QuestPages.kt): the quests and
/// their board, "done" claims to approve, ideas from /quest suggest, a new
/// quest, the Community events with their attendance, new ones, and the polls. The agent
/// checks every value and the "Quests and events" permission again.
@MainActor
@Observable
final class QuestPages {
    let serverId: String
    @ObservationIgnored private let client: () -> AgentClient?
    @ObservationIgnored private let noted: (AgentError) -> Void

    init(serverId: String, client: @escaping () -> AgentClient?, noted: @escaping (AgentError) -> Void) {
        self.serverId = serverId
        self.client = client
        self.noted = noted
    }

    private(set) var page = PageRead<QuestsPage>()
    private(set) var events = PageRead<EventsPage>()
    /// "end:3", "claim:4", "make" while that button works.
    private(set) var busy: String? = nil
    var problem: String? = nil
    /// What the agent said was wrong with the new quest, kept by the form.
    var formProblem: String? = nil
    /// The one claim screenshot shown (claim id and the picture's bytes);
    /// showing another lets this one go, so a long session keeps one picture.
    var screenshot: (claimId: Int64, data: Data)? = nil

    func load() async {
        guard let client = client() else { return }
        page.loading = true
        events.loading = true
        do {
            page = PageRead(value: try await client.quests(serverId: serverId))
        } catch let problem as AgentError {
            noted(problem)
            page.problem = problem
            page.loading = false
        } catch {
            page.loading = false
        }
        do {
            events = PageRead(value: try await client.questEvents(serverId: serverId))
        } catch let problem as AgentError {
            noted(problem)
            events.problem = problem
            events.loading = false
        } catch {
            events.loading = false
        }
    }

    /// True once the agent made it, to empty the form.
    func make(_ form: QuestForm, _ strings: Strings) async -> Bool {
        var made = false
        formProblem = nil
        await work("make", strings) { [weak self] client, id in
            do {
                self?.page = PageRead(value: try await client.makeQuest(serverId: id, form: form))
                made = true
            } catch let refused as AgentError {
                guard case .failed = refused else { throw refused }
                self?.formProblem = refused.message(strings)
            }
        }
        return made
    }

    func end(_ questId: Int64, _ strings: Strings) async {
        await work("end:\(questId)", strings) { [weak self] client, id in
            self?.page = PageRead(value: try await client.endQuest(serverId: id, questId: questId))
        }
    }

    func answerClaim(_ claimId: Int64, approve: Bool, _ strings: Strings) async {
        await work("claim:\(claimId)", strings) { [weak self] client, id in
            self?.page = PageRead(value: try await client.answerClaim(serverId: id, claimId: claimId, approve: approve))
        }
    }

    func turnDown(_ ideaId: Int64, _ strings: Strings) async {
        await work("idea:\(ideaId)", strings) { [weak self] client, id in
            self?.page = PageRead(value: try await client.turnDownIdea(serverId: id, ideaId: ideaId))
        }
    }

    func showScreenshot(_ claimId: Int64, _ strings: Strings) async {
        await work("shot:\(claimId)", strings) { [weak self] client, id in
            let shot = try await client.claimScreenshot(serverId: id, claimId: claimId)
            if let data = Data(base64Encoded: shot.image) {
                self?.screenshot = (claimId: claimId, data: data)
            } else {
                self?.problem = strings.t("quests.screenshot_unreadable")
            }
        }
    }

    /// What the agent said was wrong with the new Community event or poll ("event" or "poll").
    var eventProblem: (which: String, text: String)? = nil

    /// True once the agent made it, to empty the form. A time that can't be read says so here.
    func makeEvent(_ form: EventForm, _ strings: Strings) async -> Bool {
        await made("event", Community.eventBody(form), strings) { client, id, body in
            try await client.makeEvent(serverId: id, body: body)
        }
    }

    func makePoll(_ form: PollForm, _ strings: Strings) async -> Bool {
        await made("poll", Community.pollBody(form), strings) { client, id, body in
            try await client.makePoll(serverId: id, body: body)
        }
    }

    private func made(_ which: String, _ body: [String: JSONValue]?, _ strings: Strings,
                      _ call: @escaping (AgentClient, String, [String: JSONValue]) async throws -> EventsPage) async -> Bool {
        guard let body else {
            eventProblem = (which, strings.t("mobile.quests.when_hint"))
            return false
        }
        var made = false
        eventProblem = nil
        await work("make:\(which)", strings) { [weak self] client, id in
            do {
                self?.events = PageRead(value: try await call(client, id, body))
                made = true
            } catch let refused as AgentError {
                guard case .failed = refused else { throw refused }
                self?.eventProblem = (which, refused.message(strings))
            }
        }
        return made
    }

    func callOff(_ eventId: Int64, _ strings: Strings) async {
        await work("event:\(eventId)", strings) { [weak self] client, id in
            self?.events = PageRead(value: try await client.callOffEvent(serverId: id, eventId: eventId))
        }
    }

    func approve(_ eventId: Int64, _ strings: Strings) async {
        await work("event:\(eventId)", strings) { [weak self] client, id in
            self?.events = PageRead(value: try await client.approveAttendance(serverId: id, eventId: eventId))
        }
    }

    func fix(_ eventId: Int64, _ discordId: String, attended: Bool, _ strings: Strings) async {
        await work("fix:\(eventId):\(discordId)", strings) { [weak self] client, id in
            self?.events = PageRead(value: try await client.fixAttendance(serverId: id, eventId: eventId, discordId: discordId, attended: attended))
        }
    }

    func closePoll(_ pollId: Int64, _ strings: Strings) async {
        await work("poll:\(pollId)", strings) { [weak self] client, id in
            self?.events = PageRead(value: try await client.closePoll(serverId: id, pollId: pollId))
        }
    }

    /// One button at a time; what went wrong stays in `problem`.
    private func work(_ name: String, _ strings: Strings, _ call: (AgentClient, String) async throws -> Void) async {
        guard let client = client(), busy == nil else { return }
        busy = name
        problem = nil
        do {
            try await call(client, serverId)
        } catch let refused as AgentError {
            noted(refused)
            problem = refused.message(strings)
        } catch {}
        busy = nil
    }
}
