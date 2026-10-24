import Core
import UIKit
import UserNotifications

/// Apple's side of lock-screen alerts: this iPhone's push address, and a
/// tap on an alert. The PC's push carries no words; the notification
/// extension (ServerControllerAlerts) reads the alert from the PC first.
final class PushDelegate: NSObject, UIApplicationDelegate, UNUserNotificationCenterDelegate {
    /// Set by AppModel: the address Apple gave (hex), or nil when it refused.
    @MainActor static var gotAddress: ((String?) -> Void)?
    /// Set by AppModel: an alert was tapped, with the link the extension added.
    @MainActor static var opened: ((URL) -> Void)?

    /// Start and Say no on a start request's alert. Both need the phone
    /// unlocked, so nobody can start a server from a locked phone.
    static let START = "start-request.start"
    static let SAY_NO = "start-request.no"

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        let center = UNUserNotificationCenter.current()
        center.delegate = self
        let strings = PushDelegate.strings()
        center.setNotificationCategories([
            UNNotificationCategory(
                identifier: Community.START_REQUEST_CATEGORY,
                actions: [
                    UNNotificationAction(identifier: PushDelegate.START, title: strings.t("desktop.start_request.start"), options: [.authenticationRequired]),
                    UNNotificationAction(identifier: PushDelegate.SAY_NO, title: strings.t("desktop.start_request.no"), options: [.authenticationRequired]),
                ],
                intentIdentifiers: []
            ),
        ])
        return true
    }

    func application(_ application: UIApplication, didRegisterForRemoteNotificationsWithDeviceToken deviceToken: Data) {
        let hex = deviceToken.map { String(format: "%02x", $0) }.joined()
        Task { @MainActor in PushDelegate.gotAddress?(hex) }
    }

    func application(_ application: UIApplication, didFailToRegisterForRemoteNotificationsWithError error: Error) {
        Task { @MainActor in PushDelegate.gotAddress?(nil) }
    }

    // While the app is open its Alerts tab already shows the alert.
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification) async -> UNNotificationPresentationOptions {
        [.list]
    }

    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse) async {
        let request = response.notification.request
        if response.actionIdentifier == PushDelegate.START || response.actionIdentifier == PushDelegate.SAY_NO,
           let serverId = request.content.userInfo["server"] as? String {
            await answer(request, serverId: serverId, start: response.actionIdentifier == PushDelegate.START)
            return
        }
        let link = (response.notification.request.content.userInfo["link"] as? String).flatMap(URL.init(string:)) ?? DeepLink.home
        await MainActor.run { PushDelegate.opened?(link) }
    }

    /// Sends the answer with the app's own sign-in (the app may not be open),
    /// then shows the same alert again saying what happened.
    private func answer(_ request: UNNotificationRequest, serverId: String, start: Bool) async {
        let strings = PushDelegate.strings()
        let words: String
        if let pairing = SharedStore().pairing, TokenStore.read() != nil {
            let client = AgentClient(pairing: pairing, token: { TokenStore.read() }, onSignedOut: { TokenStore.delete() })
            do {
                words = Community.answered(strings, try await client.answerStartRequest(serverId: serverId, start: start).answer)
            } catch let problem as AgentError {
                words = problem.message(strings)
            } catch {
                words = AgentError.unreachable.message(strings)
            }
        } else {
            words = strings.t("error.session_ended")
        }
        let content = UNMutableNotificationContent()
        content.title = request.content.title
        content.body = words
        content.threadIdentifier = request.content.threadIdentifier
        content.userInfo["link"] = request.content.userInfo["link"] as? String ?? DeepLink.home.absoluteString
        try? await UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: request.identifier, content: content, trigger: nil))
    }

    private static func strings() -> Strings {
        let strings = Strings(Bundled.english)
        strings.technical = SharedStore().technical
        return strings
    }
}
