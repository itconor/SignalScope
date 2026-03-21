import SwiftUI

struct FaultsView: View {
    @EnvironmentObject private var appModel: AppModel

    var body: some View {
        NavigationStack {
            ZStack {
                Theme.backgroundGradient.ignoresSafeArea()

                if appModel.displayedFaults.isEmpty, !appModel.isLoading {
                    emptyState
                } else {
                    ScrollView {
                        LazyVStack(spacing: 12) {
                            faultSummary

                            ForEach(appModel.displayedFaults) { chain in
                                NavigationLink {
                                    ChainDetailView(chainID: chain.id, initialChain: chain)
                                } label: {
                                    FaultRowView(chain: chain)
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .padding()
                    }
                    .refreshable {
                        await appModel.fetchChains()
                    }
                }
            }
            .navigationTitle("Active Faults")
            .toolbar {
                ToolbarItem(placement: .topBarTrailing) {
                    if appModel.isLoading {
                        ProgressView().tint(Theme.brandBlue)
                    }
                }
            }
        }
    }

    private var faultSummary: some View {
        PanelCard {
            HStack(spacing: 12) {
                VStack(alignment: .leading, spacing: 4) {
                    Text("\(appModel.displayedFaults.count) active fault\(appModel.displayedFaults.count == 1 ? "" : "s")")
                        .font(.headline)
                        .foregroundStyle(Theme.primaryText)
                    Text("Youngest issue first, tap any chain for node detail.")
                        .font(.footnote)
                        .foregroundStyle(Theme.secondaryText)
                }
                Spacer()
                Image(systemName: "bolt.trianglebadge.exclamationmark.fill")
                    .font(.system(size: 26))
                    .foregroundStyle(Theme.faultRed)
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 14) {
            Image(systemName: "checkmark.shield")
                .font(.system(size: 46))
                .foregroundStyle(Theme.okGreen)

            Text("No active faults")
                .font(.title3.weight(.semibold))
                .foregroundStyle(Theme.primaryText)

            Text(appModel.errorMessage ?? "Everything currently looks healthy.")
                .multilineTextAlignment(.center)
                .foregroundStyle(Theme.secondaryText)
                .padding(.horizontal)
        }
        .padding()
    }
}

private struct FaultRowView: View {
    let chain: ChainSummary

    var body: some View {
        PanelCard {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top) {
                    VStack(alignment: .leading, spacing: 4) {
                        Text(chain.name)
                            .font(.headline)
                            .foregroundStyle(Theme.primaryText)
                        if let faultAt = chain.fault_at, !faultAt.isEmpty {
                            Text("Fault at \(faultAt)")
                                .font(.subheadline)
                                .foregroundStyle(Theme.secondaryText)
                        }
                    }
                    Spacer()
                    StatusPill(status: chain.displayStatus)
                }

                HStack(spacing: 8) {
                    MetricChip(icon: "clock", text: chain.age_secs.formattedSeconds())
                    MetricChip(icon: "chart.line.uptrend.xyaxis", text: "SLA \(chain.sla_pct.formattedPercent())")
                    if chain.flapping {
                        MetricChip(icon: "arrow.triangle.2.circlepath", text: "Flapping")
                    }
                }

                Text(chain.headlineReason)
                    .font(.footnote)
                    .foregroundStyle(Theme.primaryText)
            }
        }
    }
}
