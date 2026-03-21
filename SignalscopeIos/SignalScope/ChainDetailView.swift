import SwiftUI
import AVFoundation

struct ChainDetailView: View {
    @EnvironmentObject private var appModel: AppModel

    let chainID: String
    let initialChain: ChainSummary

    @State private var chain: ChainSummary
    @State private var isLoading = false
    @State private var errorMessage: String?
    @State private var selectedAudio: AudioTarget?
    @State private var jumpTargetID: String?

    init(chainID: String, initialChain: ChainSummary) {
        self.chainID = chainID
        self.initialChain = initialChain
        _chain = State(initialValue: initialChain)
    }

    var body: some View {
        ScrollViewReader { proxy in
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    summaryCard(proxy: proxy)
                    nodesCard
                }
                .padding()
            }
            .background(Theme.backgroundGradient.ignoresSafeArea())
            .navigationTitle(chain.name)
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if isLoading {
                        ProgressView()
                            .tint(Theme.brandBlue)
                    }
                }
            }
            .refreshable {
                await loadDetail()
            }
            .task {
                await loadDetail()
            }
            .sheet(item: $selectedAudio) { target in
                AudioPlayerSheet(target: target)
            }
            .onChange(of: jumpTargetID) { _, newValue in
                guard let newValue else { return }
                withAnimation(.easeInOut) {
                    proxy.scrollTo(newValue, anchor: .center)
                }
            }
        }
    }

    private func summaryCard(proxy: ScrollViewProxy) -> some View {
        PanelCard(title: "Chain Summary") {
            VStack(alignment: .leading, spacing: 12) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(chain.name)
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(Theme.primaryText)
                        Text(chain.healthLabel)
                            .font(.subheadline)
                            .foregroundStyle(Theme.secondaryText)
                    }
                    Spacer()
                    StatusPill(status: chain.displayStatus)
                }

                ChainDiagramStrip(nodes: chain.diagramNodes)

                FlowChipRow(items: summaryChips)

                if let faultAt = chain.fault_at, !faultAt.isEmpty {
                    HStack(spacing: 10) {
                        Label("Fault at \(faultAt)", systemImage: "bolt.trianglebadge.exclamationmark")
                            .font(.subheadline)
                            .foregroundStyle(Theme.primaryText)
                        Spacer()
                        if let targetID = chain.faultNodeID {
                            Button("Jump to node") {
                                jumpTargetID = targetID
                                withAnimation(.easeInOut) {
                                    proxy.scrollTo(targetID, anchor: .center)
                                }
                            }
                            .font(.footnote.weight(.semibold))
                            .buttonStyle(.borderedProminent)
                            .tint(Theme.brandBlue)
                        }
                    }
                }

                Text(chain.headlineReason)
                    .font(.subheadline)
                    .foregroundStyle(Theme.primaryText)

                if let errorMessage, !errorMessage.isEmpty {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(Theme.faultRed)
                }
            }
        }
    }

    private var nodesCard: some View {
        PanelCard(title: "Nodes") {
            if chain.nodes.isEmpty {
                Text("No node data returned by the API.")
                    .foregroundStyle(Theme.secondaryText)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(chain.nodes) { node in
                        NodeTreeView(
                            node: node,
                            depth: 0,
                            baseURL: appModel.api.baseURL,
                            faultLabel: chain.fault_at,
                            maintenanceNodes: Set(chain.maintenance_nodes),
                            onPlayAudio: { url in
                                selectedAudio = AudioTarget(url: appModel.api.authorizedPlaybackURL(for: url))
                            }
                        )
                    }
                }
            }
        }
    }

    private var summaryChips: [MetricChipData] {
        var items: [MetricChipData] = [
            .init(icon: "clock", text: "Age \(chain.age_secs.formattedSeconds())"),
            .init(icon: "chart.line.uptrend.xyaxis", text: "SLA \(chain.sla_pct.formattedPercent())"),
            .init(icon: "point.3.connected.trianglepath.dotted", text: "\(chain.nodeCount) nodes")
        ]

        if chain.pending { items.append(.init(icon: "hourglass", text: "Pending")) }
        if chain.adbreak {
            let remaining = chain.adbreak_remaining.map { $0.formattedSeconds() } ?? "Live"
            items.append(.init(icon: "play.rectangle", text: "Adbreak \(remaining)"))
        }
        if chain.maintenance { items.append(.init(icon: "wrench.and.screwdriver", text: "Maintenance")) }
        if chain.flapping { items.append(.init(icon: "arrow.triangle.2.circlepath", text: "Flapping")) }
        return items
    }

    private func loadDetail() async {
        guard appModel.api.baseURL != nil else { return }
        isLoading = true
        defer { isLoading = false }

        do {
            chain = try await appModel.api.fetchChainDetail(id: chainID)
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

private struct NodeTreeView: View {
    let node: ChainNode
    let depth: Int
    let baseURL: URL?
    let faultLabel: String?
    let maintenanceNodes: Set<String>
    let onPlayAudio: (URL) -> Void

    private var isFaultTarget: Bool {
        guard let faultLabel else { return false }
        return node.label.caseInsensitiveCompare(faultLabel) == .orderedSame
    }

    private var isMaintenanceTarget: Bool {
        maintenanceNodes.contains(node.label) || node.isMaintenance
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Rectangle()
                    .fill(accentColor)
                    .frame(width: 4)
                    .clipShape(Capsule())

                VStack(alignment: .leading, spacing: 8) {
                    HStack(alignment: .top, spacing: 10) {
                        VStack(alignment: .leading, spacing: 4) {
                            HStack(spacing: 6) {
                                Image(systemName: node.isStack ? "square.stack.3d.up.fill" : "dot.radiowaves.left.and.right")
                                    .foregroundStyle(Theme.brandBlue)
                                Text(node.label)
                                    .font(.headline)
                                    .foregroundStyle(Theme.primaryText)
                            }

                            if let stream = node.stream, !stream.isEmpty {
                                Text(stream)
                                    .font(.subheadline)
                                    .foregroundStyle(Theme.secondaryText)
                            }
                        }

                        Spacer()
                        NodeStatusPill(status: node.status)
                    }

                    HStack(spacing: 8) {
                        SignalMeterView(level: node.level_dbfs, label: node.signalLabel)
                        if isFaultTarget {
                            MetricChip(icon: "exclamationmark.triangle", text: "Fault focus")
                        }
                        if isMaintenanceTarget {
                            MetricChip(icon: "wrench.and.screwdriver", text: "Maintenance")
                        }
                    }

                    FlowChipRow(items: node.chips)

                    if let reason = node.reason, !reason.isEmpty {
                        Text(reason)
                            .font(.footnote)
                            .foregroundStyle(Theme.secondaryText)
                    }

                    if let url = node.resolvedLiveURL(baseURL: baseURL) {
                        Button {
                            onPlayAudio(url)
                        } label: {
                            Label(node.isStack ? "Play stack audio" : "Play audio", systemImage: "play.circle")
                                .font(.footnote.weight(.medium))
                        }
                        .buttonStyle(.plain)
                        .tint(Theme.brandBlue)
                    }

                    if !node.childNodes.isEmpty {
                        VStack(alignment: .leading, spacing: 10) {
                            ForEach(node.childNodes) { child in
                                NodeTreeView(
                                    node: child,
                                    depth: depth + 1,
                                    baseURL: baseURL,
                                    faultLabel: faultLabel,
                                    maintenanceNodes: maintenanceNodes,
                                    onPlayAudio: onPlayAudio
                                )
                            }
                        }
                        .padding(.top, 2)
                    }
                }
            }
        }
        .id(node.id)
        .padding(.leading, CGFloat(depth) * 8)
        .padding(10)
        .background(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(backgroundColor)
        )
        .overlay(
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .stroke(borderColor, lineWidth: isFaultTarget ? 1.5 : 1)
        )
    }

    private var accentColor: Color {
        if isFaultTarget || node.isFaultLike { return Theme.faultRed }
        if isMaintenanceTarget { return Theme.mutedText }
        return depth == 0 ? Theme.brandBlue.opacity(0.8) : Theme.panelBorder
    }

    private var backgroundColor: Color {
        if isFaultTarget || node.isFaultLike { return Theme.faultRed.opacity(0.12) }
        if isMaintenanceTarget { return Theme.panelSecondary.opacity(0.12) }
        return Theme.panelSecondary.opacity(depth == 0 ? 0.35 : 0.2)
    }

    private var borderColor: Color {
        if isFaultTarget || node.isFaultLike { return Theme.faultRed.opacity(0.75) }
        if isMaintenanceTarget { return Theme.mutedText.opacity(0.6) }
        return Theme.panelBorder.opacity(0.55)
    }
}

