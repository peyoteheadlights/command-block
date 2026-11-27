import Core
import SwiftUI

/// Server files: tap through the server's folder, download a file (then
/// the share sheet saves it to Files or sends it), upload one into the
/// folder that's open. The owner's alone (files.browse); the agent checks
/// every path and never replaces a file that's already there.
struct FilesView: View {
    let serverId: String

    @Environment(AppModel.self) private var model
    @State private var picking = false

    var body: some View {
        let pages = model.pages(serverId)
        let strings = model.strings
        ServerFrame(serverId: serverId, title: model.t("files.title")) { palette in
            List {
                Text(model.t("files.hint")).foregroundStyle(.secondary)
                HStack {
                    Button(model.t("files.up")) {
                        if let parent = pages.files.value?.parent { Task { await pages.loadFiles(parent) } }
                    }
                    .disabled(pages.files.value?.parent == nil)
                    Spacer()
                    Button(model.t("files.upload")) { picking = true }
                        .disabled(pages.files.value == nil || pages.filesBusy)
                }
                .buttonStyle(.borderless)
                Text(hiding: "/" + (pages.files.value?.path ?? "")).font(.footnote.monospaced())
                SaidRow(said: pages.filesSaid, palette: palette)
                if let problem = pages.files.problem {
                    ProblemCard(problem: problem, palette: palette) { await pages.loadFiles() }
                } else if let folder = pages.files.value {
                    if folder.entries.isEmpty { Text(model.t("files.empty")).foregroundStyle(.secondary) }
                    if folder.truncated { Text(model.t("files.truncated")).foregroundStyle(.secondary) }
                    ForEach(folder.entries) { entry in
                        if entry.folder {
                            Button(entry.name + "/") { Task { await pages.loadFiles(folder.inside(entry.name)) } }
                        } else {
                            Button {
                                Task { await pages.downloadFile(folder.inside(entry.name), strings) }
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(hiding: entry.name)
                                    Text(Display.bytes(strings, entry.size)).font(.footnote).foregroundStyle(.secondary)
                                }
                            }
                            .disabled(pages.filesBusy)
                        }
                    }
                } else {
                    ProgressView().frame(maxWidth: .infinity)
                }
            }
            .refreshable { await pages.loadFiles() }
            .fileImporter(isPresented: $picking, allowedContentTypes: [.item]) { result in
                if case .success(let url) = result, let file = try? PickedCopy.make(url) {
                    Task { await pages.uploadFile(file, strings) }
                }
            }
        }
        .task(id: serverId) { await pages.loadFiles("") }
        .sheet(
            item: Binding(get: { pages.sharingFile }, set: { _ in }),
            onDismiss: { pages.finishSharingFile() }
        ) { done in
            ShareSheet(file: done.file)
        }
    }
}
