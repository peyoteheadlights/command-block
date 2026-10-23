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

    func application(_ application: UIApplication, didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UNUserNotificationCenter.current().delegate = self
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
        let link = (response.notification.request.content.userInfo["link"] as? String).flatMap(URL.init(string:)) ?? DeepLink.home
        await MainActor.run { PushDelegate.opened?(link) }
    }
}