private struct AudioTarget: Identifiable {
    let url: URL

    var id: String { url.absoluteString }
}

private struct AudioPlayerSheet: View {
    let target: AudioTarget
    @State private var player: AVPlayer?
    @State private var isPlaying = false
    @State private var statusText = "Preparing audio…"
    @State private var errorMessage: String?
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            VStack(spacing: 20) {
                ZStack {
                    RoundedRectangle(cornerRadius: 18, style: .continuous)
                        .fill(Theme.panelSecondary.opacity(0.65))
                        .frame(height: 150)
                    VStack(spacing: 12) {
                        Image(systemName: isPlaying ? "waveform.circle.fill" : "play.circle.fill")
                            .font(.system(size: 44))
                            .foregroundStyle(Theme.brandBlue)
                        Text(isPlaying ? "Live stream playing" : "Live stream ready")
                            .font(.headline)
                            .foregroundStyle(Theme.primaryText)
                        Text(statusText)
                            .font(.footnote)
                            .foregroundStyle(Theme.secondaryText)
                    }
                }

                HStack(spacing: 12) {
                    Button {
                        resumePlayback()
                    } label: {
                        Label(isPlaying ? "Pause" : "Play", systemImage: isPlaying ? "pause.fill" : "play.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(Theme.brandBlue)

                    Button {
                        stopPlayback()
                    } label: {
                        Label("Stop", systemImage: "stop.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.bordered)
                    .tint(Theme.secondaryText)
                }

                if let errorMessage {
                    Text(errorMessage)
                        .font(.footnote)
                        .foregroundStyle(Theme.faultRed)
                        .frame(maxWidth: .infinity, alignment: .leading)
                }

                Spacer()
            }
            .padding()
            .background(Theme.backgroundGradient.ignoresSafeArea())
            .navigationTitle("Live Audio")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Done") {
                        stopPlayback()
                        dismiss()
                    }
                }
            }
        }
        .task {
            await prepareAndPlay()
        }
        .onDisappear {
            stopPlayback()
        }
    }

    @MainActor
    private func prepareAndPlay() async {
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default, options: [])
            try session.setActive(true)

            let item = AVPlayerItem(url: target.url)
            let newPlayer = AVPlayer(playerItem: item)
            player = newPlayer
            statusText = "Connecting to hub relay…"
            errorMessage = nil
            newPlayer.play()
            isPlaying = true

            DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) {
                guard let currentItem = newPlayer.currentItem else { return }
                switch currentItem.status {
                case .readyToPlay:
                    statusText = "Streaming"
                case .failed:
                    statusText = "Playback failed"
                    errorMessage = currentItem.error?.localizedDescription ?? "Unknown playback error"
                    isPlaying = false
                case .unknown:
                    statusText = "Waiting for audio…"
                @unknown default:
                    statusText = "Waiting for audio…"
                }
            }
        } catch {
            statusText = "Audio session failed"
            errorMessage = error.localizedDescription
            isPlaying = false
        }
    }

    private func resumePlayback() {
        guard let player else { return }
        if isPlaying {
            player.pause()
            statusText = "Paused"
            isPlaying = false
        } else {
            player.play()
            statusText = "Streaming"
            isPlaying = true
        }
    }

    private func stopPlayback() {
        player?.pause()
        player = nil
        statusText = "Stopped"
        isPlaying = false
    }
}

