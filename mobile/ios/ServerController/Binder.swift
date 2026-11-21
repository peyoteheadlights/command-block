import Core
import SwiftUI

// The top view on a phone, as in the desktop app: the page tips back and
// every server's tab stands behind it, one step apart. Pulling the band
// under the top bar down opens it, and a tap on a tab opens that server.
// Pushing it back up or a tap on the page closes it. With Reduce Motion on
// nothing glides: it is just open or closed.

/// One tab's room in the top view, and how many show before the list scrolls.
private let step: CGFloat = 52
private let most = 6

extension View {
    /// Puts a screen in the binder. `current` is the server the screen is
    /// of, or nil for the list of all servers.
    func binder(current: String?) -> some View {
        modifier(Binder(current: current))
    }
}

private struct Binder: ViewModifier {
    let current: String?

    @Environment(AppModel.self) private var model
    @Environment(\.colorScheme) private var colorScheme
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    /// 0 is the page as usual, 1 the top view.
    @State private var at: CGFloat = 0
    @State private var wasOpen = false

    private var strip: [String?] {
        [nil] + model.servers.map { $0.id }
    }

    private var distance: CGFloat {
        step * CGFloat(min(strip.count, most)) + 8
    }

    func body(content: Content) -> some View {
        let palette = model.serverPalette(model.servers.first(where: { $0.id == current })?.color, colorScheme)
        ZStack(alignment: .top) {
            palette.desk.ignoresSafeArea(edges: .bottom)
            if at > 0 {
                tabs(palette)
                    .opacity(Double(at))
            }
            content
                .safeAreaInset(edge: .top, spacing: 0) {
                    band(palette)
                }
                .rotation3DEffect(.degrees(TopView.tip * Double(at)), axis: (x: 1, y: 0, z: 0), anchor: .top, perspective: 0.6)
                .scaleEffect(1 - 0.14 * at, anchor: .top)
                .offset(y: at * distance)
                .overlay {
                    if at > 0 {
                        // While it is tipped back the page is only a way back.
                        Color.clear
                            .contentShape(Rectangle())
                            .onTapGesture { show(false) }
                            .gesture(pull)
                            .accessibilityLabel(model.t("mobile.common.close"))
                            .accessibilityAddTraits(.isButton)
                    }
                }
        }
        .toolbarBackground(palette.band, for: .navigationBar)
        .toolbarBackground(.visible, for: .navigationBar)
    }

    /// The band under the top bar, with the top edge of the sheet lying on
    /// it. Pulling it down opens the top view.
    private func band(_ palette: Palette) -> some View {
        VStack(spacing: 0) {
            Capsule()
                .fill(palette.bandText.opacity(0.35))
                .frame(width: 36, height: 5)
                .padding(.vertical, 6)
            UnevenRoundedRectangle(topLeadingRadius: 14, topTrailingRadius: 14)
                .fill(palette.sheet)
                .frame(height: 14)
        }
        .frame(maxWidth: .infinity)
        .background(palette.band)
        .contentShape(Rectangle())
        .gesture(pull)
        // For VoiceOver, which can't pull: the top view as a button.
        .accessibilityElement()
        .accessibilityLabel(model.t("page.servers"))
        .accessibilityAddTraits(.isButton)
        .accessibilityAction { show(at < 0.5) }
    }

    private var pull: some Gesture {
        DragGesture(minimumDistance: 8)
            .onChanged { drag in
                if drag.translation == .zero || reduceMotion { return }
                let from: CGFloat = wasOpen ? 1 : 0
                at = min(1, max(0, from + drag.translation.height / distance))
            }
            .onEnded { drag in
                show(TopView.opens(wasOpen: wasOpen, pulled: Double(drag.translation.height), distance: Double(distance)))
            }
    }

    private func show(_ open: Bool) {
        wasOpen = open
        let to: CGFloat = open ? 1 : 0
        if reduceMotion {
            at = to
        } else {
            withAnimation(.timingCurve(0.22, 1, 0.36, 1, duration: TopView.seconds)) {
                at = to
            }
        }
    }

    /// The tabs, the furthest at the top and the open one at the bottom,
    /// right behind the page.
    private func tabs(_ palette: Palette) -> some View {
        let order = TopView.order(strip, open: current)
        return ScrollView {
            VStack(spacing: 0) {
                ForEach(Array(order.enumerated().reversed()), id: \.offset) { depth, id in
                    tab(id, depth: depth, place: (strip.firstIndex(of: id) ?? 0) % 3, palette: palette)
                }
            }
        }
        .defaultScrollAnchor(.bottom)
        .frame(height: distance)
    }

    /// One divider of the binder: its tab, and the top edge of its sheet.
    private func tab(_ id: String?, depth: Int, place: Int, palette: Palette) -> some View {
        let row = model.servers.first(where: { $0.id == id })
        let look = model.serverPalette(row?.color, colorScheme)
        let open = id == current
        // Read before the last failed attempt: not current, so no dot.
        let running = model.serversFresh && row?.state == States.ONLINE
        return VStack(alignment: .leading, spacing: 0) {
            Button {
                show(false)
                if !open {
                    if let id = id {
                        model.openServer(id)
                    } else {
                        model.serversPath = []
                    }
                }
            } label: {
                HStack(spacing: 8) {
                    if running {
                        // The green dot of a running server, as on the desktop app's tabs.
                        Circle()
                            .fill(palette.success)
                            .frame(width: 8, height: 8)
                    }
                    Text(row?.name ?? model.t("page.servers"))
                        .font(.heading(.subheadline))
                        .fontWeight(open ? .bold : .medium)
                        .foregroundStyle(look.tabText)
                        .lineLimit(1)
                }
                .padding(.horizontal, 14)
                .frame(minWidth: 96, maxWidth: 220, minHeight: step - 8, alignment: .leading)
                .background(look.tabFill, in: UnevenRoundedRectangle(topLeadingRadius: 10, topTrailingRadius: 10))
            }
            .buttonStyle(.plain)
            .padding(.leading, 12 + 56 * CGFloat(place))
            .accessibilityAddTraits(open ? .isSelected : [])
            UnevenRoundedRectangle(topLeadingRadius: 6, topTrailingRadius: 6)
                .fill(palette.sheet)
                .frame(height: 8)
        }
        .padding(.horizontal, 12 + 4 * CGFloat(min(depth, 5)))
        .frame(height: step)
    }
}