private struct NodeStatusPill: View {
    let status: String

    private var color: Color {
        switch status.lowercased() {
        case "ok": return Theme.okGreen
        case "maintenance": return Theme.pendingAmber
        case "down", "offline": return Theme.faultRed
        default: return Theme.mutedText
        }
    }

    var body: some View {
        Text(status.uppercased())
            .font(.caption2.weight(.semibold))
            .foregroundStyle(.black)
            .padding(.horizontal, 8)
            .padding(.vertical, 4)
            .background(Capsule().fill(color))
    }
}

private struct MetricChipData: Hashable {
    let icon: String
    let text: String
}

private struct FlowChipRow: View {
    let items: [MetricChipData]

    var body: some View {
        HStack(spacing: 8) {
            ForEach(items, id: \.self) { item in
                MetricChip(icon: item.icon, text: item.text)
            }
            Spacer(minLength: 0)
        }
    }
}

private struct SignalMeterView: View {
    let level: Double?
    let label: String

    private var fillColor: Color {
        guard let level else { return Theme.mutedText }
        if level >= -12 { return Theme.pendingAmber }
        if level >= -30 { return Theme.okGreen }
        return Theme.faultRed
    }

    private var fraction: Double {
        guard let level else { return 0 }
        let clamped = min(max(level, -60), 0)
        return (clamped + 60) / 60
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(level?.formattedDbfs() ?? "No level")
                    .font(.caption.weight(.semibold))
                    .foregroundStyle(Theme.primaryText)
                Spacer(minLength: 0)
                Text(label)
                    .font(.caption2)
                    .foregroundStyle(Theme.secondaryText)
            }
            GeometryReader { proxy in
                ZStack(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 999)
                        .fill(Theme.panel.opacity(0.95))
                    RoundedRectangle(cornerRadius: 999)
                        .fill(fillColor)
                        .frame(width: max(proxy.size.width * fraction, fraction > 0 ? 10 : 0))
                }
            }
            .frame(height: 8)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Theme.panel.opacity(0.5))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(Theme.panelBorder.opacity(0.45), lineWidth: 1)
        )
    }
}

struct ChainDiagramStrip: View {
    let nodes: [ChainNode]

    var body: some View {
        ScrollView(.horizontal, showsIndicators: false) {
            HStack(spacing: 8) {
                ForEach(Array(nodes.enumerated()), id: \.element.id) { index, node in
                    DiagramNodeChip(node: node)
                    if index < nodes.count - 1 {
                        Image(systemName: "arrow.right")
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Theme.mutedText)
                    }
                }
            }
            .padding(.vertical, 2)
        }
    }
}

private struct DiagramNodeChip: View {
    let node: ChainNode

    private var color: Color {
        switch node.normalizedStatus {
        case "ok": return Theme.okGreen
        case "maintenance": return Theme.pendingAmber
        case "down", "offline": return Theme.faultRed
        default: return Theme.mutedText
        }
    }

    var body: some View {
        HStack(spacing: 6) {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
            Text(node.label)
                .font(.caption)
                .foregroundStyle(Theme.primaryText)
                .lineLimit(1)
        }
        .padding(.horizontal, 10)
        .padding(.vertical, 7)
        .background(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Theme.panelSecondary.opacity(0.45))
        )
        .overlay(
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .stroke(color.opacity(0.55), lineWidth: 1)
        )
    }
}

private extension ChainNode {
    var chips: [MetricChipData] {
        var items: [MetricChipData] = []
        if let site, !site.isEmpty { items.append(.init(icon: "building.2", text: site)) }
        if let machine, !machine.isEmpty { items.append(.init(icon: "macpro.gen3", text: machine)) }
        if let mode, !mode.isEmpty, isStack { items.append(.init(icon: "square.stack.3d.up", text: "Mode \(mode)")) }
        if isStack { items.append(.init(icon: "list.bullet.indent", text: "\(childNodes.count) child nodes")) }
        return items
    }
}
